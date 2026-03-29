"""
main.py — Application entry point.

Initializes:
  1. Logging
  2. Database (creates tables, seeds admin user)
  3. Imou API client (restores cached token)
  4. APScheduler (token refresh + device cache refresh)
  5. Flask app
"""
import logging
import os
import sys
import time
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from werkzeug.security import generate_password_hash

# Determine log file path — use DATA_DIR env var or fall back to local data/
_LOG_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
os.makedirs(_LOG_DIR, exist_ok=True)
_LOG_FILE = os.path.join(_LOG_DIR, "imou_portal.log")

# Set up logging before importing anything else
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

import requests

import config
import database as db
from imou_api import ImouAPI
import api.app as flask_app_module

# Ensure alarm image cache directory exists
os.makedirs(config.ALARM_IMG_DIR, exist_ok=True)


def _dav_to_jpeg(dav_data: bytes, dest: str) -> bool:
    """
    Extract the first video frame from a DHAV .dav file as JPEG using ffmpeg.
    Returns True on success. ffmpeg must be installed.
    """
    import subprocess, tempfile
    with tempfile.NamedTemporaryFile(suffix=".dav", delete=False) as tf:
        tf.write(dav_data)
        tmp_dav = tf.name
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_dav, "-frames:v", "1", "-q:v", "2", dest],
            capture_output=True, timeout=20
        )
        if result.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 100:
            # Verify it's a JPEG
            with open(dest, "rb") as f:
                return f.read(2) == b"\xff\xd8"
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    finally:
        try:
            os.unlink(tmp_dav)
        except OSError:
            pass


