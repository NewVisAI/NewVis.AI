"""
Clip metadata for one-click jump-to-footage playback.

Given a search/event/alert result, this computes everything the UI needs to
jump straight to the right moment in the source video and to explain the hit:

  - why_logged      : plain-English reason the event was recorded
  - logged_at       : when it was logged (ISO timestamp)
  - clip_start_seconds / clip_end_seconds / clip_duration_seconds
                      : where the clip sits in the source video (with a short
                        pre/post-roll pad so the moment isn't clipped)
  - details         : camera, zone, global id, object type, dwell duration
  - video_url       : a byte-range-seekable URL the browser <video> can seek to

Keeping this in one place means the CLI, the REST API and the dashboard all
describe a clip the same way.
"""

import os
from typing import Any, Dict, List, Optional

import cv2

PRE_ROLL_SECONDS = 3.0
POST_ROLL_SECONDS = 3.0
DEFAULT_FPS = 29.97

_fps_cache: Dict[str, float] = {}

_WHY = {
    "leaving": "Tracked through zone (session ended)",
    "entering": "Entered zone",
    "staying": "Extended dwell in zone",
    "occupancy_alert": "Zone occupancy exceeded its limit (crowding)",
    "dress_code_violation": "Dress-code violation (non-uniform clothing)",
    "running_detected": "Running detected",
    "fall_detected": "Possible fall detected",
    "restricted_zone_entry": "Entered a restricted zone (intrusion)",
    "after_hours_entry": "Entry outside allowed hours / non-school day",
}


def _fps_for(video_path: Optional[str]) -> float:
    if not video_path:
        return DEFAULT_FPS
    if video_path in _fps_cache:
        return _fps_cache[video_path]
    fps = DEFAULT_FPS
    try:
        if os.path.exists(video_path):
            cap = cv2.VideoCapture(video_path)
            if cap.isOpened():
                value = cap.get(cv2.CAP_PROP_FPS)
                if value and value > 0:
                    fps = float(value)
            cap.release()
    except Exception:
        pass
    _fps_cache[video_path] = fps
    return fps


def why_logged(event: Dict[str, Any]) -> str:
    event_type = event.get("event_type") or event.get("alert_type") or ""
    base = _WHY.get(event_type)
    if base is None:
        base = event_type.replace("_", " ").title() if event_type else "Tracked event"

    # Note: a long dwell keeps its neutral "tracked through zone" reason — we log
    # the dwell time but no longer label it "loitering".
    return base


def _seconds_from_frame(frame: Optional[int], fps: float) -> Optional[float]:
    if frame is None:
        return None
    try:
        return round(int(frame) / max(1.0, fps), 2)
    except (TypeError, ValueError):
        return None


def build_clip(event: Dict[str, Any], video_url_base: str = "/api/clip/video") -> Dict[str, Any]:
    video_path = event.get("video_path")
    fps = _fps_for(video_path)

    frame_start = event.get("frame_start")
    frame_end = event.get("frame_end")
    frame_number = event.get("frame_number")

    # Anchor the clip on the moment the event was actually LOGGED — for a session
    # ("leaving") that's the exit; for an instant alert it's the frame it fired.
    # `video_time` records exactly that moment for every event type, so prefer it.
    # Fall back to the logged frame, then the session end, then its start. Using
    # frame_start was the bug: anyone already on-screen when recording began has
    # frame_start == 0, so their clip opened at the top of the video instead of at
    # the event. (Alert rows also store frame_* as 0 but carry the real
    # video_time — hence video_time takes precedence over a 0 frame.)
    anchor_raw = None
    video_time = event.get("video_time")
    if video_time is not None:
        try:
            value = float(video_time)
            if value > 0:
                anchor_raw = round(value, 2)
        except (TypeError, ValueError):
            pass

    if anchor_raw is None:
        anchor_frame = frame_number
        if anchor_frame is None:
            anchor_frame = frame_end
        if anchor_frame is None:
            anchor_frame = frame_start
        anchor_raw = _seconds_from_frame(anchor_frame, fps)

    if anchor_raw is None:
        anchor_raw = 0.0

    # Event window: a short lead-in before the logged moment and a short tail
    # after it, so playback jumps straight to the event.
    clip_start = max(0.0, round(anchor_raw - PRE_ROLL_SECONDS, 2))
    clip_end = round(anchor_raw + POST_ROLL_SECONDS, 2)
    if clip_end < clip_start:
        clip_end = clip_start
    clip_duration = round(clip_end - clip_start, 2)

    logged_at = (
        event.get("timestamp")
        or event.get("exit_time")
        or event.get("entry_time")
        or event.get("display_value")
    )

    zone_id = event.get("zone_id")
    duration = event.get("duration")
    details_bits: List[str] = []
    if event.get("object_type"):
        details_bits.append(str(event["object_type"]))
    if event.get("global_id") not in (None, -1):
        details_bits.append(f"GID {event['global_id']}")
    if event.get("camera_id") is not None:
        details_bits.append(f"camera {event['camera_id']}")
    if zone_id not in (None, 0):
        details_bits.append(f"zone {zone_id}")
    if isinstance(duration, (int, float)) and duration > 0:
        details_bits.append(f"dwell {float(duration):.1f}s")

    video_url = None
    if video_path:
        from urllib.parse import quote
        video_url = f"{video_url_base}?path={quote(str(video_path))}#t={clip_start},{clip_end}"

    # Event bounds the player seeks to == the padded window centred on the logged
    # moment (see above). Kept as separate fields for the frontend's box overlay.
    event_start = clip_start
    event_end = clip_end

    stayed = bool(event.get("stayed"))

    return {
        "why_logged": why_logged(event),
        "event_type": event.get("event_type") or event.get("alert_type"),
        "logged_at": logged_at,
        "clip_start_seconds": clip_start,
        "clip_end_seconds": clip_end,
        "clip_duration_seconds": clip_duration,
        "event_start_seconds": event_start,
        "event_end_seconds": event_end,
        "duration_seconds": round(float(duration), 2) if isinstance(duration, (int, float)) else None,
        "stayed": stayed,
        "source_fps": round(fps, 2),
        "details": ", ".join(details_bits) if details_bits else "-",
        "camera_id": event.get("camera_id"),
        "zone_id": zone_id,
        "global_id": event.get("global_id"),
        "video_path": video_path,
        "video_url": video_url,
    }


def enrich_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach a `clip` block to each result so the UI can offer one-click playback."""
    for row in results:
        try:
            row["clip"] = build_clip(row)
        except Exception as exc:
            row["clip"] = {"error": str(exc)}
    return results
