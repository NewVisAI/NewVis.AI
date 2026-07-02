"""
Running detection.

Deliberately built with NO new ML model: it derives "running" purely from
motion already computed by the existing detector/tracker pipeline. A
person's frame-to-frame centroid displacement is measured and normalized
by their own bounding-box height (so the same real-world stride reads as
the same speed whether the person is near or far from the camera, without
needing camera calibration). This is the same trick applicable to slip/fall
detection later, since both start from bbox motion analysis.

Usage from app.py's per-track loop:

    from running import check_running, is_currently_running, reset_running_state

    check_running(
        track_key=(camera_id, track_id),
        global_id=global_id,
        camera_id=camera_id,
        bbox=(x1, y1, x2, y2),
        video_time=video_time,
        video_path=camera_state.source,
    )
    ...
    if is_currently_running(global_id, video_time):
        label += " | RUNNING"

Call reset_running_state() wherever the rest of the runtime state is reset
(new session / seek / camera restart) so stale motion history doesn't
trigger false positives after a seek.
"""

from typing import Dict, Optional, Tuple

# Speed threshold expressed in "bbox-heights per second" rather than raw
# pixels. A person's height is roughly proportional to their stride length
# in the same frame, so this scales reasonably with distance from the
# camera without any real-world calibration. Tune this per-deployment once
# real footage is available.
RUNNING_SPEED_THRESHOLD = 2.5       # bbox-heights per second (tuned for classroom sitting)
MIN_SAMPLES_BEFORE_TRIGGER = 6      # ignore the first couple of noisy frames
ALERT_COOLDOWN_SECONDS = 5.0        # don't re-log the same sprint every frame
DISPLAY_WINDOW_SECONDS = 3.0        # how long the "RUNNING" label stays on screen

_track_history: Dict[Tuple, Dict[str, object]] = {}
_active_running: Dict[int, float] = {}  # global_id -> video_time it should stop showing


def reset_running_state() -> None:
    _track_history.clear()
    _active_running.clear()


def _speed_bbox_heights_per_sec(prev, curr, dt: float) -> float:
    if dt <= 0:
        return 0.0
    px, py, ph = prev
    cx, cy, ch = curr
    dist = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5
    avg_height = max(1.0, (ph + ch) / 2.0)
    return (dist / avg_height) / dt


def is_currently_running(global_id: int, video_time: float) -> bool:
    expiry = _active_running.get(global_id)
    return expiry is not None and video_time <= expiry


def check_running(
    track_key,
    global_id: int,
    camera_id: Optional[int],
    bbox: Tuple[int, int, int, int],
    video_time: float,
    video_path: str = "",
) -> bool:
    """
    Updates per-track motion history. Returns True the moment running is
    newly detected for this track (edge-triggered + cooldown, so a sustained
    sprint logs one event rather than one per frame).
    """
    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    height = max(1.0, y2 - y1)

    history = _track_history.get(track_key)
    if history is None:
        _track_history[track_key] = {
            "prev": (cx, cy, height),
            "prev_time": video_time,
            "samples": 0,
            "last_alert_time": -1e9,
        }
        return False

    prev_time = history["prev_time"]
    dt = video_time - prev_time
    
    # Exponential Moving Average (EMA) to smooth out bounding box jitter
    # (very important when using low confidence thresholds where bbox sizes flicker)
    alpha = 0.15
    px, py, ph = history["prev"]
    scx = alpha * cx + (1.0 - alpha) * px
    scy = alpha * cy + (1.0 - alpha) * py
    sheight = alpha * height + (1.0 - alpha) * ph

    speed = _speed_bbox_heights_per_sec(history["prev"], (scx, scy, sheight), dt)

    history["prev"] = (scx, scy, sheight)
    history["prev_time"] = video_time
    history["samples"] = int(history.get("samples", 0)) + 1

    newly_detected = False
    if speed >= RUNNING_SPEED_THRESHOLD and history["samples"] >= MIN_SAMPLES_BEFORE_TRIGGER:
        _active_running[global_id] = video_time + DISPLAY_WINDOW_SECONDS
        if video_time - float(history["last_alert_time"]) >= ALERT_COOLDOWN_SECONDS:
            history["last_alert_time"] = video_time
            newly_detected = True
            _write_running_event(global_id, camera_id, speed, video_time, video_path)

    return newly_detected


def _write_running_event(global_id, camera_id, speed, video_time, video_path) -> None:
    from db_schema import connect_db
    from event import _utc_now_iso

    timestamp = _utc_now_iso()
    try:
        conn = connect_db(validate_schema=False)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO events (
                timestamp, object_type, track_id, global_id, camera_id, video_path,
                frame_number, frame_start, frame_end, video_time, zone_id, event_type,
                entry_time, exit_time, duration, stayed, event_mode, mode_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp, "person", -1, global_id, camera_id, video_path,
                0, 0, 0, video_time, 0, "running_detected",
                timestamp, timestamp, 0.0, 0, "single", "single",
            ),
        )
        conn.commit()
    except Exception as exc:
        print(f"Error logging running event: {exc}")
    finally:
        if "conn" in locals():
            conn.close()

    print(
        f"🏃 [RUNNING DETECTED] GID {global_id} camera {camera_id} "
        f"speed={speed:.2f} bbox-heights/sec"
    )
