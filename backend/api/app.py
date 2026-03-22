"""
api/app.py — Flask application with all routes.

Endpoints:
  Auth:          POST /api/login, POST /api/logout, GET /api/me
  Devices:       GET /api/devices, GET /api/devices/<id>, GET /api/devices/<id>/snapshot
                 POST /api/devices/<id>/stream, POST /api/devices/<id>/ptz
                 POST /api/devices/<id>/restart, GET/POST /api/devices/<id>/motion
                 GET/POST /api/devices/<id>/nightvision, GET/POST /api/devices/<id>/privacy
  Notifications: GET /api/notifications, POST /api/notifications/read-all
                 POST /api/notifications/<id>/read, DELETE /api/notifications
  Webhook:       POST /api/webhook/imou
  SSE:           GET /api/sse
  Settings:      GET/POST /api/settings
  Admin:         GET/POST /api/admin/users, PUT/DELETE /api/admin/users/<id>
  Snapshot proxy: GET /api/proxy/image
"""
import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timedelta
from functools import wraps

import requests
from flask import (Flask, Response, g, jsonify, redirect, request,
                   send_from_directory, session, stream_with_context)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import check_password_hash, generate_password_hash

import database as db
from config import (ADMIN_PASSWORD, ADMIN_USERNAME, DEBUG, SECRET_KEY,
                    SESSION_LIFETIME_HOURS, WEBHOOK_SECRET)

logger = logging.getLogger(__name__)

# Locate frontend directory relative to this file; works both in Docker and dev
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
FRONTEND_DIR = os.path.normpath(os.path.join(_HERE, "..", "frontend"))

app = Flask(__name__, static_folder=None)
app.secret_key = SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=SESSION_LIFETIME_HOURS)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per minute"],
    storage_uri="memory://",
)

# Global reference to the ImouAPI instance (set by main.py)
imou: "ImouAPI" = None

# ─────────────────── SSE notification broadcast ──────────────────────────────
# Each connected SSE client gets a queue; new events are broadcast to all queues.
_sse_clients: dict[str, queue.Queue] = {}
_sse_lock = threading.Lock()


def broadcast_event(event_type: str, data: dict):
    """Push an event to all connected SSE clients."""
    payload = json.dumps({"type": event_type, "data": data})
    with _sse_lock:
        dead = []
        for client_id, q in _sse_clients.items():
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(client_id)
        for cid in dead:
            del _sse_clients[cid]


