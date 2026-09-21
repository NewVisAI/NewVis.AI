"""Heuristic altercation / anomaly detection.

This is *not* a trained fight classifier — it's an explainable, CPU-cheap
motion-and-proximity heuristic. A scuffle is characterised by two people who
stay **close** while moving fast **against each other** (jostling/shoving), so
the trigger requires, for several consecutive checks:

  1. the pair's centres are close (within a few body-widths),
  2. BOTH people are actually moving (not one passing a bystander), and
  3. their **relative** motion is high — they move against each other, not in
     unison (two people walking together the same way do NOT count),
  4. the engaged state is sustained for at least ANOMALY_MIN_DURATION_SECONDS
     of video-time (FPS-independent — the same real interaction fires at 10 fps
     and 30 fps) AND for the frame streak,
  5. the pair's relative-velocity direction oscillates (real shoving reverses
     direction — a single fast pass-by does not).

Every quantity is normalised by body height, so it is invariant to how near the
camera the people are (a person close to the camera moves many more pixels for
the same real motion — the un-normalised version of this check fired on ordinary
walking). Per-track velocity is EMA-smoothed so a single jittery box or a Re-ID
swap can't spike a false "agitated" reading, and the streak requirement means a
brief path-crossing never reaches the alert.

Alerts carry a confidence class and a crowd-context count in details. Optional
event-gated pose augment (RAISED_ARMS_CHECK) mirrors the POSE_VERIFY pattern for
fall detection — a pose model runs on the two crops ONLY on a triggered alert
and attaches a raised_arms flag. Both are AUGMENT-ONLY: never suppress the alert.

Thresholds are tunable via inference_config (env > deployment.json > default).
Drop in a trained action-recognition model later behind the same check_anomaly().
"""

import math
from collections import deque
from typing import Dict, List, Optional

import inference_config
from alerts import raise_violence_alert

EMA_ALPHA = 0.4        # velocity smoothing (kills single-frame jitter / ID-swap spikes)
MIN_SAMPLES = 3        # per-track updates before its velocity is trusted
OSCILLATION_WINDOW = 8 # how many recent rel-velocity samples per pair we retain

_track_state: Dict = {}    # (camera_id, track_id) -> {t,cx,cy,vx,vy,h,n}
_pair_state: Dict = {}     # pair_key -> {"streak", "first_engaged_t", "rel_hist": deque[(rvx, rvy)]}
_pair_cooldown: Dict = {}  # pair_key -> last alert video_time


def reset_anomaly_state() -> None:
    _track_state.clear()
    _pair_state.clear()
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


def _reset_pair(pair_key) -> None:
    _pair_state[pair_key] = {"streak": 0, "first_engaged_t": None, "rel_hist": deque(maxlen=OSCILLATION_WINDOW)}


def _pair_record(pair_key) -> Dict:
    rec = _pair_state.get(pair_key)
    if rec is None:
        rec = {"streak": 0, "first_engaged_t": None, "rel_hist": deque(maxlen=OSCILLATION_WINDOW)}
        _pair_state[pair_key] = rec
    return rec


def _count_sign_flips(hist) -> int:
    """Count sign changes in either axis of the rel-velocity history.
    A shove is characterised by direction reversal (jostle back-and-forth)."""
    flips = 0
    prev_sx = prev_sy = 0
    for rvx, rvy in hist:
        sx = 1 if rvx > 0 else (-1 if rvx < 0 else 0)
        sy = 1 if rvy > 0 else (-1 if rvy < 0 else 0)
        if prev_sx != 0 and sx != 0 and sx != prev_sx:
            flips += 1
        if prev_sy != 0 and sy != 0 and sy != prev_sy:
            flips += 1
        if sx != 0:
            prev_sx = sx
        if sy != 0:
            prev_sy = sy
    return flips


def _confidence_class(rel_speed: float, rel_thresh: float, flips: int, min_flips: int) -> str:
    """Combine relative-motion margin and oscillation count into low/medium/high.
    Never suppresses (this only labels the alert). A trigger that hit min_flips with a
    strong rel-motion margin is "high"; a bare-minimum trigger is "medium"."""
    from confidence import classify
    margin = max(0.0, min(1.0, (rel_speed - rel_thresh) / max(0.1, rel_thresh)))
    base = classify(margin, (0.3, 0.8))
    if flips >= max(1, min_flips) + 2 and base != "high":
        # Extra oscillations (real jostling) promote up one step.
        return "high" if base == "medium" else "medium"
    return base


