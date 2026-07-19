"""
Camera-side motion gating (cost-optimization lever ③).

Most school cameras are idle most of the time (nights, weekends, empty rooms).
Decoding those idle streams at full frame rate is wasted work. This module lets
the analytics reader thin its DECODE rate on a camera until the camera itself
reports motion (over ONVIF), then snap back to full rate.

Why this and not just the adaptive-rate gate? The adaptive gate throttles the
heavy pipeline (inference), but the reader still decodes every frame for the live
tile. Decode is the usual bottleneck on a GPU box, so skipping decode on idle
cameras is the real saving.

SAFETY (non-negotiable for a security product):
  * Fail-safe: a camera with no working motion source is ALWAYS treated as active
    (is_active -> True), so a broken/unsupported ONVIF subscription can never make
    us stop watching a camera.
  * The reader also keeps full decode whenever the analytics person-gate is active,
    so a person who stops moving (or falls) keeps the camera hot regardless of
    motion events. Motion gating only deepens the *idle* saving; it never overrides
    "a person is/was here".
  * The idle heartbeat still decodes a few frames/sec, so a person who appears
    without tripping the camera's motion detector is still picked up within ~0.5 s.

The ONVIF listener is best-effort and defensive: any failure (library missing,
camera doesn't speak ONVIF, auth/network error) marks the camera unsubscribed,
which fails safe to "always active". Validate against the real Adiva camera
on-site — until then, enabling MOTION_GATING simply changes nothing on cameras
whose motion source didn't come up.
"""

import threading
import time
from typing import Dict, Optional
from urllib.parse import urlparse

import inference_config


class MotionGate:
    """Thread-safe per-camera motion state. Fed by a motion source (ONVIF); read by
    the reader loop to decide full-rate vs heartbeat decode."""

    def __init__(self, window_s: Optional[float] = None):
        self._last_motion: Dict[int, float] = {}
        self._subscribed: Dict[int, bool] = {}
        self._lock = threading.Lock()
        self.window_s = float(window_s if window_s is not None else inference_config.motion_window_s())

    def record_motion(self, camera_id: int, ts: Optional[float] = None) -> None:
        with self._lock:
            self._last_motion[int(camera_id)] = float(ts if ts is not None else time.time())

    def mark_subscribed(self, camera_id: int, ok: bool) -> None:
        with self._lock:
            self._subscribed[int(camera_id)] = bool(ok)

    def is_active(self, camera_id: int) -> bool:
        """True => decode this camera at full rate. Fail-safe: with no working motion
        source, always True (never skip a camera we can't get motion events from)."""
        cid = int(camera_id)
        with self._lock:
            if not self._subscribed.get(cid, False):
                return True  # fail-safe
            last = self._last_motion.get(cid, 0.0)
        return (time.time() - last) <= self.window_s

    def state(self, camera_id: int) -> str:
        cid = int(camera_id)
        with self._lock:
            sub = self._subscribed.get(cid, False)
        if not sub:
            return "unsubscribed"  # -> treated as active (fail-safe)
        return "active" if self.is_active(cid) else "motion-idle"


_gate: Optional[MotionGate] = None


def gate() -> MotionGate:
    global _gate
    if _gate is None:
        _gate = MotionGate()
    return _gate


# --------------------------------------------------------------------------- #
# ONVIF motion source (best-effort, fail-safe). Validate on the real camera.
# --------------------------------------------------------------------------- #
def _onvif_params(camera_cfg: dict):
    """Pull ONVIF host/port/creds from the camera config. Credentials default to the
    ones embedded in the RTSP source; port defaults to 80 (ONVIF, not RTSP 554)."""
    src = str(camera_cfg.get("source", ""))
    parsed = urlparse(src if "://" in src else "rtsp://" + src)
    host = parsed.hostname or camera_cfg.get("onvif_host")
    user = camera_cfg.get("onvif_user", parsed.username or "admin")
    password = camera_cfg.get("onvif_pass", parsed.password or "")
    port = int(camera_cfg.get("onvif_port", 80))
    return host, port, user, password


def start_onvif_listener(camera_cfg: dict, target: Optional[MotionGate] = None) -> threading.Thread:
    """Spawn a daemon thread that subscribes to the camera's ONVIF motion events and
    feeds the gate. Returns the thread. On ANY failure it marks the camera
    unsubscribed (fail-safe to always-active) and exits quietly."""
    g = target or gate()
    camera_id = int(camera_cfg["camera_id"] if "camera_id" in camera_cfg else camera_cfg["id"])

    def _run():
        host, port, user, password = _onvif_params(camera_cfg)
        if not host:
            g.mark_subscribed(camera_id, False)
            return
        try:
            from onvif import ONVIFCamera  # onvif-zeep; optional dependency
        except Exception:
            print(f"[motion_gate] Cam {camera_id}: onvif-zeep not installed; "
                  "motion gating inactive (fail-safe: camera stays full-rate). "
                  "pip install onvif-zeep to enable.", flush=True)
            g.mark_subscribed(camera_id, False)
            return
        try:
            cam = ONVIFCamera(host, port, user, password)
            events = cam.create_events_service()
            pullpoint = cam.create_pullpoint_service()
            g.mark_subscribed(camera_id, True)
            print(f"[motion_gate] Cam {camera_id}: ONVIF motion subscription up ({host}:{port}).", flush=True)
            while True:
                try:
                    msgs = pullpoint.PullMessages({"Timeout": "PT10S", "MessageLimit": 10})
                    for msg in getattr(msgs, "NotificationMessage", []) or []:
                        topic = str(getattr(getattr(msg, "Topic", None), "_value_1", "")).lower()
                        if "motion" in topic or "cellmotion" in topic or "motionalarm" in topic:
                            # Only record on the "started" transition when we can read it;
                            # otherwise any motion message keeps the camera awake.
                            g.record_motion(camera_id)
                except Exception as exc:
                    print(f"[motion_gate] Cam {camera_id}: ONVIF pull error ({exc}); "
                          "marking unsubscribed (fail-safe).", flush=True)
                    g.mark_subscribed(camera_id, False)
                    return
        except Exception as exc:
            print(f"[motion_gate] Cam {camera_id}: ONVIF subscribe failed ({exc}); "
                  "fail-safe to full-rate.", flush=True)
            g.mark_subscribed(camera_id, False)
            return

    t = threading.Thread(target=_run, name=f"onvif-motion-{camera_id}", daemon=True)
    t.start()
    return t
