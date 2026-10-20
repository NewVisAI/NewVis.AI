"""After-hours active deterrence — strobe light + audio warning on the camera,
or an external siren via the alarm-out relay, when a LIVE after-hours intrusion
alert fires.

Wiring: `deterrence.install()` registers `alert_callback` via
`alerts.register_alert_callback` at server startup. It fires only on live
`after_hours_entry` alerts — alerts.py already suppresses callbacks for offline
re-processing of uploaded footage, so this never fires on recorded clips. Each
camera is rate-limited by a cooldown.

Per-camera config lives in the camera's `deterrence` block in cameras.json:

    "deterrence": {
      "enabled": true,
      "driver": "log",              // "log" | "isapi" | "onvif"
      "host": "192.168.1.20",
      "username": "admin", "password": "",
      "strobe_seconds": 10,
      "audio": true,
      "relay_token": "AlarmOut_0", "onvif_port": 80,
      "cooldown_seconds": 30
    }

Drivers:
  "log"   — no hardware; logs + audits the intended action. SAFE DEFAULT until the
            camera's capabilities are confirmed by a live probe.
  "isapi" — Hikvision-family built-in white-light STROBE + two-way-audio SPEAKER
            (HTTP ISAPI + digest auth; needs an -S/-U or /SL SKU).
  "onvif" — external strobe/siren wired to the camera/NVR ALARM-OUT relay, switched
            via ONVIF SetRelayOutputState (needs onvif-zeep + a relay output).

NOTE: the isapi/onvif paths are firmware/SKU-dependent and were written while the
camera was offline, so they are UNVERIFIED against real hardware. They fail safe
(log on any error) and must be validated with a live probe + a dry-run before use.
"""
import threading
import time
from typing import Dict, Optional

AFTER_HOURS_ALERT = "after_hours_entry"
DEFAULT_COOLDOWN_S = 30
DEFAULT_STROBE_S = 10

_last_fire: Dict[int, float] = {}
_last_fire_lock = threading.Lock()


def _log(msg: str) -> None:
    try:
        print(f"[DETERRENCE] {msg}", flush=True)
    except Exception:
        pass


def _audit(camera_id, driver: str) -> None:
    try:
        import audit_log
        audit_log.record_audit("system", "system", "deterrence_fired",
                               target=f"camera {camera_id} ({driver})", ip="")
    except Exception:
        pass


# --- drivers ---------------------------------------------------------------- #
def _drive_log(camera_id, cfg: Dict, payload: Dict) -> None:
    _log(f"[LOG DRIVER] camera {camera_id}: would fire STROBE + AUDIO warning "
         f"(after-hours intrusion). Set 'driver' to 'isapi' or 'onvif' in the "
         f"camera's deterrence config once its capabilities are confirmed.")


def _drive_isapi(camera_id, cfg: Dict, payload: Dict) -> None:
    """Hikvision-family built-in white-light strobe + two-way-audio speaker."""
    import requests
    from requests.auth import HTTPDigestAuth
    host = cfg.get("host")
    if not host:
        _log(f"camera {camera_id}: isapi driver needs 'host'; skipping.")
        return
    auth = HTTPDigestAuth(cfg.get("username", "admin"), cfg.get("password", ""))
    secs = int(cfg.get("strobe_seconds", DEFAULT_STROBE_S))
    body = (f"<WhiteLightAlarm><whiteLightDuration>{secs}</whiteLightDuration>"
            f"<frequency>high</frequency></WhiteLightAlarm>")
    url = f"http://{host}/ISAPI/Smart/WhiteLightAlarm/1"
    try:
        r = requests.put(url, data=body, auth=auth, timeout=5,
                         headers={"Content-Type": "application/xml"})
        _log(f"camera {camera_id}: ISAPI white-light PUT -> {r.status_code}")
    except Exception as e:
        _log(f"camera {camera_id}: ISAPI white-light failed: {e}")
    if cfg.get("audio", True):
        base = f"http://{host}/ISAPI/System/TwoWayAudio/channels/1"
        try:
            r = requests.put(base + "/open", auth=auth, timeout=5)
            _log(f"camera {camera_id}: ISAPI two-way audio open -> {r.status_code}")
            # A stored audible-warning clip / streamed G.711 would be pushed here;
            # left as open/close until the camera's audio codec is confirmed.
            requests.put(base + "/close", auth=auth, timeout=5)
        except Exception as e:
            _log(f"camera {camera_id}: ISAPI two-way audio failed: {e}")


