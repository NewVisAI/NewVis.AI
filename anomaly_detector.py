"""Heuristic altercation / anomaly detection.

This is *not* a trained fight classifier — it's an explainable, CPU-cheap
motion-and-proximity heuristic. A scuffle is characterised by two people who
stay **close** while moving fast **against each other** (jostling/shoving), so
the trigger requires, for several consecutive checks:

  1. the pair's centres are close (within a few body-widths),
  2. BOTH people are actually moving (not one passing a bystander), and
  3. their **relative** motion is high — they move against each other, not in
     unison (two people walking together the same way do NOT count).

Every quantity is normalised by body height, so it is invariant to how near the
camera the people are (a person close to the camera moves many more pixels for
the same real motion — the un-normalised version of this check fired on ordinary
walking). Per-track velocity is EMA-smoothed so a single jittery box or a Re-ID
swap can't spike a false "agitated" reading, and the streak requirement means a
brief path-crossing never reaches the alert.

Thresholds are tunable via inference_config (env > deployment.json > default).
Drop in a trained action-recognition model later behind the same check_anomaly().
"""

import math
from typing import Dict, List, Optional

import inference_config
from alerts import raise_violence_alert

EMA_ALPHA = 0.4        # velocity smoothing (kills single-frame jitter / ID-swap spikes)
MIN_SAMPLES = 3        # per-track updates before its velocity is trusted

_track_state: Dict = {}    # (camera_id, track_id) -> {t,cx,cy,vx,vy,h,n}
_pair_streak: Dict = {}    # pair_key -> consecutive close+engaged checks
_pair_cooldown: Dict = {}  # pair_key -> last alert video_time


def reset_anomaly_state() -> None:
    _track_state.clear()
    _pair_streak.clear()
    _pair_cooldown.clear()


def _update_track(key, t: float, cx: float, cy: float, h: float) -> dict:
    """Update a track's EMA-smoothed velocity (pixels/sec) and return its state."""
    prev = _track_state.get(key)
    if prev is None:
        st = {"t": t, "cx": cx, "cy": cy, "vx": 0.0, "vy": 0.0, "h": h, "n": 1}
    else:
        dt = max(1e-3, t - prev["t"])
        ivx = (cx - prev["cx"]) / dt
        ivy = (cy - prev["cy"]) / dt
        st = {
            "t": t, "cx": cx, "cy": cy,
            "vx": EMA_ALPHA * ivx + (1.0 - EMA_ALPHA) * prev["vx"],
            "vy": EMA_ALPHA * ivy + (1.0 - EMA_ALPHA) * prev["vy"],
            "h": h, "n": prev["n"] + 1,
        }
    _track_state[key] = st
    return st


def check_anomaly(camera_id, people: List[Dict], video_time: float,
                  video_path: str, frame=None, frame_number: Optional[int] = None) -> List[Dict]:
    """`people` = list of dicts: track_id, global_id, cx, cy, w, h, bbox.
    Returns a list of fired-alert descriptors (also raised through alerts)."""
    if not inference_config.anomaly_detection():
        return []

    proximity_factor = inference_config.anomaly_proximity_factor()
    move_floor = inference_config.anomaly_move_floor()
    rel_thresh = inference_config.anomaly_rel_motion_thresh()
    streak_needed = inference_config.anomaly_streak()
    cooldown_s = inference_config.anomaly_cooldown_s()

    # Update every track's smoothed velocity first.
    states = {}
    for p in people:
        key = (camera_id, p["track_id"])
        states[p["track_id"]] = _update_track(key, video_time, p["cx"], p["cy"], max(1.0, float(p["h"])))

    fired: List[Dict] = []
    n = len(people)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = people[i], people[j]
            pair_key = (camera_id, min(a["global_id"], b["global_id"]), max(a["global_id"], b["global_id"]))
            sa, sb = states[a["track_id"]], states[b["track_id"]]

            # Normalise everything by body height -> distance/resolution invariant.
            avg_h = max(1.0, (sa["h"] + sb["h"]) / 2.0)
            avg_w = max(1.0, (a["w"] + b["w"]) / 2.0)

            # (1) close together?
            dist = math.hypot(a["cx"] - b["cx"], a["cy"] - b["cy"])
            if dist > proximity_factor * avg_w:
                _pair_streak[pair_key] = 0
                continue

            # Need enough samples on both tracks before trusting velocity.
            if sa["n"] < MIN_SAMPLES or sb["n"] < MIN_SAMPLES:
                _pair_streak[pair_key] = 0
                continue

            speed_a = math.hypot(sa["vx"], sa["vy"]) / avg_h
            speed_b = math.hypot(sb["vx"], sb["vy"]) / avg_h
            rel_speed = math.hypot(sa["vx"] - sb["vx"], sa["vy"] - sb["vy"]) / avg_h

            # (2) BOTH moving  AND  (3) high RELATIVE motion (against each other, not in unison)
            engaged = (speed_a >= move_floor and speed_b >= move_floor and rel_speed >= rel_thresh)
            if not engaged:
                _pair_streak[pair_key] = 0
                continue

            _pair_streak[pair_key] = _pair_streak.get(pair_key, 0) + 1
            if _pair_streak[pair_key] < streak_needed:
                continue

            last = _pair_cooldown.get(pair_key, -1e9)
            if video_time - last < cooldown_s:
                continue
            _pair_cooldown[pair_key] = video_time

            details = {
                "distance_bw": round(dist / avg_w, 2),       # separation in body-widths
                "rel_motion_bh_s": round(rel_speed, 2),      # relative speed in body-heights/sec
            }
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
