"""
Per-camera live analytics — SMOOTH feed up front, heavy processing in the back.

Two decoupled threads per camera (one RTSP decode, fanned out):

  * Reader/display thread  — owns the RTSP connection, reads at the camera's full
    frame rate, and publishes raw frames (with a LIVE banner, NO YOLO) to
    backend_runner.latest_frames[camera_id]. This is what the live tile shows, so
    the feed stays smooth and real-time no matter how slow analysis is.

  * Analytics thread       — pulls only the LATEST decoded frame (old ones are
    dropped) and runs the FULL pipeline (detection, tracking, ReID, zones,
    running/fall/violence + event & alert logging) at its own pace. It never
    writes to the display cache, so slow processing can't make the feed choppy.

Design notes:
  * Thread-based (not a forked pool worker) — avoids backend_runner's fork issues.
  * Heavy models (YOLO + OSNet) are loaded once, shared, and pre-warmed at startup.
  * A fast socket probe precedes any cv2 open, so an offline camera never blocks
    the server; open is retried with backoff and auto-recovers.

    import live_analytics
    live_analytics.start(1)          # smooth feed + background event logging
    live_analytics.status()
    live_analytics.stop(1)
"""

import os
import queue
import threading
import time
from typing import Dict, Optional

import inference_config

# Set the FFmpeg capture options (TCP + timeout, plus a hardware decoder when
# DECODE_BACKEND selects one — lever ②) BEFORE the first cv2.VideoCapture.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    inference_config.ffmpeg_capture_options(),
)

import cv2

import backend_runner
import camera_registry
import motion_gate
from backend_runner import FrameInjector, overlay_live_status

DISPLAY_WIDTH = 640        # published live-view width (raw feed)
PROCESS_WIDTH = 1280       # analytics downscale (zones auto-scale)
DEFAULT_ANALYTICS_FPS = 3  # background pipeline rate when a person is present
IDLE_ANALYTICS_FPS = 1     # slow rate on an empty camera (adaptive gate)
GATE_HANGOVER_S = 8.0      # stay at the fast rate for this long after last person
DISPLAY_JPEG_QUALITY = 70

_workers: Dict[int, "_LiveWorker"] = {}
_lock = threading.Lock()

_models = None
_models_lock = threading.Lock()


def _get_models():
    """(detector, identity_manager, incident_manager), loaded once, shared."""
    global _models
    with _models_lock:
        if _models is None:
            from app import HumanDetector, GlobalIdentityManager, IncidentManager
            _models = (
                HumanDetector(model_type="yolo", weights="yolov8n.pt"),
                GlobalIdentityManager(),
                IncidentManager(),
            )
        return _models


def prewarm():
    threading.Thread(target=_safe_prewarm, name="live-analytics-prewarm", daemon=True).start()


def _safe_prewarm():
    try:
        _get_models()
        print("[LIVE ANALYTICS] Models pre-warmed and ready.", flush=True)
    except Exception as exc:
        print(f"[LIVE ANALYTICS] Pre-warm failed: {exc}", flush=True)


def _camera_config(camera_id: int) -> Optional[dict]:
    for cam in camera_registry._load().get("cameras", []):
        if int(cam.get("id")) == int(camera_id):
            cfg = dict(cam)
            cfg["camera_id"] = int(cam["id"])
            return cfg
    return None


def _source_reachable(source, timeout: float = 0.5) -> bool:
    """Fast probe before the slow, GIL-holding cv2 open on an offline camera."""
    import socket
    s = str(source)
    if not s.lower().startswith(("rtsp://", "rtmp://", "http://", "https://")):
        return os.path.exists(s)
    try:
        rest = s.split("://", 1)[1]
        hostport = rest.split("/", 1)[0]
        if "@" in hostport:
            hostport = hostport.split("@", 1)[1]
        if ":" in hostport:
            host, port_s = hostport.rsplit(":", 1)
            port = int(port_s)
        else:
            host, port = hostport, 554
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except Exception:
        return False


