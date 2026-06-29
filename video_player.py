from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import cv2
import numpy as np

from event import (
    get_playback_segments,
    get_tracking_data,
)
from zone_manager import build_pixel_zones, get_camera_zones

TARGET_PLAYBACK_FPS = 20
PLAYBACK_JUMP_SECONDS = 2
LEFT_ARROW_KEYS = {81, 2424832, 65361}
RIGHT_ARROW_KEYS = {83, 2555904, 65363}


@dataclass
class PlaybackFeed:
    camera_id: Optional[int]
    video_path: str
    cap: cv2.VideoCapture
    fps: float
    total_frames: int
    start_frame: int
    end_frame: int
    entry_time: datetime
    zone_defs: List[Dict]
    metadata_by_frame: Dict[int, Dict[str, object]]
    current_frame: int
    title: str
    target_global_id: Optional[int]

    def close(self) -> None:
        self.cap.release()


def _draw_zones(frame, zones):
    if not zones:
        return

    for zone in zones:
        polygon = zone.get("polygon")
        if not polygon:
            continue

        pts = np.array(polygon, dtype=np.int32)
        cv2.polylines(frame, [pts], True, (0, 128, 255), 2)

        label = zone.get("name", f"Zone {zone.get('id', '')}")
        text_pos = (pts[0][0], max(12, pts[0][1] - 8))
        cv2.putText(
            frame,
            label,
            text_pos,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 255, 255),
            1,
        )


