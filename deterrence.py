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
  "whitelight" — blink the camera's OWN built-in white-light LED as a strobe via the
            Xiongmai/XMeye Sofia DVRIP protocol (port 34567). No external hardware.
            VERIFIED on the Cam 1 Xiongmai (IPC_GK7205V500). The LED is NIGHT-ONLY
            (schedule + dark-scene gated), so the strobe shows after dark.
  "log"   — no hardware; logs + audits the intended action. Safe fallback.
  "isapi" — Hikvision-family built-in white-light STROBE + two-way-audio SPEAKER
            (HTTP ISAPI + digest auth; needs an -S/-U or /SL SKU). UNVERIFIED.
  "onvif" — external strobe/siren wired to the camera/NVR ALARM-OUT relay, switched
            via ONVIF SetRelayOutputState (raw SOAP). Relay verified on Cam 1
            (RELAY_OUTPUT_000, port 8899); needs an external siren wired to use.

NOTE: the isapi path is firmware/SKU-dependent and UNVERIFIED. Every driver fails
safe (logs on any error) and restores camera state after firing.
"""
import copy
import hashlib
import json
import socket
import struct
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


_ONVIF_DEV_NS = "http://www.onvif.org/ver10/device/wsdl"


def _onvif_soap(host, port, body: str, user: str, pwd: str):
    """POST one ONVIF SOAP action to the device service. Includes a
    WS-UsernameToken (PasswordDigest) for cameras that require auth; cameras that
    don't (many budget units) simply ignore it. No onvif-zeep dependency."""
    import base64
    import datetime
    import hashlib
    import os as _os
    import requests
    created = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    nonce_b = _os.urandom(16)
    digest = base64.b64encode(
        hashlib.sha1(nonce_b + created.encode() + (pwd or "").encode()).digest()).decode()
    wsse = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
    wsu = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
    header = (
        f'<s:Header><Security s:mustUnderstand="1" xmlns="{wsse}">'
        f'<UsernameToken><Username>{user}</Username>'
        f'<Password Type="http://docs.oasis-open.org/wss/2004/01/'
        f'oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</Password>'
        f'<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/'
        f'oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
        f'{base64.b64encode(nonce_b).decode()}</Nonce>'
        f'<Created xmlns="{wsu}">{created}</Created>'
        f'</UsernameToken></Security></s:Header>')
    env = (f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
           f'{header}<s:Body>{body}</s:Body></s:Envelope>')
    url = f"http://{host}:{port}/onvif/device_service"
    r = requests.post(url, data=env.encode(),
                      headers={"Content-Type": "application/soap+xml"}, timeout=6)
    return r.status_code, r.text


def _drive_onvif(camera_id, cfg: Dict, payload: Dict) -> None:
    """External strobe/siren on the alarm-out relay via ONVIF SetRelayOutputState
    (raw SOAP — no onvif-zeep needed). Monostable relays auto-reset after the
    camera's DelayTime, so we only pulse 'active'; bistable relays are reset
    explicitly after strobe_seconds."""
    host = cfg.get("host")
    if not host:
        _log(f"camera {camera_id}: onvif driver needs 'host'; skipping.")
        return
    port = int(cfg.get("onvif_port", 8899))
    token = cfg.get("relay_token", "RELAY_OUTPUT_000")
    mode = str(cfg.get("relay_mode", "monostable")).lower()
    user, pwd = cfg.get("username", "admin"), cfg.get("password", "")

    def _set(state: str):
        body = (f'<SetRelayOutputState xmlns="{_ONVIF_DEV_NS}">'
                f'<RelayOutputToken>{token}</RelayOutputToken>'
                f'<LogicalState>{state}</LogicalState></SetRelayOutputState>')
        return _onvif_soap(host, port, body, user, pwd)

    try:
        code, text = _set("active")
        _log(f"camera {camera_id}: ONVIF relay {token} -> active (HTTP {code})")
        if "Fault" in text:
            _log(f"camera {camera_id}: relay fault: {' '.join(text.split())[:200]}")
            return
        if mode == "bistable":
            time.sleep(min(int(cfg.get("strobe_seconds", DEFAULT_STROBE_S)), 30))
            _set("inactive")
            _log(f"camera {camera_id}: ONVIF relay {token} -> inactive")
        # monostable: the camera auto-resets after its DelayTime; no 'inactive'.
    except Exception as e:
        _log(f"camera {camera_id}: ONVIF relay failed: {e}")


