"""
Fall and slip/hazard detection — hybrid of the two project lineages.

Built with NO new ML model to fit CPU budgets: it works purely on the
bounding boxes the existing detector/tracker pipeline already produces.

Detection logic (transition-based, from the verified SCI implementation):
a fall is a *transition* observed over a short sliding window —
  1. the person WAS upright (aspect ratio below ASPECT_RATIO_STANDING_MAX for
     a majority of the pre-transition half of the window, so a sitting person
     whose bbox momentarily narrows cannot fake the transition),
  2. is NOW horizontal (width/height above ASPECT_RATIO_FALLEN_MIN),
  3. and their centroid dropped by at least VERTICAL_DROP_RATIO of their
     own body height within that window.
Requiring the standing→fallen transition avoids false positives on people
who are already sitting/lying when first detected.

Robustness additions (from the HackathonPro implementation):
  - EMA smoothing of bbox geometry before it enters the window, so jittery
    low-confidence boxes don't fake a transition.
  - Cooldown measured in video-time seconds (FPS-independent), per identity.

Confidence class (attached to every fired fall):
  "medium" — bbox transition matched cleanly, no other signal
  "high"   — bbox transition + pose torso confirms horizontal
             OR + post-fall stillness confirms person stayed down
             (both can be checked with stillness_status() after the fact)
  "low"    — bbox transition marginal (drop ratio only just over threshold)
Safety rule: confidence never suppresses. A "low" fall still fires.

Usage from app.py's per-track loop:

    from fall_detector import check_fall, is_currently_fallen, reset_fall_state

    fall_details = check_fall(track_key, global_id, bbox, video_time)
    if fall_details:
        alerts.raise_fall_alert(..., details=fall_details)
    if is_currently_fallen(global_id, video_time):
        label += " | FALLEN"

Call reset_fall_state() wherever the rest of the runtime state is reset
(new session / seek) so stale bbox history can't trigger false falls.
"""

import collections
from typing import Dict, Optional, Tuple

WINDOW_SIZE = 8
MIN_HISTORY_FOR_FALL = 5
ASPECT_RATIO_STANDING_MAX = 0.8   # width/height below this looks upright
ASPECT_RATIO_FALLEN_MIN = 1.2     # width/height above this looks horizontal
VERTICAL_DROP_RATIO = 0.5         # centroid must drop at least this many bbox-heights
STANDING_MAJORITY = 0.6           # fraction of pre-transition samples that must be upright
ALERT_COOLDOWN_SECONDS = 5.0      # per-identity, FPS-independent
DISPLAY_WINDOW_SECONDS = 3.0      # how long the "FALLEN" label stays on screen
EMA_ALPHA = 0.25                  # smoothing for jittery low-quality bounding boxes

# Confidence thresholds — how the bbox drop ratio alone maps to a class
# before pose/stillness augmentation. Both signals can promote up one step.
DROP_RATIO_HIGH = 0.9             # very clear fall = start at "high"
DROP_RATIO_MEDIUM = VERTICAL_DROP_RATIO  # meets threshold = "medium"

_history: Dict[Tuple, "collections.deque"] = {}
_smoothed: Dict[Tuple, Dict[str, float]] = {}
_last_fall_time: Dict[int, float] = {}
# global_id -> {"expiry": float, "fall_time": float, "track_key": tuple, "fall_cy": float, "fall_h": float, "confirmed": Optional[bool]}
_active_falls: Dict[int, Dict[str, object]] = {}


def reset_fall_state() -> None:
    _history.clear()
    _smoothed.clear()
    _last_fall_time.clear()
    _active_falls.clear()


def reset_identity(track_key) -> None:
    _history.pop(track_key, None)
    _smoothed.pop(track_key, None)


def is_currently_fallen(global_id: int, video_time: float) -> bool:
    record = _active_falls.get(global_id)
    if record is None:
        return False
    expiry = record.get("expiry")
    return isinstance(expiry, (int, float)) and video_time <= float(expiry)


def _bbox_metrics(bbox: Tuple[int, int, int, int]) -> Tuple[float, float, float]:
    x1, y1, x2, y2 = bbox
    width = max(1.0, float(x2 - x1))
    height = max(1.0, float(y2 - y1))
    centroid_y = (y1 + y2) / 2.0
    return width / height, centroid_y, height


def _confidence_from_drop(drop_ratio: float) -> str:
    from confidence import classify
    return classify(drop_ratio, (DROP_RATIO_MEDIUM, DROP_RATIO_HIGH))