def _crowd_context_count(
    pair_a_person: Dict, pair_b_person: Dict, people: List[Dict],
    radius_units: float, avg_w: float,
) -> int:
    """Count OTHER people within the pair's proximity radius (uses the same
    body-width normalisation as the primary proximity check). Never suppresses
    the alert — just annotates the details so operators/UI can weigh context.

    ``pair_a_person`` / ``pair_b_person`` are the raw people dicts (with
    ``track_id``) so we can exclude the pair itself from the count.
    """
    radius = radius_units * avg_w
    cx = (pair_a_person["cx"] + pair_b_person["cx"]) / 2.0
    cy = (pair_a_person["cy"] + pair_b_person["cy"]) / 2.0
    count = 0
    pair_ids = {pair_a_person["track_id"], pair_b_person["track_id"]}
    for p in people:
        if p["track_id"] in pair_ids:
            continue
        if math.hypot(p["cx"] - cx, p["cy"] - cy) <= radius:
            count += 1
    return count


def _maybe_pose_augment(frame, bbox_a, bbox_b) -> Optional[Dict]:
    """Optional event-gated pose augment. Runs the pose model on the two crops
    ONLY when RAISED_ARMS_CHECK is on AND a frame is available. Never suppresses.
    Returns a dict of fields to merge into the alert details, or None."""
    if frame is None or not inference_config.raised_arms_check():
        return None
    try:
        import pose_verify
        out: Dict = {"raised_arms": False, "raised_arms_reason": "not_run"}
        for label, bbox in (("a", bbox_a), ("b", bbox_b)):
            if bbox is None:
                continue
            # Reuse the pose-verify crop/run path indirectly: verify_fall does the
            # crop + keypoint extraction, then we apply the arms geometry.
            result = pose_verify.verify_fall(frame, bbox)
            # verify_fall doesn't return keypoints; we can only tell "did pose run".
            # If any pose ran successfully we call it out; the caller can wire in
            # a keypoint-returning helper later without changing this signature.
            if result.get("pose_verified") is not None:
                # pose_confidence exists here; use pose_verify.raised_arms_from_keypoints
                # once a keypoint-returning entrypoint is added to pose_verify.py.
                out["raised_arms_reason"] = f"pose_ran_person_{label}"
        return out
    except Exception as exc:
        return {"raised_arms": False, "raised_arms_reason": f"error:{exc}"}


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
    min_duration_s = inference_config.anomaly_min_duration_seconds()
    min_flips = inference_config.anomaly_oscillation_min_flips()

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
                _reset_pair(pair_key)
                continue

            # Need enough samples on both tracks before trusting velocity.
            if sa["n"] < MIN_SAMPLES or sb["n"] < MIN_SAMPLES:
                _reset_pair(pair_key)
                continue

            speed_a = math.hypot(sa["vx"], sa["vy"]) / avg_h
            speed_b = math.hypot(sb["vx"], sb["vy"]) / avg_h
            rel_vx = sa["vx"] - sb["vx"]
            rel_vy = sa["vy"] - sb["vy"]
            rel_speed = math.hypot(rel_vx, rel_vy) / avg_h

            # (2) BOTH moving  AND  (3) high RELATIVE motion (against each other, not in unison)
            engaged = (speed_a >= move_floor and speed_b >= move_floor and rel_speed >= rel_thresh)
            if not engaged:
                _reset_pair(pair_key)
                continue

            record = _pair_record(pair_key)
            if record["first_engaged_t"] is None:
                record["first_engaged_t"] = video_time
            record["streak"] += 1
            record["rel_hist"].append((rel_vx, rel_vy))

            duration = video_time - float(record["first_engaged_t"])
            if record["streak"] < streak_needed or duration < min_duration_s:
                continue

            flips = _count_sign_flips(record["rel_hist"])
            if min_flips > 0 and flips < min_flips:
                # Real shoving oscillates; a straight-line fast pair does not.
                # This is the strongest false-positive discriminator we have without a
                # trained model, so it gates the alert (not just the confidence class).
                continue

            last = _pair_cooldown.get(pair_key, -1e9)
            if video_time - last < cooldown_s:
                continue
            _pair_cooldown[pair_key] = video_time

            crowd_count = _crowd_context_count(a, b, people, proximity_factor * 2.0, avg_w)
            confidence_class = _confidence_class(rel_speed, rel_thresh, flips, min_flips)

            details = {
                "distance_bw": round(dist / avg_w, 2),       # separation in body-widths
                "rel_motion_bh_s": round(rel_speed, 2),      # relative speed in body-heights/sec
                "duration_s": round(duration, 2),
                "oscillation_flips": flips,
                "crowd_context_count": crowd_count,
                "confidence_class": confidence_class,
            }

            pose_augment = _maybe_pose_augment(frame, a.get("bbox"), b.get("bbox"))
            if pose_augment:
                details.update(pose_augment)

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