class _LiveWorker:
    def __init__(self, config: dict, analytics_fps: int = DEFAULT_ANALYTICS_FPS,
                 idle_fps: float = IDLE_ANALYTICS_FPS, hangover_s: float = GATE_HANGOVER_S,
                 adaptive: bool = True):
        self.config = config
        self.camera_id = int(config["camera_id"])
        self.name = str(config.get("name", f"Cam {self.camera_id}"))
        # Sub-stream analytics: decode the low-res sub-stream (big decode saving)
        # when enabled. This single reader owns the only decode, so switching its
        # source moves both display + analytics onto the sub-stream. Off by default
        # -> self.source is just the configured main source (unchanged behaviour).
        raw_source = config["source"]
        if inference_config.use_substream():
            self.source = inference_config.to_substream_url(raw_source, config.get("substream_source"))
            if self.source != raw_source:
                print(f"[LIVE ANALYTICS] Cam {self.camera_id}: SUB-stream analytics -> {self.source}", flush=True)
            else:
                print(f"[LIVE ANALYTICS] Cam {self.camera_id}: USE_SUBSTREAM on but no sub-stream URL derived; "
                      "using main stream (set 'substream_source' in the registry to override).", flush=True)
        else:
            self.source = raw_source
        # Motion gating (lever ③): thin DECODE on cameras the camera itself reports
        # as idle (via ONVIF). Fail-safe + person-hold enforced in the reader loop.
        self.motion_gating = inference_config.motion_gating()
        self.idle_decode_interval = 1.0 / max(0.2, inference_config.idle_decode_fps())
        self.decode_gate = "active"   # 'active' (full-rate) or 'motion-idle' (heartbeat)
        if self.motion_gating:
            try:
                motion_gate.start_onvif_listener(config)
                print(f"[LIVE ANALYTICS] Cam {self.camera_id}: motion gating ON "
                      f"(heartbeat {inference_config.idle_decode_fps():.1f} fps when idle).", flush=True)
            except Exception as exc:
                print(f"[LIVE ANALYTICS] Cam {self.camera_id}: motion listener start failed ({exc}); "
                      "fail-safe to full-rate decode.", flush=True)
        # Adaptive-rate gate: run the pipeline fast when a person is around, slow
        # (idle rate) when the camera is empty — with a hangover so a person who
        # stops moving (or falls) keeps being processed.
        self.active_interval = 1.0 / max(1, analytics_fps)   # fast rate (occupied)
        self.idle_interval = 1.0 / max(0.2, idle_fps)        # slow rate (empty)
        self.hangover_s = hangover_s
        self.adaptive = adaptive
        self.stop_event = threading.Event()
        # size-1 hand-off: reader publishes the newest frame, analytics drops old.
        self._frame_q: "queue.Queue" = queue.Queue(maxsize=1)

        self.reader_thread = threading.Thread(target=self._reader_loop, name=f"live-reader-{self.camera_id}", daemon=True)
        self.analytics_thread = threading.Thread(target=self._analytics_loop, name=f"live-analytics-{self.camera_id}", daemon=True)

        self.status = "starting"          # reader/display status
        self.display_frames = 0
        self.analytics_frames = 0
        self.active_frames = 0            # frames processed at the fast rate
        self.idle_frames = 0             # frames processed at the slow (empty) rate
        self.gate = "idle"              # 'active' (person around) or 'idle' (empty)
        self.last_error: Optional[str] = None

    def start(self):
        self.reader_thread.start()
        self.analytics_thread.start()

    def stop(self):
        self.stop_event.set()

    def snapshot(self) -> dict:
        return {
            "status": self.status,
            "display_frames": self.display_frames,
            "analytics_frames": self.analytics_frames,
            "gate": self.gate,
            "decode_gate": self.decode_gate,
            "motion_gating": self.motion_gating,
            "motion_state": motion_gate.gate().state(self.camera_id) if self.motion_gating else "off",
            "active_frames": self.active_frames,
            "idle_frames": self.idle_frames,
            "adaptive": self.adaptive,
            "name": self.name,
            "last_error": self.last_error,
            "alive": self.reader_thread.is_alive(),
        }

    # --- Reader/display: owns RTSP, keeps the feed smooth ------------------- #
    def _reader_loop(self):
        from app import resolve_capture_source
        cap = None
        fails = 0
        open_backoff = 2.0
        try:
            while not self.stop_event.is_set():
                try:
                    if cap is None or not cap.isOpened():
                        self.status = "waiting-for-camera"
                        if not _source_reachable(self.source):
                            self.stop_event.wait(open_backoff)
                            open_backoff = min(15.0, open_backoff * 1.5)
                            continue
                        cap = cv2.VideoCapture(resolve_capture_source(self.source))
                        if not cap.isOpened():
                            try:
                                cap.release()
                            except Exception:
                                pass
                            cap = None
                            self.stop_event.wait(open_backoff)
                            open_backoff = min(15.0, open_backoff * 1.5)
                            continue
                        open_backoff = 2.0
                        fails = 0
                        self.status = "running"
                        # One-time: log whether hardware decode actually engaged, so a
                        # GPU node can confirm NVDEC/QuickSync is on (0 = software).
                        if inference_config.decode_backend() != "cpu" and not getattr(self, "_hw_logged", False):
                            try:
                                hw = cap.get(cv2.CAP_PROP_HW_ACCELERATION)
                                print(f"[LIVE ANALYTICS] Cam {self.camera_id}: "
                                      f"decode_backend={inference_config.decode_backend()} "
                                      f"CAP_PROP_HW_ACCELERATION={hw} (0=software/none).", flush=True)
                            except Exception:
                                pass
                            self._hw_logged = True

                    ok, frame = cap.read()
                    if not ok or frame is None:
                        fails += 1
                        if fails >= 8:
                            try:
                                cap.release()
                            except Exception:
                                pass
                            cap = None
                            fails = 0
                            self.status = "reconnecting"
                        else:
                            self.stop_event.wait(0.05)
                        continue
                    fails = 0

                    # Publish the raw frame (LIVE banner only) — smooth, real-time.
                    disp = frame
                    if disp.shape[1] > DISPLAY_WIDTH:
                        s = DISPLAY_WIDTH / float(disp.shape[1])
                        disp = cv2.resize(disp, (DISPLAY_WIDTH, int(disp.shape[0] * s)))
                    disp = overlay_live_status(disp, self.name)
                    enc_ok, buf = cv2.imencode(".jpg", disp, [cv2.IMWRITE_JPEG_QUALITY, DISPLAY_JPEG_QUALITY])
                    if enc_ok:
                        backend_runner.latest_frames[self.camera_id] = buf.tobytes()
                        self.display_frames += 1

                    # Hand the newest frame to analytics (drop any stale one).
                    if self._frame_q.full():
                        try:
                            self._frame_q.get_nowait()
                        except queue.Empty:
                            pass
                    try:
                        self._frame_q.put_nowait(frame)
                    except queue.Full:
                        pass

                    # Motion gating (lever ③): thin decode to the heartbeat rate on a
                    # motion-idle camera. Stays full-rate whenever the person-gate is
                    # active (someone is/was here — covers a motionless/fallen person)
                    # OR motion is recent OR the motion source is unreliable
                    # (is_active fail-safes to True). Never skips a watched camera.
                    if (self.motion_gating and self.gate != "active"
                            and not motion_gate.gate().is_active(self.camera_id)):
                        self.decode_gate = "motion-idle"
                        self.stop_event.wait(self.idle_decode_interval)
                    else:
                        self.decode_gate = "active"
                except Exception as exc:
                    self.last_error = f"reader error: {exc}"
                    self.stop_event.wait(0.3)
        finally:
            try:
                if cap is not None:
                    cap.release()
            except Exception:
                pass
            backend_runner.latest_frames.pop(self.camera_id, None)
            self.status = "stopped"

    # --- Analytics: heavy pipeline, background, never gates the feed -------- #
    def _analytics_loop(self):
        from app import (
            _create_camera_runtime, _process_camera_frame,
            reset_runtime_state, reset_running_state, reset_fall_state,
        )
        try:
            detector, identity_manager, incident_manager = _get_models()
        except Exception as exc:
            self.last_error = f"model load: {exc}"
            return

        reset_runtime_state()
        reset_running_state()
        reset_fall_state()

        camera_state = None
        last_run = 0.0
        last_person_time = -1e9   # when we last saw a person (drives the gate)
        try:
            while not self.stop_event.is_set():
                try:
                    frame = self._frame_q.get(timeout=1.0)
                except queue.Empty:
                    continue

                now = time.time()
                # Adaptive-rate gate: fast rate while a person was seen recently
                # (within the hangover), slow idle rate on an empty camera. The
                # DISPLAY is unaffected — this only throttles the heavy pipeline.
                if self.adaptive and (now - last_person_time) > self.hangover_s:
                    self.gate = "idle"
                    interval = self.idle_interval
                else:
                    self.gate = "active"
                    interval = self.active_interval
                if now - last_run < interval:
                    continue
                last_run = now

                try:
                    if camera_state is None:
                        camera_state = _create_camera_runtime(self.config)
                        if camera_state is None:
                            continue
                        try:
                            camera_state.cap.release()
                        except Exception:
                            pass
                        identity_manager.clear_camera_track_mappings(self.camera_id)

                    proc = frame
                    if proc.shape[1] > PROCESS_WIDTH:
                        s = PROCESS_WIDTH / float(proc.shape[1])
                        proc = cv2.resize(proc, (PROCESS_WIDTH, int(proc.shape[0] * s)))
                    camera_state.cap = FrameInjector(proc)
                    camera_state.finished = False
                    _process_camera_frame(camera_state, detector, identity_manager, incident_manager, "multi")
                    self.analytics_frames += 1
                    if self.gate == "active":
                        self.active_frames += 1
                    else:
                        self.idle_frames += 1
                    # A person present (now or recently) keeps us at the fast rate;
                    # crucially, YOLO still detects a motionless/fallen person, so the
                    # gate holds active on them — no "background absorption" blind spot.
                    if getattr(camera_state, "last_person_count", 0) > 0:
                        last_person_time = now
                except Exception as exc:
                    self.last_error = f"analytics error: {exc}"
                    self.stop_event.wait(0.3)
        finally:
            try:
                from event import finalize_camera_sessions, flush_tracking_data
                finalize_camera_sessions(self.camera_id, self.config.get("source"))
                flush_tracking_data()
            except Exception:
                pass


def start(camera_id: int, analytics_fps: int = DEFAULT_ANALYTICS_FPS) -> dict:
    camera_id = int(camera_id)
    with _lock:
        existing = _workers.get(camera_id)
        if existing and existing.reader_thread.is_alive():
            return {"started": False, "reason": "already running", **existing.snapshot()}
        config = _camera_config(camera_id)
        if config is None:
            return {"started": False, "reason": "unknown camera_id"}
        worker = _LiveWorker(config, analytics_fps=analytics_fps)
        _workers[camera_id] = worker
        worker.start()
        return {"started": True, "camera_id": camera_id, **worker.snapshot()}


def stop(camera_id: int) -> dict:
    camera_id = int(camera_id)
    with _lock:
        worker = _workers.get(camera_id)
        if worker is None:
            return {"stopped": False, "reason": "not running"}
        worker.stop()
        return {"stopped": True, "camera_id": camera_id}


def stop_all():
    with _lock:
        for worker in _workers.values():
            worker.stop()


def status() -> dict:
    with _lock:
        return {cid: w.snapshot() for cid, w in _workers.items()}