def _draw_tracking_box(frame, bbox, track_id, global_id, object_type):
    x1, y1, x2, y2 = bbox
    color = (0, 0, 255)
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3)
    cv2.putText(
        frame,
        f"{object_type} GID {global_id} | Track {track_id}",
        (x1, max(16, y1 - 12)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        color,
        2,
    )


def _draw_info_overlay(frame, feed, current_time: datetime, metadata: Optional[Dict]):
    # Draw Camera ID and Timestamp at top-left
    overlay_text = f"CAM {feed.camera_id} | {current_time.strftime('%Y-%m-%d %H:%M:%S')}"
    cv2.putText(frame, overlay_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

    if metadata:
        # Draw Object info at bottom-left
        obj_text = f"OBJECT: {metadata['object_type'].upper()} | GID: {metadata['global_id']} | TRACK: {metadata['track_id']}"
        cv2.putText(frame, obj_text, (10, frame.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)


def _draw_status(
    frame,
    playback_seconds: float,
    total_duration_seconds: float,
    target_label: str,
    paused: bool,
):
    status = f"Playback | {target_label} | {playback_seconds:.1f}s / {total_duration_seconds:.1f}s"
    if paused:
        status += " | PAUSED"

    controls = "SPACE: pause | LEFT/RIGHT: seek | Q: quit"
    cv2.rectangle(frame, (0, 0), (frame.shape[1], 58), (0, 0, 0), cv2.FILLED)
    cv2.putText(
        frame,
        status,
        (12, 22),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.62,
        (255, 255, 255),
        2,
    )
    cv2.putText(
        frame,
        controls,
        (12, 46),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (255, 255, 255),
        1,
    )


def _is_left_arrow(key):
    return key in LEFT_ARROW_KEYS


def _is_right_arrow(key):
    return key in RIGHT_ARROW_KEYS


def _clamp_frame(frame_number: int, total_frames: int) -> int:
    if total_frames > 0:
        return max(0, min(frame_number, total_frames - 1))
    return max(0, frame_number)


def _event_payload(
    event_or_video_file,
    frame_number=None,
    target_track_id=None,
    camera_id=None,
    video_time=None,
    target_global_id=None,
):
    if isinstance(event_or_video_file, dict):
        return dict(event_or_video_file)

    return {
        "video_path": event_or_video_file,
        "frame_number": frame_number,
        "frame_start": frame_number,
        "frame_end": frame_number,
        "track_id": target_track_id,
        "camera_id": camera_id,
        "video_time": video_time,
        "global_id": target_global_id,
        "entry_time": None,
        "exit_time": None,
    }


def _build_feed(
    source: Dict[str, object],
    event_entry: Dict[str, object],
) -> Optional[PlaybackFeed]:
    video_path = str(source["video_path"])
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"⚠️ Could not open video for camera {source.get('camera_id')}: {video_path}")
        return None

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if fps <= 0:
        cap.release()
        print(f"⚠️ Invalid FPS for camera {source.get('camera_id')}: {video_path}")
        return None

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    start_frame = _clamp_frame(int(source.get("start_frame") or event_entry.get("frame_start") or 0), total_frames)
    end_frame = _clamp_frame(
        int(source.get("end_frame") or event_entry.get("frame_end") or event_entry.get("frame_number") or start_frame),
        total_frames,
    )
    if end_frame < start_frame:
        end_frame = start_frame

    # Parse entry time
    entry_time_str = source.get("entry_time")
    if entry_time_str:
        try:
            entry_time = datetime.fromisoformat(str(entry_time_str))
        except ValueError:
            entry_time = datetime.now()
    else:
        entry_time = datetime.now()

    feed_camera_id = source.get("camera_id")
    event_camera_id = event_entry.get("camera_id")
    feed_track_id = event_entry.get("track_id") if feed_camera_id == event_camera_id else None
    feed_global_id = event_entry.get("global_id")
    metadata_rows: List[Dict[str, object]] = []
    if feed_global_id is not None or feed_track_id is not None:
        metadata_rows = get_tracking_data(
            video_path,
            feed_track_id,
            start_frame,
            end_frame,
            camera_id=feed_camera_id,
            global_id=feed_global_id,
        )

    return PlaybackFeed(
        camera_id=feed_camera_id,
        video_path=video_path,
        cap=cap,
        fps=fps,
        total_frames=total_frames,
        start_frame=start_frame,
        end_frame=end_frame,
        entry_time=entry_time,
        zone_defs=get_camera_zones(feed_camera_id) if feed_camera_id is not None else [],
        metadata_by_frame={row["frame_number"]: row for row in metadata_rows},
        current_frame=start_frame,
        title=f"Camera {feed_camera_id}" if feed_camera_id is not None else "Camera",
        target_global_id=feed_global_id,
    )


def _read_feed_frame(
    feed: PlaybackFeed,
    session_start_time: datetime,
    playback_offset_sec: float
) -> tuple[Optional[np.ndarray], bool]:
    # Calculate target absolute time
    target_absolute_time = session_start_time + timedelta(seconds=playback_offset_sec)

    # Calculate frame relative to feed's entry time
    time_diff = (target_absolute_time - feed.entry_time).total_seconds()
    target_frame = feed.start_frame + int(round(time_diff * feed.fps))

    # Check if target_frame is within feed's valid range
    is_active = feed.start_frame <= target_frame <= feed.end_frame

    # Clamp for reading
    read_frame = _clamp_frame(target_frame, feed.total_frames)
    feed.current_frame = read_frame

    feed.cap.set(cv2.CAP_PROP_POS_FRAMES, read_frame)
    ret, frame = feed.cap.read()
    if not ret or frame is None:
        return None, False

    # Draw zones
    pixel_zones = build_pixel_zones(feed.zone_defs, frame.shape) if feed.zone_defs else []
    _draw_zones(frame, pixel_zones)

    # Get metadata if active
    metadata = feed.metadata_by_frame.get(read_frame) if is_active else None
    if metadata:
        _draw_tracking_box(
            frame,
            metadata["bbox"],
            metadata["track_id"],
            metadata["global_id"],
            metadata["object_type"],
        )

    # Draw info overlay
    _draw_info_overlay(frame, feed, target_absolute_time, metadata)

    return frame, metadata is not None


def play_event(
    event_or_video_file,
    frame_number=None,
    target_track_id=None,
    camera_id=None,
    video_time=None,
    target_global_id=None,
):
    event_entry = _event_payload(
        event_or_video_file,
        frame_number=frame_number,
        target_track_id=target_track_id,
        camera_id=camera_id,
        video_time=video_time,
        target_global_id=target_global_id,
    )
    video_file = str(event_entry["video_path"])
    camera_id = event_entry.get("camera_id")
    target_global_id = event_entry.get("global_id")
    target_track_id = event_entry.get("track_id")

    sources = get_playback_segments(
        video_path=video_file,
        frame_start=event_entry.get("frame_start"),
        frame_end=event_entry.get("frame_end"),
        camera_id=camera_id,
        track_id=target_track_id,
        global_id=target_global_id,
        entry_time=event_entry.get("entry_time"),
        exit_time=event_entry.get("exit_time"),
        event_mode=event_entry.get("event_mode") or event_entry.get("session_mode"),
    )

    feeds: List[PlaybackFeed] = []
    for source in sources:
        feed = _build_feed(source=source, event_entry=event_entry)
        if feed is not None:
            feeds.append(feed)

    if not feeds:
        print("❌ No playable camera feeds found for the selected event.")
        return

    # Determine global session start time (earliest entry time)
    session_start_time = min(f.entry_time for f in feeds)
    # Determine global session end time (latest exit time approximated by duration)
    session_end_time = session_start_time
    for f in feeds:
        feed_duration = (f.end_frame - f.start_frame) / f.fps
        feed_exit_time = f.entry_time + timedelta(seconds=feed_duration)
        if feed_exit_time > session_end_time:
            session_end_time = feed_exit_time

    session_duration_sec = (session_end_time - session_start_time).total_seconds()

    target_label = f"GID {target_global_id}" if target_global_id is not None else f"Track {target_track_id}"
    print(f"🎯 Strict Sync Session playback for {target_label} | Duration: {session_duration_sec:.1f}s")

    window_name = "Sentinel AI - Event Playback"
    paused = False
    playback_offset_sec = 0.0
    feed = feeds[0]

    try:
        while True:
            frame, has_highlight = _read_feed_frame(feed, session_start_time, playback_offset_sec)
            if frame is not None:
                canvas = frame.copy()
                _draw_status(canvas, playback_offset_sec, session_duration_sec, target_label, paused)
                cv2.imshow(window_name, canvas)

            key = cv2.waitKeyEx(30 if paused else max(1, int(1000 / TARGET_PLAYBACK_FPS)))
            key_cmd = key & 0xFF
            if key_cmd in (ord("q"), ord("Q"), 27):
                break

            if key_cmd == ord(" "):
                paused = not paused
                continue

            if _is_left_arrow(key) or _is_right_arrow(key):
                frame_offset_seconds = -PLAYBACK_JUMP_SECONDS if _is_left_arrow(key) else PLAYBACK_JUMP_SECONDS
                playback_offset_sec = max(0.0, min(session_duration_sec, playback_offset_sec + frame_offset_seconds))
                continue

            if not paused:
                playback_offset_sec += 1.0 / TARGET_PLAYBACK_FPS
                if playback_offset_sec > session_duration_sec:
                    playback_offset_sec = session_duration_sec
                    paused = True
    finally:
        for f in feeds:
            f.close()
        cv2.destroyWindow(window_name)

