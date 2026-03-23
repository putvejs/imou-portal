"""
imou_api.py — Imou Open Platform API client.

Handles authentication (MD5-based signature), token caching,
and all supported API operations for camera management.

Docs: https://open.imoulife.com/book/http/develop.html
"""
import hashlib
import random
import string
import time
import logging
import requests
from database import save_token, load_token

logger = logging.getLogger(__name__)


class ImouAPIError(Exception):
    """Raised when the Imou API returns a non-zero result code."""
    def __init__(self, code, msg):
        self.code = code
        self.msg = msg
        super().__init__(f"Imou API error [{code}]: {msg}")


class ImouAPI:
    """
    Client for the Imou Open Platform HTTP API.

    Usage:
        api = ImouAPI(app_id="xxx", app_secret="yyy")
        devices = api.get_device_list()
    """

    def __init__(self, app_id: str, app_secret: str, base_url: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = base_url.rstrip("/")

        # Load cached token from DB so we survive restarts
        token, expires_at = load_token()
        self._token = token
        self._token_expires = expires_at

    # ────────────────────────── Authentication ──────────────────────────────

    @staticmethod
    def _nonce() -> str:
        """Generate a random 32-character alphanumeric string."""
        return "".join(random.choices(string.ascii_letters + string.digits, k=32))

    def _sign(self, timestamp: str, nonce: str) -> str:
        """
        Compute MD5 signature required by Imou API.
        Format: MD5("time:{ts},nonce:{nonce},appSecret:{secret}")
        """
        content = f"time:{timestamp},nonce:{nonce},appSecret:{self.app_secret}"
        return hashlib.md5(content.encode()).hexdigest()

    def _post(self, method: str, params: dict = None) -> dict:
        """
        Send a signed POST request to the Imou API.
        Returns the 'data' field of a successful response.
        Raises ImouAPIError on API-level failures.

        Imou API uses a nested 'system' block for auth fields:
        { "system": { "ver", "appId", "sign", "time", "nonce" }, "params": {...} }
        """
        ts = str(int(time.time()))
        nonce = self._nonce()
        sign = self._sign(ts, nonce)

        payload = {
            "system": {
                "ver": "1.0",
                "appId": self.app_id,
                "sign": sign,
                "time": int(ts),
                "nonce": nonce,
            },
            "params": params or {},
        }

        url = f"{self.base_url}/{method}"
        logger.debug("POST %s params=%s", url, list((params or {}).keys()))

        try:
            resp = requests.post(url, json=payload, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise ImouAPIError("NET", str(e))

        body = resp.json()
        result = body.get("result", {})
        code = str(result.get("code", "-1"))

        if code != "0":
            raise ImouAPIError(code, result.get("msg", "unknown error"))

        return result.get("data", {})

    def _post_auth(self, method: str, params: dict = None) -> dict:
        """Like _post but automatically injects the current access token."""
        self._ensure_token()
        p = dict(params or {})
        p["token"] = self._token
        return self._post(method, p)

    def _ensure_token(self):
        """Refresh access token if missing or expiring within 5 minutes."""
        if not self._token or time.time() >= self._token_expires - 300:
            self.refresh_token()

    def refresh_token(self):
        """
        Fetch a new access token from Imou and cache it in the database.
        Token lifetime is returned by the API (usually 3 days).
        Also updates base_url to the domain Imou instructs us to use (currentDomain).
        """
        logger.info("Refreshing Imou access token")
        data = self._post("accessToken")
        token = data["accessToken"]
        expires_at = int(time.time()) + int(data.get("expireTime", 259200))
        self._token = token
        self._token_expires = expires_at
        save_token(token, expires_at)

        # Imou returns the correct regional domain — use it for all subsequent calls
        if data.get("currentDomain"):
            domain = data["currentDomain"].rstrip("/")
            self.base_url = domain + "/openapi" if not domain.endswith("/openapi") else domain
            logger.info("Switched to regional domain: %s", self.base_url)

        logger.info("Access token refreshed, expires in %d seconds", int(data.get("expireTime", 259200)))
        return token

    @property
    def token_valid(self) -> bool:
        return bool(self._token) and time.time() < self._token_expires - 300

    # ────────────────────────── Device Discovery ────────────────────────────

    def get_device_list(self, page: int = 0, count: int = 20) -> dict:
        """
        Get device list. Tries standard account-bound API first; if that fails
        (common with Device Access Service accounts), returns empty so the caller
        can fall back to manually-registered devices.
        Returns dict with keys: deviceList, count
        """
        try:
            return self._post_auth("deviceBaseList", {
                "bindId": 0,
                "limit": count,
                "offset": page,
                "type": "bindAndShare",
            })
        except Exception:
            # Device Access Service accounts don't support deviceBaseList —
            # return empty so the Flask layer uses the manual device table.
            return {"deviceList": [], "count": 0}

    def get_device_status(self, device_id: str) -> str:
        """
        Check if a Device Access Service device is online.
        Uses deviceOnlineStatus (free, doesn't count against snapshot quota).
        Returns 'online' or 'offline'.
        """
        try:
            result = self.get_device_online_status([device_id])
            device_list = result.get("deviceList", [])
            if device_list:
                return "online" if device_list[0].get("onLine") == 1 else "offline"
            return "offline"
        except Exception:
            return "offline"

    def get_device_detail(self, device_ids: list) -> dict:
        """
        Get detailed info including capabilities for specific devices.
        device_ids: list of device ID strings (max 20 per call)
        """
        return self._post_auth("deviceBaseDetailList", {
            "deviceList": [{"deviceId": d} for d in device_ids]
        })

    def get_device_online_status(self, device_ids: list) -> dict:
        """Check online/offline status for multiple devices."""
        return self._post_auth("deviceOnlineStatus", {
            "deviceList": [{"deviceId": d} for d in device_ids]
        })

    def get_storage_info(self, device_id: str, channel_id: str = "0") -> dict:
        """Get SD card / cloud storage status for a device."""
        return self._post_auth("deviceSdcardStatus", {
            "deviceId": device_id,
            "channelId": channel_id,
        })

    def get_device_power_info(self, device_id: str) -> dict:
        """Get battery level for battery-powered cameras."""
        return self._post_auth("devicePowerInfo", {
            "deviceId": device_id,
        })

    # ────────────────────────── Live Streaming ──────────────────────────────

    def bind_live_stream(self, device_id: str, channel_id: str = "0", stream_id: int = 0) -> dict:
        """
        Bind a live stream for a device.
        For Device Access Service accounts, bindDeviceLive returns the stream URLs
        directly in the 'streams' array (no need to call getLiveStreamInfo after).
        stream_id: 0 = main stream (HD), 1 = sub stream (SD)
        Returns normalised dict: {hls, liveToken, streams, ...}
        """
        try:
            result = self._post_auth("bindDeviceLive", {
                "deviceId": device_id,
                "channelId": channel_id,
                "streamId": stream_id,
            })
        except ImouAPIError as e:
            if e.code == "LV1001":
                # Stream already bound — fetch existing session instead of erroring
                logger.info("LV1001 for %s — reusing existing live session", device_id)
                result = self.get_live_stream_info(device_id, channel_id)
            elif e.code == "OP1026":
                # "Requests too frequent" — wait 3 seconds and retry once
                logger.info("OP1026 for %s stream bind — waiting 3s then retrying", device_id)
                time.sleep(3)
                result = self._post_auth("bindDeviceLive", {
                    "deviceId": device_id,
                    "channelId": channel_id,
                    "streamId": stream_id,
                })
            else:
                raise

        # Normalise: extract HLS from streams array if present (Device Access Service format)
        streams = result.get("streams", [])
        if streams and isinstance(streams, list):
            # Find streams matching requested stream_id, prefer HTTPS
            matching = [s for s in streams if s.get("streamId") == stream_id and "proto=https" in s.get("hls", "")]
            if not matching:
                matching = [s for s in streams if s.get("streamId") == stream_id]
            if not matching:
                matching = streams
            s = matching[0]
            alt = matching[1] if len(matching) > 1 else matching[0]
            result["hls"]    = result.get("hls") or s.get("hls", "")
            result["subHls"] = result.get("subHls") or alt.get("hls", "")
            result["flv"]    = result.get("flv") or s.get("flv", "")
            result["rtmp"]   = result.get("rtmp") or s.get("rtmp", "")
        return result

    def get_live_stream_info(self, device_id: str, channel_id: str = "0") -> dict:
        """
        Get current live stream URLs for a device.
        Returns URLs for HLS, RTMP, FLV formats.
        """
        return self._post_auth("getLiveStreamInfo", {
            "deviceId": device_id,
            "channelId": channel_id,
        })

    def unbind_live_stream(self, device_id: str, channel_id: str = "0") -> dict:
        """Release a live stream binding to free server resources."""
        return self._post_auth("unbindDeviceLive", {
            "deviceId": device_id,
            "channelId": channel_id,
        })

    # ────────────────────────── Snapshots ───────────────────────────────────

    def get_snapshot(self, device_id: str, channel_id: str = "0") -> dict:
        """
        Capture a snapshot from the camera.
        Returns dict with 'url' pointing to the JPEG image (valid 7 days).
        Uses enhanced endpoint (1 per second); falls back to legacy if needed.
        """
        try:
            return self._post_auth("setDeviceSnapEnhanced", {
                "deviceId": device_id,
                "channelId": channel_id,
            })
        except ImouAPIError as e:
            # Legacy devices may not support enhanced — fall back
            logger.debug("Enhanced snap failed (%s), trying legacy", e.code)
            return self._post_auth("setDeviceSnap", {
                "deviceId": device_id,
                "channelId": channel_id,
            })

    # ────────────────────────── PTZ Control ─────────────────────────────────

    def ptz_control(self, device_id: str, channel_id: str = "0",
                    operation: str = "0", duration: int = 1000) -> dict:
        """
        Send a PTZ movement command.

        operation codes:
          "0" = stop
          "1" = up        "2" = down
          "3" = left      "4" = right
          "5" = upper-left  "6" = upper-right
          "7" = lower-left  "8" = lower-right
          "9" = zoom in   "10" = zoom out
          "11" = focus near  "12" = focus far

        duration: milliseconds to move (default 1000ms)
        """
        return self._post_auth("controlMovePTZ", {
            "deviceId": device_id,
            "channelId": channel_id,
            "operation": operation,
            "duration": duration,
        })

    def get_ptz_position(self, device_id: str, channel_id: str = "0") -> dict:
        """Get current PTZ position (pan, tilt, zoom values)."""
        return self._post_auth("getDevicePTZInfo", {
            "deviceId": device_id,
            "channelId": channel_id,
        })

    def set_ptz_preset(self, device_id: str, channel_id: str = "0",
                       preset_id: int = 1) -> dict:
        """Move camera to a saved PTZ preset position."""
        return self._post_auth("controlLocationPTZ", {
            "deviceId": device_id,
            "channelId": channel_id,
            "token": preset_id,
            "enable": 1,
        })

    # ────────────────────────── Motion / Alarm Config ───────────────────────

    def get_motion_detect(self, device_id: str, channel_id: str = "0") -> dict:
        """Get motion detection configuration."""
        return self._post_auth("getDeviceMotionDetect", {
            "deviceId": device_id,
            "channelId": channel_id,
        })

    def set_motion_detect(self, device_id: str, channel_id: str = "0",
                          enabled: bool = True, sensitivity: int = 6) -> dict:
        """
        Enable/disable motion detection.
        sensitivity: 1 (low) to 8 (high)
        """
        return self._post_auth("setDeviceMotionDetect", {
            "deviceId": device_id,
            "channelId": channel_id,
            "enable": 1 if enabled else 0,
            "sensitivity": sensitivity,
        })

    def get_alarm_region(self, device_id: str, channel_id: str = "0") -> dict:
        """Get configured alarm/detection regions."""
        return self._post_auth("getDeviceAlarmRegion", {
            "deviceId": device_id,
            "channelId": channel_id,
        })

    def set_alarm_region(self, device_id: str, channel_id: str = "0",
                         regions: list = None) -> dict:
        """
        Set motion detection regions.
        regions: list of region dicts with coordinates.
        Pass empty list to clear all regions.
        """
        return self._post_auth("setDeviceAlarmRegion", {
            "deviceId": device_id,
            "channelId": channel_id,
            "regionList": regions or [],
        })

    def get_push_alarm_config(self, device_id: str) -> dict:
        """Get push notification alarm type configuration."""
        return self._post_auth("getDeviceAlarmList", {
            "deviceId": device_id,
        })

    def set_push_alarm_config(self, device_id: str, alarm_list: list) -> dict:
        """
        Configure which alarm types trigger push notifications.
        alarm_list: list of alarm type strings, e.g. ["AlarmHumanDetection", "AlarmMotion"]
        """
        return self._post_auth("setDevicePushAlarmType", {
            "deviceId": device_id,
            "alarmList": alarm_list,
        })

    # ────────────────────────── Device Operations ───────────────────────────

    def restart_device(self, device_id: str) -> dict:
        """Remotely restart the camera."""
        return self._post_auth("restartDevice", {
            "deviceId": device_id,
        })

    def get_device_time(self, device_id: str) -> dict:
        """Get device's current time and timezone setting."""
        return self._post_auth("getDeviceTime", {
            "deviceId": device_id,
        })

    def set_night_vision(self, device_id: str, channel_id: str = "0",
                         mode: int = 2) -> dict:
        """
        Set night vision / IR mode.
        mode: 1=on, 2=auto, 3=off (white light camera: 1=auto IR, 2=auto white, 3=off)
        """
        return self._post_auth("setDeviceNightVisionMode", {
            "deviceId": device_id,
            "channelId": channel_id,
            "mode": mode,
        })

    def get_night_vision(self, device_id: str, channel_id: str = "0") -> dict:
        """Get current night vision mode."""
        return self._post_auth("getDeviceNightVisionMode", {
            "deviceId": device_id,
            "channelId": channel_id,
        })

    def set_privacy_mask(self, device_id: str, channel_id: str = "0",
                         enabled: bool = False) -> dict:
        """
        Enable/disable privacy mask (covers the lens).
        When enabled, the camera is blocked from recording/streaming.
        """
        return self._post_auth("setDevicePrivacyMask", {
            "deviceId": device_id,
            "channelId": channel_id,
            "enable": 1 if enabled else 0,
        })

    def get_privacy_mask(self, device_id: str, channel_id: str = "0") -> dict:
        """Get privacy mask status."""
        return self._post_auth("getDevicePrivacyMask", {
            "deviceId": device_id,
            "channelId": channel_id,
        })

    def get_video_quality(self, device_id: str, channel_id: str = "0") -> dict:
        """Get video stream quality/resolution settings."""
        return self._post_auth("getVideoQuality", {
            "deviceId": device_id,
            "channelId": channel_id,
        })

    def set_video_quality(self, device_id: str, channel_id: str = "0",
                          quality: int = 4) -> dict:
        """
        Set video stream quality.
        quality: 1=1080p, 2=720p, 3=VGA, 4=CIF
        """
        return self._post_auth("setVideoQuality", {
            "deviceId": device_id,
            "channelId": channel_id,
            "quality": quality,
        })

    # ────────────────────────── Recording ───────────────────────────────────

    def get_recording_list(self, device_id: str, channel_id: str = "0",
                           begin_time: str = "", end_time: str = "",
                           event_type: int = 0) -> dict:
        """
        Get list of recorded video clips stored on device SD card or cloud.
        begin_time / end_time: ISO format strings
        event_type: 0=all, 1=manual, 2=schedule, 3=alarm
        """
        return self._post_auth("getRecordingPlan", {
            "deviceId": device_id,
            "channelId": channel_id,
            "beginTime": begin_time,
            "endTime": end_time,
            "eventType": event_type,
        })

    def set_record_enable(self, device_id: str, channel_id: str = "0",
                          enabled: bool = True) -> dict:
        """Enable or disable local recording on the device."""
        return self._post_auth("setDeviceRecordEnble", {
            "deviceId": device_id,
            "channelId": channel_id,
            "enable": 1 if enabled else 0,
        })

    # ────────────────────────── Callback / Webhook ──────────────────────────

    def set_callback_url(self, callback_url: str) -> dict:
        """
        Register the webhook URL for receiving push notifications.
        Imou will POST alarm events to this URL.
        """
        return self._post_auth("setCallbackUrl", {
            "callbackUrl": callback_url,
        })

    def get_callback_url(self) -> dict:
        """Get the currently registered webhook callback URL."""
        return self._post_auth("getCallbackUrl", {})

    # ────────────────────────── Alarm history ───────────────────────────────

    def get_alarm_list(self, device_id: str, channel_id: str = "0",
                       begin_time: str = "", end_time: str = "",
                       alarm_type: str = "", limit: int = 20) -> dict:
        """
        Get alarm/event history from Imou cloud.
        Returns a list of alarm events with image URLs where available.
        """
        return self._post_auth("getAlarmMessage", {
            "deviceId": device_id,
            "channelId": channel_id,
            "beginTime": begin_time,
            "endTime": end_time,
            "alarmType": alarm_type,
            "limit": limit,
        })
