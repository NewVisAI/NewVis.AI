"""
Fall and slip/hazard detection — hybrid of the two project lineages.

Built with NO new ML model to fit CPU budgets: it works purely on the
bounding boxes the existing detector/tracker pipeline already produces.

Detection logic (transition-based, from the verified SCI implementation):
a fall is a *transition* observed over a short sliding window —
  1. the person WAS upright (width/height below ASPECT_RATIO_STANDING_MAX),
  2. is NOW horizontal (width/height above ASPECT_RATIO_FALLEN_MIN),
  3. and their centroid dropped by at least VERTICAL_DROP_RATIO of their
     own body height within that window.
Requiring the standing→fallen transition avoids false positives on people
who are already sitting/lying when first detected.

Robustness additions (from the HackathonPro implementation):
  - EMA smoothing of bbox geometry before it enters the window, so jittery
    low-confidence boxes don't fake a transition.
  - Cooldown measured in video-time seconds (FPS-independent), per identity.

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
ALERT_COOLDOWN_SECONDS = 5.0      # per-identity, FPS-independent
DISPLAY_WINDOW_SECONDS = 3.0      # how long the "FALLEN" label stays on screen
EMA_ALPHA = 0.25                  # smoothing for jittery low-quality bounding boxes

_history: Dict[Tuple, "collections.deque"] = {}
_smoothed: Dict[Tuple, Dict[str, float]] = {}
_last_fall_time: Dict[int, float] = {}
_active_falls: Dict[int, float] = {}  # global_id -> video_time it should stop showing


def reset_fall_state() -> None:
    _history.clear()
    _smoothed.clear()
    _last_fall_time.clear()
    _active_falls.clear()


def reset_identity(track_key) -> None:
    _history.pop(track_key, None)
    _smoothed.pop(track_key, None)


def is_currently_fallen(global_id: int, video_time: float) -> bool:
    expiry = _active_falls.get(global_id)
    return expiry is not None and video_time <= expiry


def _bbox_metrics(bbox: Tuple[int, int, int, int]) -> Tuple[float, float, float]:
    x1, y1, x2, y2 = bbox
    width = max(1.0, float(x2 - x1))
    height = max(1.0, float(y2 - y1))
    centroid_y = (y1 + y2) / 2.0
    return width / height, centroid_y, height


def check_fall(
    track_key,
    global_id: int,
    bbox: Tuple[int, int, int, int],
    video_time: float,
) -> Optional[Dict]:
    """
    Feed one frame's bbox for a tracked person.
    Returns a details dict the moment a fall is newly detected, else None.
    The caller is responsible for routing it into the alerts pipeline
    (alerts.raise_fall_alert) so it gets a snapshot + principal notification.
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

    was_standing = earliest["ratio"] < ASPECT_RATIO_STANDING_MAX
    now_horizontal = latest["ratio"] > ASPECT_RATIO_FALLEN_MIN
    vertical_drop_ratio = (latest["cy"] - earliest["cy"]) / earliest["h"]
    dropped_significantly = vertical_drop_ratio > VERTICAL_DROP_RATIO

    if not (was_standing and now_horizontal and dropped_significantly):
        return None

    _active_falls[global_id] = video_time + DISPLAY_WINDOW_SECONDS

    last_fall = _last_fall_time.get(global_id)
    if last_fall is not None and (video_time - last_fall) < ALERT_COOLDOWN_SECONDS:
        return None

    _last_fall_time[global_id] = video_time
    return {
        "aspect_ratio_before": round(earliest["ratio"], 2),
        "aspect_ratio_after": round(latest["ratio"], 2),
        "vertical_drop_ratio": round(vertical_drop_ratio, 2),
        "window_seconds": round(latest["time"] - earliest["time"], 2),
    }
