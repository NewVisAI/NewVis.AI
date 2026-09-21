"""
Running detection.

Deliberately built with NO new ML model: it derives "running" purely from
motion already computed by the existing detector/tracker pipeline. A
person's frame-to-frame centroid displacement is measured and normalized
by their own bounding-box height (so the same real-world stride reads as
the same speed whether the person is near or far from the camera, without
needing camera calibration). This is the same trick applicable to slip/fall
detection later, since both start from bbox motion analysis.

Robustness layers on top of the raw speed measurement:

  - **Sustained-speed** requirement: N consecutive frames above threshold
    (kills the "one jittery bbox spike" false positive).
  - **Direction coherence**: the smoothed velocity vector must keep the same
    general direction across the sustained window — real running has a
    dominant heading; bbox jitter oscillates.
  - **Edge-of-frame suppression**: bbox distortion at frame boundaries
    inflates apparent speed, so a track whose centre is right at the edge
    is not eligible to fire.
  - **Confidence class**: attached to every event; combines margin-over-
    threshold with direction coherence.

All thresholds resolve through ``inference_config`` (env > deployment.json >
default) so a deployment can tune per site without editing code.

Usage from app.py's per-track loop:

    from running import check_running, is_currently_running, reset_running_state

    check_running(
        track_key=(camera_id, track_id),
        global_id=global_id,
        camera_id=camera_id,
        bbox=(x1, y1, x2, y2),
        video_time=video_time,
        video_path=camera_state.source,
        frame_shape=frame.shape,   # optional; enables edge suppression
    )
    ...
    if is_currently_running(global_id, video_time):
        label += " | RUNNING"

Call reset_running_state() wherever the rest of the runtime state is reset
(new session / seek / camera restart) so stale motion history doesn't
trigger false positives after a seek.
"""

from typing import Dict, Optional, Tuple

# Non-tunable protocol constants (state-shape / smoothing, not policy).
ALERT_COOLDOWN_SECONDS = 5.0        # don't re-log the same sprint every frame
DISPLAY_WINDOW_SECONDS = 3.0        # how long the "RUNNING" label stays on screen
EMA_ALPHA = 0.15                    # velocity smoothing (kills bbox jitter)

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


def _confidence_class(speed: float, threshold: float, direction_coherent: bool) -> str:
    """Bucket the running event by margin over threshold and direction coherence.
    A clean, sustained, directional run is "high"; a threshold-scraping event with
    non-coherent direction is "low". Never suppresses (this only labels)."""
    from confidence import classify
    # Speed margin as a normalised score: 0 at threshold, 1 at 2x threshold.
    margin = max(0.0, min(1.0, (speed - threshold) / max(0.1, threshold)))
    base = classify(margin, (0.25, 0.75))
    if not direction_coherent:
        # Cap at "medium" when direction is jittery, even if the speed is huge.
        return "medium" if base == "high" else base
    return base


def _is_edge_of_frame(cx: float, cy: float, frame_shape, margin_pct: float) -> bool:
    if frame_shape is None or margin_pct <= 0.0:
        return False
    try:
        h, w = int(frame_shape[0]), int(frame_shape[1])
    except Exception:
        return False
    if w <= 0 or h <= 0:
        return False
    mx = w * margin_pct
    my = h * margin_pct
    return cx < mx or cx > (w - mx) or cy < my or cy > (h - my)