# ─────────────────── Auth decorators ─────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Unauthorized"}), 401
        g.current_user = db.get_user_by_id(session["user_id"])
        if not g.current_user:
            session.clear()
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Unauthorized"}), 401
        g.current_user = db.get_user_by_id(session["user_id"])
        if not g.current_user or not g.current_user.get("is_admin"):
            return jsonify({"error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated


def api_ok(data=None, **kwargs):
    payload = {"ok": True}
    if data is not None:
        payload["data"] = data
    payload.update(kwargs)
    return jsonify(payload)


def api_err(msg, status=400):
    return jsonify({"ok": False, "error": msg}), status


# ─────────────────── Static file serving ─────────────────────────────────────

@app.route("/")
def index():
    if "user_id" not in session:
        return redirect("/login")
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/login")
def login_page():
    return send_from_directory(FRONTEND_DIR, "login.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(FRONTEND_DIR, filename)


# ─────────────────── Auth routes ─────────────────────────────────────────────

@app.route("/api/login", methods=["POST"])
@limiter.limit("10 per minute")
def login():
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    user = db.get_user(username)
    if not user or not check_password_hash(user["password_hash"], password):
        logger.warning("Failed login attempt for user: %s from %s", username, request.remote_addr)
        return api_err("Invalid credentials", 401)

    session.permanent = True
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    logger.info("User %s logged in from %s", username, request.remote_addr)
    return api_ok({"username": user["username"], "is_admin": bool(user["is_admin"])})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return api_ok()


@app.route("/api/me")
@login_required
def me():
    u = g.current_user
    return api_ok({"username": u["username"], "is_admin": bool(u["is_admin"])})


# ─────────────────── Device routes ───────────────────────────────────────────

@app.route("/api/devices")
@login_required
def list_devices():
    """
    Returns device list. Tries standard cloud API first;
    if no devices returned (Device Access Service mode), uses manually-registered devices
    enriched with live status from the API.
    """
    try:
        result = imou.get_device_list(count=50)
        cloud_devices = result.get("deviceList", [])
    except Exception as e:
        logger.error("get_device_list failed: %s", e)
        cloud_devices = []

    if cloud_devices:
        # Standard account mode — devices from Imou cloud
        db.save_devices(cloud_devices)
        for d in cloud_devices:
            ability_str = d.get("ability", "")
            d["abilities"] = [a.strip() for a in ability_str.split(",") if a.strip()]
        return api_ok(cloud_devices)

    # Device Access Service mode — return manual devices immediately from DB.
    # Each CameraCard polls /snapshot independently so no per-device probing needed here.
    manual = db.get_manual_devices()
    devices = [
        {
            "deviceId":   m["device_id"],
            "name":       m["name"],
            "status":     "online",   # assume online; CameraCard shows "no signal" if snapshot fails
            "channelNum": m.get("channel_count", 1),
            "abilities":  ["Snap", "PT"],
            "_manual":    True,
        }
        for m in manual
    ]
    return api_ok(devices)


@app.route("/api/devices/<device_id>")
@login_required
def device_detail(device_id):
    try:
        result = imou.get_device_detail([device_id])
        devices = result.get("deviceList", [])
        if devices:
            d = devices[0]
            d["abilities"] = [a.strip() for a in d.get("ability", "").split(",") if a.strip()]
            return api_ok(d)
        return api_err("Device not found", 404)
    except Exception as e:
        logger.error("get_device_detail failed for %s: %s", device_id, e)
        cached = db.get_device(device_id)
        if cached:
            return api_ok(cached, cached=True)
        return api_err(str(e))


# Snapshot rate-limit state.
# Imou free tier: 30,000 API calls/month → ~1,000/day budget.
# OP1013 = monthly quota exhausted (not per-second limit).
# We serialize all snapshot requests through a single lock with a minimum
# 1.1-second gap between calls so concurrent camera cards never pile up.
# At the default 15-min interval: 5 cameras × 96 calls/day = 480 calls/day ✓
_snapshot_lock = threading.Lock()
_snapshot_last_call = 0.0        # epoch seconds of last successful API call
_SNAPSHOT_MIN_GAP = 1.1          # seconds between Imou snapshot calls
_snapshot_blocked_until = 0      # epoch seconds; hard backoff after OP1013


@app.route("/api/devices/<device_id>/snapshot")
@login_required
def device_snapshot(device_id):
    global _snapshot_blocked_until, _snapshot_last_call
    channel_id = request.args.get("channel", "0")

    # Hard backoff: OP1013 was hit recently — tell the client to wait
    if time.time() < _snapshot_blocked_until:
        wait = int(_snapshot_blocked_until - time.time())
        return api_err(f"rate_limited:{wait}", 429)

    # Serialize: only one snapshot call to Imou at a time, with minimum gap
    with _snapshot_lock:
        # Re-check backoff after acquiring the lock (another thread may have set it)
        if time.time() < _snapshot_blocked_until:
            wait = int(_snapshot_blocked_until - time.time())
            return api_err(f"rate_limited:{wait}", 429)

        # Enforce minimum gap between consecutive Imou calls
        gap = _SNAPSHOT_MIN_GAP - (time.time() - _snapshot_last_call)
        if gap > 0:
            time.sleep(gap)

        try:
            result = imou.get_snapshot(device_id, channel_id)
            _snapshot_last_call = time.time()
            return api_ok(result)
        except Exception as e:
            err = str(e)
            logger.error("snapshot failed for %s: %s", device_id, err)
            if "OP1013" in err:
                # Monthly quota exhausted (30k/month free tier). Block for 24h.
                # Monitor usage at: open.imoulife.com/consoleNew/resourceManage/myResource
                _snapshot_blocked_until = time.time() + 86400
                logger.warning("OP1013 monthly quota hit — snapshots blocked for 24h. Check usage at open.imoulife.com/consoleNew/resourceManage/myResource")
                return api_err("rate_limited:86400", 429)
            return api_err(err)


@app.route("/api/devices/<device_id>/stream", methods=["POST"])
@login_required
def device_stream(device_id):
    body = request.get_json() or {}
    channel_id = body.get("channel", "0")
    stream_id = int(body.get("streamId", 0))  # 0=main, 1=sub
    try:
        # Bind stream first, then get URLs
        bind_result = imou.bind_live_stream(device_id, channel_id, stream_id)
        stream_info = imou.get_live_stream_info(device_id, channel_id)
        return api_ok({**bind_result, **stream_info})
    except Exception as e:
        logger.error("stream failed for %s: %s", device_id, e)
        return api_err(str(e))


@app.route("/api/devices/<device_id>/stream/unbind", methods=["POST"])
@login_required
def unbind_stream(device_id):
    body = request.get_json() or {}
    channel_id = body.get("channel", "0")
    try:
        imou.unbind_live_stream(device_id, channel_id)
        return api_ok()
    except Exception as e:
        logger.error("unbind stream failed: %s", e)
        return api_err(str(e))


@app.route("/api/devices/<device_id>/ptz", methods=["POST"])
@login_required
def device_ptz(device_id):
    body = request.get_json() or {}
    channel_id = body.get("channel", "0")
    operation = str(body.get("operation", "0"))
    duration = int(body.get("duration", 1000))
    try:
        result = imou.ptz_control(device_id, channel_id, operation, duration)
        return api_ok(result)
    except Exception as e:
        logger.error("PTZ control failed for %s: %s", device_id, e)
        return api_err(str(e))


@app.route("/api/devices/<device_id>/ptz/position")
@login_required
def device_ptz_position(device_id):
    channel_id = request.args.get("channel", "0")
    try:
        result = imou.get_ptz_position(device_id, channel_id)
        return api_ok(result)
    except Exception as e:
        return api_err(str(e))


@app.route("/api/devices/<device_id>/restart", methods=["POST"])
@login_required
def device_restart(device_id):
    try:
        imou.restart_device(device_id)
        return api_ok()
    except Exception as e:
        logger.error("restart failed for %s: %s", device_id, e)
        return api_err(str(e))


@app.route("/api/devices/<device_id>/motion", methods=["GET", "POST"])
@login_required
def device_motion(device_id):
    channel_id = request.args.get("channel", "0")
    if request.method == "GET":
        try:
            return api_ok(imou.get_motion_detect(device_id, channel_id))
        except Exception as e:
            return api_err(str(e))
    else:
        body = request.get_json() or {}
        try:
            result = imou.set_motion_detect(
                device_id, channel_id,
                enabled=body.get("enabled", True),
                sensitivity=int(body.get("sensitivity", 6))
            )
            return api_ok(result)
        except Exception as e:
            return api_err(str(e))


@app.route("/api/devices/<device_id>/nightvision", methods=["GET", "POST"])
@login_required
def device_nightvision(device_id):
    channel_id = request.args.get("channel", "0")
    if request.method == "GET":
        try:
            return api_ok(imou.get_night_vision(device_id, channel_id))
        except Exception as e:
            return api_err(str(e))
    else:
        body = request.get_json() or {}
        try:
            result = imou.set_night_vision(device_id, channel_id, mode=int(body.get("mode", 2)))
            return api_ok(result)
        except Exception as e:
            return api_err(str(e))


@app.route("/api/devices/<device_id>/privacy", methods=["GET", "POST"])
@login_required
def device_privacy(device_id):
    channel_id = request.args.get("channel", "0")
    if request.method == "GET":
        try:
            return api_ok(imou.get_privacy_mask(device_id, channel_id))
        except Exception as e:
            return api_err(str(e))
    else:
        body = request.get_json() or {}
        try:
            result = imou.set_privacy_mask(device_id, channel_id, enabled=bool(body.get("enabled", False)))
            return api_ok(result)
        except Exception as e:
            return api_err(str(e))


@app.route("/api/devices/<device_id>/storage")
@login_required
def device_storage(device_id):
    channel_id = request.args.get("channel", "0")
    try:
        return api_ok(imou.get_storage_info(device_id, channel_id))
    except Exception as e:
        return api_err(str(e))


@app.route("/api/devices/<device_id>/alarm-history")
@login_required
def device_alarm_history(device_id):
    channel_id = request.args.get("channel", "0")
    begin = request.args.get("begin", "")
    end = request.args.get("end", "")
    alarm_type = request.args.get("type", "")
    limit = int(request.args.get("limit", 20))
    try:
        return api_ok(imou.get_alarm_list(device_id, channel_id, begin, end, alarm_type, limit))
    except Exception as e:
        return api_err(str(e))


# ─────────────────── Manual device management (Device Access Service) ────────

@app.route("/api/devices/manual", methods=["GET"])
@login_required
def list_manual_devices():
    return api_ok(db.get_manual_devices())


@app.route("/api/devices/manual", methods=["POST"])
@login_required
def add_manual_device():
    """Add or update a manually-registered device by serial number."""
    body = request.get_json() or {}
    device_id = body.get("device_id", "").strip().upper()
    name = body.get("name", "").strip() or device_id
    channel_count = int(body.get("channel_count", 1))

    if not device_id:
        return api_err("device_id (serial number) is required")

    db.upsert_manual_device(device_id, name, channel_count)
    # Also update the device cache so it appears in the main device list
    db.save_devices([{
        "deviceId": device_id, "name": name, "status": "unknown",
        "channelNum": channel_count, "abilities": ["Snap"], "_manual": True,
    }])
    return api_ok({"device_id": device_id, "name": name})


@app.route("/api/devices/manual/<device_id>", methods=["DELETE"])
@login_required
def delete_manual_device(device_id):
    db.delete_manual_device(device_id.upper())
    return api_ok()


@app.route("/api/devices/manual/bulk", methods=["POST"])
@login_required
def bulk_add_manual_devices():
    """
    Import multiple devices at once.
    Body: { "devices": [{"device_id": "SN1", "name": "Camera 1"}, ...] }
    """
    body = request.get_json() or {}
    devices = body.get("devices", [])
    added = []
    for d in devices:
        did = d.get("device_id", "").strip().upper()
        if did:
            name = d.get("name", did)
            db.upsert_manual_device(did, name, int(d.get("channel_count", 1)))
            added.append(did)
    return api_ok({"added": added, "count": len(added)})


# ─────────────────── Notification routes ─────────────────────────────────────

@app.route("/api/notifications")
@login_required
def get_notifications():
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    unread_only = request.args.get("unread") == "1"
    device_id = request.args.get("device_id")
    notes = db.get_notifications(limit, offset, unread_only, device_id)
    unread = db.get_unread_count()
    return api_ok({"notifications": notes, "unread_count": unread})


@app.route("/api/notifications/<int:notif_id>/read", methods=["POST"])
@login_required
def mark_notification_read(notif_id):
    db.mark_notifications_read([notif_id])
    return api_ok({"unread_count": db.get_unread_count()})


@app.route("/api/notifications/read-all", methods=["POST"])
@login_required
def mark_all_read():
    db.mark_notifications_read()
    return api_ok({"unread_count": 0})


@app.route("/api/notifications", methods=["DELETE"])
@login_required
def clear_notifications():
    days = int(request.args.get("older_than_days", 30))
    db.delete_notifications(days)
    return api_ok()


# ─────────────────── Webhook endpoint ────────────────────────────────────────

@app.route("/api/webhook/imou", methods=["GET", "POST"])
def imou_webhook():
    """
    Receives push notifications from Imou cloud.
    Imou performs a GET verification call first, then POSTs events.
    """
    if request.method == "GET":
        # Verification challenge — Imou may send a challenge parameter
        challenge = request.args.get("echostr", request.args.get("challenge", "ok"))
        return challenge, 200

    # POST — actual alarm event
    try:
        # Imou sometimes sends malformed HTTP; be lenient with parsing
        data = request.get_json(force=True, silent=True) or {}
        logger.info("Webhook received: %s", json.dumps(data)[:500])

        # Normalize fields across different Imou firmware versions
        device_id = (data.get("deviceId") or data.get("device_id") or "").strip()
        device_name = data.get("deviceName") or data.get("device_name") or device_id
        channel_id = str(data.get("channelId") or data.get("channel_id") or "0")
        event_type = (data.get("alarmType") or data.get("alarm_type") or
                      data.get("msgType") or "Unknown")
        alarm_time = data.get("alarmTime") or data.get("alarm_time") or datetime.utcnow().isoformat()

        # Image URL may be a string or a list
        image_url = ""
        raw_urls = data.get("imageUrls") or data.get("image_url") or data.get("imageUrl") or []
        if isinstance(raw_urls, list) and raw_urls:
            image_url = raw_urls[0]
        elif isinstance(raw_urls, str):
            image_url = raw_urls

        if not device_id:
            return "ok", 200

        notif_id = db.save_notification(
            device_id, device_name, channel_id, event_type, alarm_time, image_url, data
        )

        notification = {
            "id": notif_id,
            "device_id": device_id,
            "device_name": device_name,
            "channel_id": channel_id,
            "event_type": event_type,
            "alarm_time": alarm_time,
            "image_url": image_url,
            "is_read": False,
            "created_at": datetime.utcnow().isoformat(),
        }
        broadcast_event("notification", notification)
        logger.info("Notification saved: %s from %s (%s)", event_type, device_name, device_id)

    except Exception as e:
        logger.error("Webhook processing error: %s", e)

    return "ok", 200


# ─────────────────── Server-Sent Events ──────────────────────────────────────

@app.route("/api/sse")
@login_required
def sse():
    """
    Server-Sent Events endpoint.
    Clients connect here and receive real-time notifications pushed by the server.
    The browser's EventSource API handles reconnection automatically.
    """
    client_id = f"{session['user_id']}-{time.time()}"
    q = queue.Queue(maxsize=100)

    with _sse_lock:
        _sse_clients[client_id] = q

    def generate():
        try:
            # Send initial connection confirmation
            yield f"data: {json.dumps({'type': 'connected', 'data': {}})}\n\n"
            while True:
                try:
                    # Wait up to 25 seconds; send heartbeat to keep connection alive
                    payload = q.get(timeout=25)
                    yield f"data: {payload}\n\n"
                except queue.Empty:
                    # Heartbeat prevents proxy/browser from closing idle connection
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                _sse_clients.pop(client_id, None)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering for SSE
        },
    )


# ─────────────────── Image proxy ─────────────────────────────────────────────

@app.route("/api/proxy/image")
@login_required
def proxy_image():
    """
    Proxy an image URL through the server to avoid CORS issues with Imou image URLs.
    Only allows proxying HTTPS image URLs to prevent SSRF.
    """
    url = request.args.get("url", "")
    if not url.startswith("https://"):
        return api_err("Only HTTPS URLs allowed", 400)
    try:
        r = requests.get(url, timeout=10, stream=True)
        r.raise_for_status()
        content_type = r.headers.get("Content-Type", "image/jpeg")
        return Response(r.content, mimetype=content_type, headers={"Cache-Control": "max-age=300"})
    except Exception as e:
        return api_err(str(e))


# ─────────────────── Alarm image cache ───────────────────────────────────────

@app.route("/api/alarm-image/<alarm_id>")
def serve_alarm_image(alarm_id):
    """Serve a locally-cached alarm thumbnail."""
    import config as _cfg
    # Sanitise — only allow alphanumeric + dash/underscore
    safe = "".join(c for c in alarm_id if c.isalnum() or c in "-_")
    path = os.path.join(_cfg.ALARM_IMG_DIR, f"{safe}.jpg")
    if not os.path.exists(path):
        return api_err("Not found", 404)
    return send_from_directory(_cfg.ALARM_IMG_DIR, f"{safe}.jpg",
                               mimetype="image/jpeg",
                               max_age=86400)


# ─────────────────── Settings routes ─────────────────────────────────────────

@app.route("/api/settings", methods=["GET"])
@login_required
def get_settings():
    settings = {
        "webhook_url": db.get_setting("webhook_url", ""),
        "snapshot_interval": db.get_setting("snapshot_interval", "900"),
        "notification_sound": db.get_setting("notification_sound", "1"),
        "imou_region": db.get_setting("imou_region", "default"),
    }
    return api_ok(settings)


@app.route("/api/settings", methods=["POST"])
@login_required
def update_settings():
    body = request.get_json() or {}
    allowed_keys = {"webhook_url", "snapshot_interval", "notification_sound", "imou_region"}
    for key, value in body.items():
        if key in allowed_keys:
            db.set_setting(key, str(value))

    # If webhook URL changed, register it with Imou API
    if "webhook_url" in body and body["webhook_url"]:
        try:
            imou.set_callback_url(body["webhook_url"])
        except Exception as e:
            logger.warning("Failed to register webhook URL with Imou: %s", e)

    return api_ok()


@app.route("/api/settings/webhook")
@login_required
def get_webhook_config():
    """Get the webhook URL currently registered with Imou."""
    try:
        result = imou.get_callback_url()
        return api_ok(result)
    except Exception as e:
        return api_err(str(e))


# ─────────────────── Admin routes ────────────────────────────────────────────

@app.route("/api/admin/users", methods=["GET"])
@admin_required
def admin_list_users():
    return api_ok(db.get_all_users())


@app.route("/api/admin/users", methods=["POST"])
@admin_required
def admin_create_user():
    body = request.get_json() or {}
    username = body.get("username", "").strip()
    password = body.get("password", "")
    is_admin = bool(body.get("is_admin", False))

    if not username or not password:
        return api_err("Username and password required")
    if db.get_user(username):
        return api_err("Username already exists")

    user_id = db.create_user(username, generate_password_hash(password), is_admin)
    return api_ok({"id": user_id, "username": username, "is_admin": is_admin})


@app.route("/api/admin/users/<int:user_id>", methods=["PUT"])
@admin_required
def admin_update_user(user_id):
    body = request.get_json() or {}
    if "password" in body and body["password"]:
        db.update_user_password(user_id, generate_password_hash(body["password"]))
    return api_ok()


@app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@admin_required
def admin_delete_user(user_id):
    if user_id == session.get("user_id"):
        return api_err("Cannot delete yourself")
    db.delete_user(user_id)
    return api_ok()


@app.route("/api/change-password", methods=["POST"])
@login_required
def change_password():
    """Allow any logged-in user to change their own password."""
    body = request.get_json() or {}
    current = body.get("current_password", "")
    new_pw  = body.get("new_password", "")

    if not current or not new_pw:
        return api_err("All fields are required")
    if len(new_pw) < 8:
        return api_err("New password must be at least 8 characters")

    user = db.get_user_by_id(session["user_id"])
    if not check_password_hash(user["password_hash"], current):
        return api_err("Current password is incorrect")

    db.update_user_password(session["user_id"], generate_password_hash(new_pw))
    logger.info("User %s changed their password", user["username"])
    return api_ok()


# ─────────────────── Token status ────────────────────────────────────────────

@app.route("/api/token-status")
@login_required
def token_status():
    valid = imou.token_valid if imou else False
    expires_in = max(0, int(imou._token_expires - time.time())) if imou else 0
    return api_ok({"valid": valid, "expires_in_seconds": expires_in})
