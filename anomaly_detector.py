"""Heuristic violence / anomaly detection.

This is *not* a trained fight classifier — it's an explainable, CPU-cheap
motion-and-proximity heuristic: when two people are very close together AND
both are moving with high, agitated speed for several consecutive checks,
that pattern (a scuffle/shoving) is flagged as a possible altercation.

Deliberately conservative so calm footage produces (near) zero alerts. Drop in
a trained action-recognition model later behind the same check_anomaly() call.
"""

import math
from collections import defaultdict, deque
from typing import Dict, List, Optional

from alerts import raise_violence_alert

# Tunables (pixel units at source resolution). Conservative on purpose.
PROXIMITY_FACTOR = 1.4       # centres within 1.4 * avg body width = "close"
SPEED_HIGH_PX_S = 300.0      # per-person speed considered agitated
ENERGY_HIGH_PX_S = 750.0     # combined speed of the pair
STREAK_NEEDED = 3            # consecutive close+agitated checks before firing
COOLDOWN_SECONDS = 8.0       # min gap between alerts for the same pair

_track_hist: Dict = {}       # (camera_id, track_id) -> deque[(t, cx, cy)]
_pair_streak: Dict = defaultdict(int)
_pair_cooldown: Dict = {}


def reset_anomaly_state() -> None:
    _track_hist.clear()
    _pair_streak.clear()
    _pair_cooldown.clear()


def _speed(hist: deque) -> float:
    if len(hist) < 2:
        return 0.0
    (t0, x0, y0), (t1, x1, y1) = hist[-2], hist[-1]
    dt = max(1e-3, t1 - t0)
    return math.hypot(x1 - x0, y1 - y0) / dt


def check_anomaly(camera_id, people: List[Dict], video_time: float,
                  video_path: str, frame=None, frame_number: Optional[int] = None) -> List[Dict]:
    """`people` = list of dicts: track_id, global_id, cx, cy, w, h, bbox.
    Returns a list of fired-alert descriptors (also raised through alerts)."""
    for p in people:
        key = (camera_id, p["track_id"])
        hist = _track_hist.setdefault(key, deque(maxlen=6))
        hist.append((video_time, p["cx"], p["cy"]))

    fired: List[Dict] = []
    n = len(people)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = people[i], people[j]
            dist = math.hypot(a["cx"] - b["cx"], a["cy"] - b["cy"])
            avg_w = max(1.0, (a["w"] + b["w"]) / 2.0)
            pair_key = (camera_id, min(a["global_id"], b["global_id"]), max(a["global_id"], b["global_id"]))

            if dist > PROXIMITY_FACTOR * avg_w:
                _pair_streak[pair_key] = 0
                continue

            sa = _speed(_track_hist[(camera_id, a["track_id"])])
            sb = _speed(_track_hist[(camera_id, b["track_id"])])
            agitated = (sa > SPEED_HIGH_PX_S and sb > SPEED_HIGH_PX_S) or (sa + sb > ENERGY_HIGH_PX_S)

            if not agitated:
                _pair_streak[pair_key] = 0
                continue

            _pair_streak[pair_key] += 1
            if _pair_streak[pair_key] < STREAK_NEEDED:
                continue

            last = _pair_cooldown.get(pair_key, -1e9)
            if video_time - last < COOLDOWN_SECONDS:
                continue
            _pair_cooldown[pair_key] = video_time

            details = {"distance_px": round(dist, 1), "energy": round(sa + sb, 1)}
            raise_violence_alert(
                camera_id=camera_id,
                global_id=a["global_id"],
                other_global_id=b["global_id"],
                track_id=a["track_id"],
                video_path=video_path,
                frame_number=frame_number,
                frame=frame,
                bbox=a.get("bbox"),
                details=details,
            )
            fired.append({"pair": pair_key, **details})
    return fired