def check_running(
    track_key,
    global_id: int,
    camera_id: Optional[int],
    bbox: Tuple[int, int, int, int],
    video_time: float,
    video_path: str = "",
    frame_shape=None,
) -> bool:
    """
    Updates per-track motion history. Returns True the moment running is
    newly detected for this track (edge-triggered + cooldown, so a sustained
    sprint logs one event rather than one per frame).

    ``frame_shape`` is optional. When passed as ``(H, W, ...)``, an edge-of-frame
    suppression kicks in: a track whose centre is within the configured margin
    of the frame boundary is skipped for running (bbox distortion inflates
    apparent speed at edges). Omitting it preserves the original behaviour.
    """
    import inference_config

    speed_thresh = inference_config.running_speed_threshold()
    min_samples = inference_config.running_min_samples()
    sustained_needed = inference_config.running_sustained_samples()
    direction_required = inference_config.running_direction_check()
    edge_margin = inference_config.running_edge_margin_pct()

    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    height = max(1.0, y2 - y1)

    history = _track_history.get(track_key)
    if history is None:
        _track_history[track_key] = {
            "prev": (cx, cy, height),
            "raw_prev": (cx, cy, height),
            "prev_time": video_time,
            "samples": 0,
            "sustained": 0,
            "last_vx": 0.0,
            "last_vy": 0.0,
            "last_alert_time": -1e9,
        }
        return False

    prev_time = float(history["prev_time"])
    dt = video_time - prev_time
    px, py, ph = history["prev"]
    raw_px, raw_py, raw_ph = history.get("raw_prev", (px, py, ph))

    # EMA-smooth the geometry (used for reported speed + direction coherence).
    scx = EMA_ALPHA * cx + (1.0 - EMA_ALPHA) * px
    scy = EMA_ALPHA * cy + (1.0 - EMA_ALPHA) * py
    sheight = EMA_ALPHA * height + (1.0 - EMA_ALPHA) * ph

    # SMOOTHED speed is what we report on the alert (kills reported jitter).
    speed = _speed_bbox_heights_per_sec(history["prev"], (scx, scy, sheight), dt)

    # RAW speed (unsmoothed prev -> unsmoothed curr) drives the sustained-frames
    # counter: EMA propagates a single spike over several subsequent frames, which
    # would otherwise let one jittery-bbox jump satisfy the sustained gate. Truly
    # raw speed drops back the very next frame if the motion was a one-off spike.
    raw_speed = _speed_bbox_heights_per_sec(
        (raw_px, raw_py, raw_ph), (cx, cy, height), dt
    )

    vx = (scx - px) / dt if dt > 0 else 0.0
    vy = (scy - py) / dt if dt > 0 else 0.0

    last_vx = float(history.get("last_vx", 0.0))
    last_vy = float(history.get("last_vy", 0.0))
    # Direction is coherent when successive velocity vectors point roughly the same
    # way (dot product > 0). The first sustained frame has no prior vector to compare
    # against; treat it as coherent so the check doesn't accidentally block startup.
    direction_dot = vx * last_vx + vy * last_vy
    direction_coherent = (direction_dot >= 0.0) or (last_vx == 0.0 and last_vy == 0.0)

    history["prev"] = (scx, scy, sheight)
    history["raw_prev"] = (cx, cy, height)
    history["prev_time"] = video_time
    history["samples"] = int(history.get("samples", 0)) + 1
    history["last_vx"] = vx
    history["last_vy"] = vy

    over_threshold = raw_speed >= speed_thresh
    if over_threshold and (direction_coherent or not direction_required):
        history["sustained"] = int(history.get("sustained", 0)) + 1
    else:
        history["sustained"] = 0

    newly_detected = False
    if (
        history["sustained"] >= sustained_needed
        and history["samples"] >= min_samples
        and not _is_edge_of_frame(scx, scy, frame_shape, edge_margin)
    ):
        _active_running[global_id] = video_time + DISPLAY_WINDOW_SECONDS
        if video_time - float(history["last_alert_time"]) >= ALERT_COOLDOWN_SECONDS:
            history["last_alert_time"] = video_time
            newly_detected = True
            confidence_class = _confidence_class(speed, speed_thresh, direction_coherent)
            _write_running_event(
                global_id, camera_id, speed, video_time, video_path,
                confidence_class=confidence_class,
            )

    return newly_detected


def _write_running_event(
    global_id,
    camera_id,
    speed,
    video_time,
    video_path,
    confidence_class: str = "medium",
) -> None:
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
        f"speed={speed:.2f} bbox-heights/sec confidence={confidence_class}"
    )