def cache_alarm_snapshot(alarm_id: str, api_client, device_id: str, channel_id: str = "0", dav_url: str = "") -> str:
    """
    Save the event JPEG for an alarm and return its local path.
    Returns '/api/alarm-image/<alarm_id>' on success, or '' on failure.

    Strategy (in order):
    1. dav_url — download Imou's .dav (DHAV) thumbnail and extract the first frame with ffmpeg.
       This gives the EXACT event moment, same as the IMOU app.
    2. Live snapshot via setDeviceSnapEnhanced — captures the current camera frame.
       Only useful from the webhook path where the camera is still recording the event.
    """
    if not alarm_id:
        return ""
    dest = os.path.join(config.ALARM_IMG_DIR, f"{alarm_id}.jpg")
    if os.path.exists(dest):
        with open(dest, "rb") as f:
            header = f.read(2)
        if header == b"\xff\xd8":
            return f"/api/alarm-image/{alarm_id}"
        os.remove(dest)  # stale non-JPEG file

    # 1. Decode the DHAV thumbnail — exact event image, same as IMOU app
    if dav_url:
        try:
            r = requests.get(dav_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            if r.content[:4] == b"DHAV" or r.content[:4] == b"dhav":
                if _dav_to_jpeg(r.content, dest):
                    logger.debug("Alarm image decoded from .dav for %s", alarm_id)
                    return f"/api/alarm-image/{alarm_id}"
                else:
                    logger.debug("ffmpeg failed to decode .dav for %s", alarm_id)
            elif r.content[:2] == b"\xff\xd8":
                # Occasionally Imou serves a raw JPEG despite the .dav extension
                with open(dest, "wb") as f:
                    f.write(r.content)
                return f"/api/alarm-image/{alarm_id}"
        except Exception as e:
            logger.debug("dav_url download failed for %s: %s", alarm_id, e)

    # 2. Fall back: live snapshot (useful from webhook path — camera still recording the event)
    try:
        import time as _time
        result = api_client.get_snapshot(device_id, channel_id)
        snap_url = result.get("url", "")
        if not snap_url:
            return ""
        # Imou returns the CDN URL before the camera uploads the JPEG.
        # Try immediately first, then back off (0s, 2s, 5s, 10s).
        for delay in (0, 2, 5, 10):
            _time.sleep(delay)
            try:
                r = requests.get(snap_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
                r.raise_for_status()
                if r.content[:2] == b"\xff\xd8":
                    with open(dest, "wb") as f:
                        f.write(r.content)
                    logger.debug("Alarm image saved from live snapshot for %s", alarm_id)
                    return f"/api/alarm-image/{alarm_id}"
                logger.debug("Snapshot download for %s: not JPEG yet (delay=%ds)", alarm_id, delay)
            except Exception as dl_err:
                logger.debug("Snapshot download for %s failed (delay=%ds): %s", alarm_id, delay, dl_err)
        logger.warning("Snapshot for alarm %s never became a valid JPEG after retries", alarm_id)
        return ""
    except Exception as e:
        logger.warning("Failed to get alarm image %s (%s): %s", alarm_id, device_id, e)
        return ""


def seed_admin():
    """Create the default admin user if no users exist."""
    conn = db.get_connection()
    try:
        count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        if count == 0:
            db.create_user(
                username=config.ADMIN_USERNAME,
                password_hash=generate_password_hash(config.ADMIN_PASSWORD),
                is_admin=True,
            )
            logger.info("Created default admin user: %s", config.ADMIN_USERNAME)
    finally:
        conn.close()


ALARM_TYPE_MAP = {
    "1":   "Motion",
    "2":   "Motion",
    "3":   "Motion",
    "10":  "AlarmMotion",
    "11":  "AlarmLine",
    "12":  "AlarmRegion",
    "13":  "AlarmLine",
    "14":  "AlarmRegion",
    "110": "AlarmMotion",
    "111": "AlarmMotion",
    "120": "AlarmHumanDetection",
    "121": "AlarmHumanDetection",
    "122": "AlarmHumanDetection",
    "130": "AlarmFace",
    "140": "AlarmSound",
    "150": "AlarmSmoke",
    "160": "AlarmTamper",
}


def poll_alarms(api_client: ImouAPI):
    """
    Pull alarm history for all manually-registered devices every 60 seconds.
    Saves new alarms to DB and broadcasts them via SSE — works without a public webhook URL.
    """
    import database as _db
    from datetime import datetime, timezone, timedelta
    import pytz

    manual = _db.get_manual_devices()
    if not manual:
        return

    # Imou API compares beginTime/endTime in device-local time (Europe/Riga).
    # Lookback is 5 min (300s) — Imou API can take 1-3 min to publish a new alarm,
    # so 90s was too short and would miss freshly-triggered alarms. Dedup by alarm_id
    # ensures no duplicates even with the wider window.
    _lv = pytz.timezone("Europe/Riga")
    now = datetime.now(_lv)
    begin = (now - timedelta(seconds=300)).strftime("%Y-%m-%d %H:%M:%S")
    end   = now.strftime("%Y-%m-%d %H:%M:%S")

    new_count = 0
    for device in manual:
        device_id = device["device_id"]
        try:
            result = api_client._post_auth("getAlarmMessage", {
                "token":     api_client._token,
                "deviceId":  device_id,
                "channelId": "0",
                "beginTime": begin,
                "endTime":   end,
                "limit":     10,
            })
            alarms = result.get("alarms", [])
            for alarm in alarms:
                alarm_id   = str(alarm.get("alarmId", ""))
                event_type = ALARM_TYPE_MAP.get(str(alarm.get("type", "")), f"Alarm_{alarm.get('type')}")
                alarm_time = alarm.get("localDate") or str(alarm.get("time", ""))
                # thumbUrl / picurlArray are .dav (DHAV) files — ffmpeg decodes them to JPEG.
                # This gives the exact event frame, the same image the IMOU app shows.
                dav_url = alarm.get("thumbUrl") or (alarm.get("picurlArray") or [""])[0] or ""
                dev_name   = alarm.get("name") or device.get("name") or device_id

                # First save without image so we record the alarm immediately
                row_id, is_new = _db.save_notification(
                    device_id, dev_name, "0", event_type,
                    alarm_time, "", alarm, alarm_id=alarm_id
                )
                if is_new:
                    new_count += 1
                    # Broadcast immediately with no image; fetch event image in background
                    # so we don't block the 60s poll loop
                    flask_app_module.broadcast_event("notification", {
                        "id":          row_id,
                        "device_id":   device_id,
                        "device_name": dev_name,
                        "channel_id":  "0",
                        "event_type":  event_type,
                        "alarm_time":  alarm_time,
                        "image_url":   "",
                        "is_read":     False,
                        "created_at":  datetime.utcnow().isoformat() + "Z",
                    })
                    # Decode the .dav thumbnail to JPEG in a background thread
                    def _fetch_img(rid, aid, did, durl):
                        img = cache_alarm_snapshot(aid, api_client, did, "0", dav_url=durl)
                        if img:
                            _db.update_notification_image(rid, img)
                    threading.Thread(
                        target=_fetch_img, args=(row_id, alarm_id, device_id, dav_url), daemon=True
                    ).start()

        except Exception as e:
            logger.warning("Alarm poll failed for %s: %s", device_id, e)

    if new_count:
        logger.info("Alarm poll: %d new alerts found", new_count)


def refresh_devices(api_client: ImouAPI):
    """Background job: refresh device list cache every 5 minutes.
    Only broadcasts update if cloud devices were found (Device Access Service
    accounts return empty from the cloud API — their devices are in manual_devices
    and the frontend polls snapshots directly, so no broadcast needed)."""
    try:
        result = api_client.get_device_list(count=50)
        devices = result.get("deviceList", [])
        if devices:
            db.save_devices(devices)
            logger.debug("Device cache refreshed: %d cloud devices", len(devices))
            flask_app_module.broadcast_event("devices_updated", {"count": len(devices)})
        else:
            logger.debug("Device cache refresh: no cloud devices (Device Access Service mode)")
    except Exception as e:
        logger.warning("Device cache refresh failed: %s", e)


def setup_scheduler(api_client: ImouAPI) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")

    # Refresh Imou API token every 2.5 days (token expires in 3 days)
    scheduler.add_job(
        api_client.refresh_token,
        "interval",
        hours=60,  # 2.5 days
        id="token_refresh",
        next_run_time=None,  # don't run immediately; token loaded from DB cache
    )

    # Refresh device list cache every 5 minutes
    scheduler.add_job(
        refresh_devices,
        "interval",
        minutes=5,
        args=[api_client],
        id="device_refresh",
        next_run_time=None,
    )

    # Poll alarm history every 60 seconds for all manually-registered devices.
    # No next_run_time override — APScheduler will fire after the first 60s interval.
    scheduler.add_job(
        poll_alarms,
        "interval",
        seconds=60,
        args=[api_client],
        id="alarm_poll",
    )

    return scheduler


def create_app():
    # 1. Ensure data directory exists (from DB_PATH env or local data/)
    os.makedirs(_LOG_DIR, exist_ok=True)

    # 2. Initialize database
    db.init_db()
    seed_admin()

    # 3. Create Imou API client
    api_client = ImouAPI(
        app_id=config.IMOU_APP_ID,
        app_secret=config.IMOU_APP_SECRET,
        base_url=config.IMOU_BASE_URL,
    )

    # Try to get initial token if not cached or expired
    if not api_client.token_valid:
        if config.IMOU_APP_ID and config.IMOU_APP_SECRET:
            try:
                api_client.refresh_token()
                logger.info("Initial Imou token acquired")
            except Exception as e:
                logger.error("Failed to get initial Imou token: %s. Set IMOU_APP_ID and IMOU_APP_SECRET.", e)
        else:
            logger.warning("IMOU_APP_ID / IMOU_APP_SECRET not set — API calls will fail until configured")

    # 4. Inject API client into Flask module
    flask_app_module.imou = api_client

    # 5. Start scheduler
    scheduler = setup_scheduler(api_client)
    scheduler.start()

    # Delay first runs by a few seconds so Flask is fully up before API calls
    def delayed_startup():
        time.sleep(8)
        refresh_devices(api_client)
        # Also backfill recent alarms (last 24h) on first startup
        import database as _db
        from datetime import datetime, timedelta
        import pytz
        manual = _db.get_manual_devices()
        if manual:
            _lv = pytz.timezone("Europe/Riga")
            now = datetime.now(_lv)
            begin = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            end   = now.strftime("%Y-%m-%d %H:%M:%S")
            new_total = 0
            for device in manual:
                try:
                    result = api_client._post_auth("getAlarmMessage", {
                        "token": api_client._token, "deviceId": device["device_id"],
                        "channelId": "0", "beginTime": begin, "endTime": end, "limit": 50,
                    })
                    for alarm in result.get("alarms", []):
                        alarm_id   = str(alarm.get("alarmId", ""))
                        event_type = ALARM_TYPE_MAP.get(str(alarm.get("type", "")), f"Alarm_{alarm.get('type')}")
                        dav_url = alarm.get("thumbUrl") or (alarm.get("picurlArray") or [""])[0] or ""
                        _, is_new  = _db.save_notification(
                            device["device_id"], alarm.get("name") or device["name"],
                            "0", event_type,
                            alarm.get("localDate") or str(alarm.get("time", "")),
                            "",
                            alarm, alarm_id=alarm_id
                        )
                        if is_new:
                            new_total += 1
                            # Decode .dav thumbnail to JPEG in background
                            def _bf_img(aid, did, durl):
                                img = cache_alarm_snapshot(aid, api_client, did, "0", dav_url=durl)
                                if img:
                                    conn = _db.get_connection()
                                    try:
                                        row = conn.execute("SELECT id FROM notifications WHERE alarm_id=?", (aid,)).fetchone()
                                        if row:
                                            _db.update_notification_image(row["id"], img)
                                    finally:
                                        conn.close()
                            threading.Thread(target=_bf_img, args=(alarm_id, device["device_id"], dav_url), daemon=True).start()
                except Exception as e:
                    logger.warning("Startup alarm backfill failed for %s: %s", device["device_id"], e)
            logger.info("Startup alarm backfill: %d alerts loaded from last 24h", new_total)

        # Backfill images for notifications already in DB that have alarm_id but no image.
        # Re-query getAlarmMessage to get the .dav URL for ffmpeg decoding.
        missing = _db.get_notifications_missing_images(limit=60, max_age_hours=24)
        if missing:
            # Group by device_id and query alarm history to get dav_url for each
            import pytz as _pytz
            _lv2 = _pytz.timezone("Europe/Riga")
            _now2 = datetime.now(_lv2)
            _begin2 = (_now2 - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            _end2   = _now2.strftime("%Y-%m-%d %H:%M:%S")
            # Build alarm_id → dav_url map from cloud
            _dav_map = {}
            for _dev in {n["device_id"] for n in missing}:
                try:
                    _res = api_client._post_auth("getAlarmMessage", {
                        "deviceId": _dev, "channelId": "0",
                        "beginTime": _begin2, "endTime": _end2, "limit": 50,
                    })
                    for _a in _res.get("alarms", []):
                        _aid = str(_a.get("alarmId", ""))
                        _durl = _a.get("thumbUrl") or (_a.get("picurlArray") or [""])[0] or ""
                        if _aid and _durl:
                            _dav_map[_aid] = _durl
                except Exception:
                    pass
            fixed = 0
            for n in missing:
                try:
                    dav = _dav_map.get(str(n.get("alarm_id", "")), "")
                    img = cache_alarm_snapshot(str(n["alarm_id"]), api_client, n["device_id"], "0", dav_url=dav)
                    if img:
                        _db.update_notification_image(int(n["id"]), img)
                        fixed += 1
                except Exception as e:
                    logger.debug("Startup image backfill failed for notification %s: %s", n.get("id"), e)
            logger.info("Startup image backfill: %d/%d alerts updated", fixed, len(missing))

    threading.Thread(target=delayed_startup, daemon=True).start()

    logger.info("Imou Portal started on port %d", config.PORT)
    return flask_app_module.app


app = create_app()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=config.PORT,
        debug=config.DEBUG,
        threaded=True,  # needed for SSE concurrent connections
        use_reloader=False,  # avoid double-starting APScheduler
    )
