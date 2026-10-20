r"""Read-only camera capability probe (ISAPI / Hikvision-OEM + basic ONVIF hints).

Determines, for an IP camera, whether it exposes a white-light STROBE, a SPEAKER
(two-way audio), and an alarm-out RELAY — the capabilities the after-hours
deterrence feature (deterrence.py) needs to pick its driver ("isapi" vs "onvif").

GET-only; safe to run against a live camera. Reads host/credentials from the
camera's `deterrence` block in cameras.json (or pass --host/--user/--pass).

Usage (native Windows venv):
    .\.venv-win\Scripts\python.exe scripts\probe_camera.py            # cam id 1
    .\.venv-win\Scripts\python.exe scripts\probe_camera.py --host 192.168.1.20
"""
import argparse
import json
import os
import socket
import sys

import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth

TIMEOUT = 5
ENDPOINTS = {
    "deviceInfo":      "/ISAPI/System/deviceInfo",
    "capabilities":    "/ISAPI/System/capabilities",
    "io_outputs":      "/ISAPI/System/IO/outputs",
    "twoway_audio":    "/ISAPI/System/TwoWayAudio/channels",
    "ext_device":      "/ISAPI/System/externalDevice/supportInfo",
    "whitelight_caps": "/ISAPI/Smart/WhiteLightAlarm/1/capabilities",
}


def _cam_deterrence(cam_id):
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cameras.json")
    try:
        with open(path, encoding="utf-8") as fh:
            reg = json.load(fh)
        for cam in reg.get("cameras", []):
            if cam.get("id") == cam_id:
                return cam.get("deterrence", {}) or {}
    except Exception:
        pass
    return {}


def reachable(host, port, t=3):
    try:
        with socket.create_connection((host, port), timeout=t):
            return True
    except OSError as e:
        return f"unreachable: {e}"


def hit(host, path, user, pwd):
    url = f"http://{host}{path}"
    for name, auth in (("digest", HTTPDigestAuth(user, pwd)), ("basic", HTTPBasicAuth(user, pwd))):
        try:
            r = requests.get(url, auth=auth, timeout=TIMEOUT)
            if r.status_code != 401:
                return f"[{r.status_code}/{name}] {r.text.strip().replace(chr(10),' ')[:600]}"
        except requests.RequestException as e:
            return f"[ERR] {type(e).__name__}: {e}"
    return "[401] auth failed on digest+basic"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam-id", type=int, default=1)
    ap.add_argument("--host")
    ap.add_argument("--user")
    ap.add_argument("--pass", dest="pwd")
    a = ap.parse_args()

    cfg = _cam_deterrence(a.cam_id)
    host = a.host or cfg.get("host")
    user = a.user if a.user is not None else cfg.get("username", "admin")
    pwd = a.pwd if a.pwd is not None else cfg.get("password", "")
    if not host:
        print("No host. Pass --host or add a deterrence.host to cameras.json.")
        return 2

    print(f"=== probing {host} (user={user!r}) ===")
    print(f"port 80 (ISAPI/ONVIF): {reachable(host, 80)}")
    print(f"port 554 (RTSP):       {reachable(host, 554)}\n")
    for name, path in ENDPOINTS.items():
        print(f"### {name}  ({path})")
        print("   ", hit(host, path, user, pwd), "\n")
    print("Capability read -> set the camera's deterrence.driver to 'isapi' "
          "(strobe/speaker present) or 'onvif' (relay only) in cameras.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