# --- Xiongmai/Sofia white-light control (DVRIP, port 34567) ----------------- #
def _sofia_hash(password: str = "") -> str:
    md5 = hashlib.md5((password or "").encode()).digest()
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    return "".join(chars[(md5[2 * i] + md5[2 * i + 1]) % 62] for i in range(8))


class _DvripLight:
    """Tiny Xiongmai DVRIP (Sofia) client — just enough to blink Camera.WhiteLight."""

    def __init__(self, host, port, user, pwd):
        self.user, self.pwd = user, pwd
        self.session, self.seq = 0, 0
        self.sock = socket.create_connection((host, port), timeout=6)

    def _recvn(self, n):
        buf = b""
        while len(buf) < n:
            c = self.sock.recv(n - len(buf))
            if not c:
                break
            buf += c
        return buf

    def _cmd(self, msgid, data):
        p = json.dumps(data).encode() + b"\x00"
        self.sock.sendall(
            struct.pack("<BB2xII2xHI", 255, 0, self.session, self.seq, msgid, len(p)) + p)
        self.seq += 1
        h = self._recvn(20)
        if len(h) < 20:
            return {}
        _msgid, length = struct.unpack_from("<HI", h, 14)
        body = self._recvn(length) if length else b""
        try:
            return json.loads(body.rstrip(b"\x00").decode("utf-8", "ignore") or "{}")
        except Exception:
            return {}

    def login(self):
        r = self._cmd(1000, {"EncryptType": "MD5", "LoginType": "DVRIP-Web",
                             "PassWord": _sofia_hash(self.pwd), "UserName": self.user})
        sid = r.get("SessionID")
        self.session = int(sid, 16) if isinstance(sid, str) else (sid or 0)
        return self.session != 0

    def get_whitelight(self):
        return self._cmd(1042, {"Name": "Camera.WhiteLight",
                                "SessionID": "0x%08X" % self.session}).get("Camera.WhiteLight")

    def set_workmode(self, base_obj, mode):
        o = copy.deepcopy(base_obj)
        o["WorkMode"] = mode
        return self._cmd(1040, {"Name": "Camera.WhiteLight",
                                "SessionID": "0x%08X" % self.session,
                                "Camera.WhiteLight": o}).get("Ret")

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def _drive_whitelight(camera_id, cfg: Dict, payload: Dict) -> None:
    """Blink the camera's built-in white-light LED as a strobe, over Xiongmai DVRIP
    (port 34567) by toggling Camera.WhiteLight WorkMode Open/Close. The original
    WorkMode is restored afterwards.

    NOTE: these full-color white lights are NIGHT-ONLY — gated to a schedule AND to
    an actually-dark scene — so the strobe is visible after dark, which is exactly
    when after-hours intrusions occur. In daylight the command succeeds but the LED
    stays off by firmware design."""
    host = cfg.get("host")
    if not host:
        _log(f"camera {camera_id}: whitelight driver needs 'host'; skipping.")
        return
    port = int(cfg.get("dvrip_port", 34567))
    secs = int(cfg.get("strobe_seconds", DEFAULT_STROBE_S))
    half = max(0.15, float(cfg.get("blink_half_period", 0.4)))
    client = None
    try:
        client = _DvripLight(host, port, cfg.get("username", "admin"), cfg.get("password", ""))
        if not client.login():
            _log(f"camera {camera_id}: whitelight DVRIP login failed")
            return
        orig = client.get_whitelight()
        if not isinstance(orig, dict):
            _log(f"camera {camera_id}: no Camera.WhiteLight config — this SKU has no white light")
            return
        was = orig.get("WorkMode", "Auto")
        _log(f"camera {camera_id}: white-light STROBE for {secs}s (was WorkMode={was})")
        end = time.time() + secs
        while time.time() < end:
            client.set_workmode(orig, "Open")
            time.sleep(half)
            client.set_workmode(orig, "Close")
            time.sleep(half)
        client.set_workmode(orig, was)  # restore the camera's normal behaviour
    except Exception as e:
        _log(f"camera {camera_id}: whitelight strobe failed: {e}")
    finally:
        if client:
            client.close()


_DRIVERS = {"log": _drive_log, "isapi": _drive_isapi, "onvif": _drive_onvif,
            "whitelight": _drive_whitelight}


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