def check_fall(
    track_key,
    global_id: int,
    bbox: Tuple[int, int, int, int],
    video_time: float,
    frame=None,
) -> Optional[Dict]:
    """
    Feed one frame's bbox for a tracked person.
    Returns a details dict the moment a fall is newly detected, else None.
    The caller is responsible for routing it into the alerts pipeline
    (alerts.raise_fall_alert) so it gets a snapshot + principal notification.

    ``frame`` is optional. When supplied AND ``POSE_VERIFY`` is enabled, a pose model
    runs on the person crop of a newly-detected fall to attach a keypoint-based
    confidence (pose_verified / pose_confidence) to the returned details. This only
    ever *augments* the fall — it never suppresses one (safety) — so callers that pass
    no frame, and the whole existing behaviour, are unchanged.
    """
    ratio, centroid_y, height = _bbox_metrics(bbox)

    # EMA smoothing before the sample enters the sliding window.
    smoothed = _smoothed.get(track_key)
    if smoothed is None:
        smoothed = {"ratio": ratio, "cy": centroid_y, "h": height}
    else:
        smoothed = {
            "ratio": EMA_ALPHA * ratio + (1.0 - EMA_ALPHA) * smoothed["ratio"],
            "cy": EMA_ALPHA * centroid_y + (1.0 - EMA_ALPHA) * smoothed["cy"],
            "h": EMA_ALPHA * height + (1.0 - EMA_ALPHA) * smoothed["h"],
        }
    _smoothed[track_key] = smoothed

    history = _history.setdefault(track_key, collections.deque(maxlen=WINDOW_SIZE))
    history.append(
        {"time": video_time, "ratio": smoothed["ratio"], "cy": smoothed["cy"], "h": smoothed["h"]}
    )

    if len(history) < MIN_HISTORY_FOR_FALL:
        return None

    earliest = history[0]
    latest = history[-1]

    # Was-standing gate: majority of the FIRST HALF of the window must be upright.
    # This kills the "sitting person whose bbox narrows for one frame" false positive
    # that the old single-sample check could not catch.
    half = max(1, len(history) // 2)
    upright_count = sum(1 for s in list(history)[:half] if s["ratio"] < ASPECT_RATIO_STANDING_MAX)
    was_standing = (upright_count / half) >= STANDING_MAJORITY

    now_horizontal = latest["ratio"] > ASPECT_RATIO_FALLEN_MIN
    vertical_drop_ratio = (latest["cy"] - earliest["cy"]) / earliest["h"]
    dropped_significantly = vertical_drop_ratio > VERTICAL_DROP_RATIO

    if not (was_standing and now_horizontal and dropped_significantly):
        return None

    last_fall = _last_fall_time.get(global_id)
    if last_fall is not None and (video_time - last_fall) < ALERT_COOLDOWN_SECONDS:
        # Cooldown: still note the person is currently fallen (extend display window)
        # but do not re-fire the alert.
        record = _active_falls.get(global_id, {})
        record["expiry"] = video_time + DISPLAY_WINDOW_SECONDS
        _active_falls[global_id] = record
        return None

    _last_fall_time[global_id] = video_time
    _active_falls[global_id] = {
        "expiry": video_time + DISPLAY_WINDOW_SECONDS,
        "fall_time": video_time,
        "track_key": track_key,
        "fall_cy": latest["cy"],
        "fall_h": max(1.0, latest["h"]),
        "confirmed": None,
    }

    details = {
        "aspect_ratio_before": round(earliest["ratio"], 2),
        "aspect_ratio_after": round(latest["ratio"], 2),
        "vertical_drop_ratio": round(vertical_drop_ratio, 2),
        "window_seconds": round(latest["time"] - earliest["time"], 2),
        "confidence_class": _confidence_from_drop(vertical_drop_ratio),
    }

    # Event-gated pose verification (POSE_VERIFY): only now — on a rare, newly-detected
    # fall — optionally run pose on the crop to attach a keypoint-based confidence.
    # It augments the details; it must never suppress the fall (a safety alert stays).
    # Kept lazy so fall_detector has no hard dependency on the pose stack.
    if frame is not None:
        try:
            import inference_config
            if inference_config.pose_verify():
                import pose_verify
                pose_details = pose_verify.verify_fall(frame, bbox)
                details.update(pose_details)
                # Pose torso confirm can promote medium -> high (never demote).
                if pose_details.get("pose_verified") is True and details["confidence_class"] == "medium":
                    details["confidence_class"] = "high"
        except Exception:
            pass  # pose is best-effort; the fall alert stands regardless

    return details


def stillness_status(global_id: int, video_time: float) -> Optional[Dict]:
    """Post-fall stillness check.

    After a fall has fired for ``global_id``, once ``FALL_STILLNESS_SECONDS`` of
    video-time have elapsed we compare the most recent smoothed bbox centroid to
    the centroid at the moment of the fall. If the person stayed within
    ``fall_stillness_movement_ratio`` body-heights, stillness is confirmed and the
    fall is upgraded to "high" confidence. If they moved beyond that (got back up,
    ran off), stillness is refuted and confidence is downgraded to "low".

    Returns ``None`` while the fall is not yet due for evaluation, and returns the
    same dict again on repeat calls after evaluation (caller can dedup by
    ``confirmed`` field). The caller wires this from the live per-track loop; it
    reads only existing state, no new per-track dict.
    """
    record = _active_falls.get(global_id)
    if record is None:
        return None
    if record.get("confirmed") is not None:
        return {
            "stillness_confirmed": bool(record["confirmed"]),
            "confidence_class": str(record.get("post_confidence_class", "medium")),
        }

    import inference_config
    window = inference_config.fall_stillness_seconds()
    move_ratio_limit = inference_config.fall_stillness_movement_ratio()

    fall_time = float(record.get("fall_time", video_time))
    if (video_time - fall_time) < window:
        return None

    track_key = record.get("track_key")
    smoothed = _smoothed.get(track_key) if track_key is not None else None
    if smoothed is None:
        # Track was lost during the window — treat as unknown; leave the alert as-is.
        record["confirmed"] = False
        record["post_confidence_class"] = "medium"
        return {"stillness_confirmed": False, "confidence_class": "medium"}

    fall_cy = float(record.get("fall_cy", smoothed["cy"]))
    fall_h = float(record.get("fall_h", max(1.0, smoothed["h"])))
    movement = abs(smoothed["cy"] - fall_cy) / max(1.0, fall_h)

    still = movement <= move_ratio_limit
    if still:
        record["confirmed"] = True
        record["post_confidence_class"] = "high"
    else:
        record["confirmed"] = False
        record["post_confidence_class"] = "low"
    return {
        "stillness_confirmed": still,
        "confidence_class": record["post_confidence_class"],
    }