def _drive_onvif(camera_id, cfg: Dict, payload: Dict) -> None:
    """External strobe/siren on the alarm-out relay via ONVIF SetRelayOutputState."""
    host = cfg.get("host")
    if not host:
        _log(f"camera {camera_id}: onvif driver needs 'host'; skipping.")
        return
    try:
        from onvif import ONVIFCamera
    except Exception as e:
        _log(f"camera {camera_id}: onvif-zeep not installed ({e}); "
             f"run `pip install onvif-zeep`.")
        return
    token = cfg.get("relay_token", "AlarmOut_0")
    secs = min(int(cfg.get("strobe_seconds", DEFAULT_STROBE_S)), 30)
    try:
        cam = ONVIFCamera(host, int(cfg.get("onvif_port", 80)),
                          cfg.get("username", "admin"), cfg.get("password", ""))
        dev = cam.create_devicemgmt_service()
        dev.SetRelayOutputState({"RelayOutputToken": token, "LogicalState": "active"})
        _log(f"camera {camera_id}: ONVIF relay {token} -> active")
        time.sleep(secs)
        dev.SetRelayOutputState({"RelayOutputToken": token, "LogicalState": "inactive"})
        _log(f"camera {camera_id}: ONVIF relay {token} -> inactive")
    except Exception as e:
        _log(f"camera {camera_id}: ONVIF relay failed: {e}")


_DRIVERS = {"log": _drive_log, "isapi": _drive_isapi, "onvif": _drive_onvif}


def _cfg_for(camera_id) -> Dict:
    try:
        import camera_registry
        cam = camera_registry.get_camera(camera_id) or {}
        return cam.get("deterrence") or {}
    except Exception:
        return {}


def _fire(camera_id, cfg: Dict, payload: Dict) -> None:
    kind = str(cfg.get("driver", "log")).lower()
    driver = _DRIVERS.get(kind, _drive_log)
    try:
        driver(camera_id, cfg, payload)
        _audit(camera_id, kind)
    except Exception as e:
        _log(f"camera {camera_id}: driver '{kind}' crashed: {e}")


def trigger(camera_id, payload: Optional[Dict] = None) -> bool:
    """Fire deterrence for a camera, honoring per-camera `enabled` + cooldown.
    The hardware call runs on a daemon thread so it never blocks the pipeline.
    Returns True if a fire was dispatched, False if suppressed."""
    cfg = _cfg_for(camera_id)
    if not cfg.get("enabled", True):
        return False
    cooldown = float(cfg.get("cooldown_seconds", DEFAULT_COOLDOWN_S))
    now = time.monotonic()
    with _last_fire_lock:
        if now - _last_fire.get(camera_id, -1e9) < cooldown:
            return False
        _last_fire[camera_id] = now
    threading.Thread(target=_fire, args=(camera_id, cfg, payload or {}),
                     daemon=True).start()
    return True


def alert_callback(payload: Dict) -> None:
    """Registered with alerts.register_alert_callback. Acts only on live
    after-hours intrusions."""
    if payload.get("alert_type") != AFTER_HOURS_ALERT:
        return
    camera_id = payload.get("camera_id")
    if camera_id is None:
        return
    _log(f"after-hours intrusion on camera {camera_id} -> deterrence")
    trigger(camera_id, payload)


def install() -> None:
    import alerts
    alerts.register_alert_callback(alert_callback)
    _log("installed: after-hours deterrence callback registered.")
