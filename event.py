import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Hashable, List, Optional, Tuple

from db_schema import connect_db, get_db_path

DB_PATH = get_db_path()

# keeps active in-memory sessions; multi-camera mode keys by global_id,
# single-camera mode keys by camera + global_id.
sessions: Dict[Hashable, Dict[str, object]] = {}
active_events = sessions
MIN_EVENT_DURATION_SECONDS = 1.0
STAYING_THRESHOLD_SECONDS = 10.0
SESSION_LINK_WINDOW_SECONDS = 3.0
GLOBAL_EVENT_TIMEOUT_SECONDS = 3.0
TRACKING_BATCH_SIZE = 100
OBJECT_TYPE_ALIASES = {
    "people": "person",
    "man": "person",
    "woman": "person",
    "bike": "motorcycle",
    "bicycle": "motorcycle",
}
tracking_write_buffer: List[Tuple[object, ...]] = []
EVENT_CALLBACKS = []
active_occupancy_alerts = set()


def register_event_callback(callback) -> None:
    EVENT_CALLBACKS.append(callback)


def reset_runtime_state() -> None:
    sessions.clear()
    active_occupancy_alerts.clear()


def normalize_object_type(object_type: Optional[str]) -> str:
    normalized = (object_type or "").strip().lower()
    return OBJECT_TYPE_ALIASES.get(normalized, normalized)


def normalize_event_mode(event_mode: Optional[str]) -> str:
    normalized = (event_mode or "single").strip().lower().replace("_", "-")
    if normalized in {"single", "single-camera", "single camera"}:
        return "single"
    if normalized in {"multi", "multi-camera", "multi camera"}:
        return "multi"
    return "single"


def _session_key(event_mode: str, camera_id: Optional[int], global_id: int) -> Hashable:
    if normalize_event_mode(event_mode) == "single":
        return "single", camera_id, global_id
    return "multi", global_id


def _calculate_centroid(bbox):
    x, y, w, h = bbox
    return x + w / 2, y + h / 2


def _point_in_polygon(point, polygon):
    if not polygon:
        return False

    x, y = point
    inside = False
    num_points = len(polygon)

    for idx in range(num_points):
        x1, y1 = polygon[idx]
        x2, y2 = polygon[(idx + 1) % num_points]

        if (y1 > y) != (y2 > y):
            denom = y2 - y1
            if denom == 0:
                continue

            x_intersect = x1 + (y - y1) * (x2 - x1) / denom
            if x < x_intersect:
                inside = not inside

    return inside


def _point_inside_polygon_bounding_box(point, zone):
    if not zone:
        return False

    cx, cy = point
    x1 = zone.get("x1")
    x2 = zone.get("x2")
    y1 = zone.get("y1")
    y2 = zone.get("y2")

    if None in (x1, x2, y1, y2):
        return False

    return x1 <= cx <= x2 and y1 <= cy <= y2


def _point_inside_zone(centroid, zone):
    if zone is None:
        return False

    polygon = zone.get("polygon")
    if polygon:
        return _point_in_polygon(centroid, polygon)

    return _point_inside_polygon_bounding_box(centroid, zone)


def _connect():
    return connect_db()


def init_db():
    # Schema is now managed by db_schema.py
    connect_db(validate_schema=True)


def clear_event_logs() -> None:
    sessions.clear()
    tracking_write_buffer.clear()
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM events")
    cursor.execute("DELETE FROM tracking_data")
    cursor.execute("DELETE FROM sqlite_sequence WHERE name IN ('events', 'tracking_data')")
    conn.commit()
    conn.close()


def _utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _iso_with_offset(timestamp: Optional[str], seconds: float) -> Optional[str]:
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return timestamp
    return (parsed + timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


def _calculate_duration(entry_video_time: Optional[float], current_video_time: float) -> float:
    if entry_video_time is None:
        return 0.0
    return max(0.0, float(current_video_time) - float(entry_video_time))


def _require_global_id(global_id: Optional[int]) -> int:
    if global_id is None:
        raise ValueError("global_id must never be None")
    return int(global_id)


def _find_zone_by_id(zones: List[Dict], zone_id: int) -> Optional[Dict]:
    for index, zone in enumerate(zones, start=1):
        current_zone_id = zone.get("id", index)
        if current_zone_id == zone_id:
            return zone
    return None


def _find_matching_zone(
    centroid: Tuple[float, float],
    zones: List[Dict],
    preferred_zone_id: Optional[int] = None,
) -> Optional[Dict]:
    if preferred_zone_id is not None:
        preferred_zone = _find_zone_by_id(zones, preferred_zone_id)
        if preferred_zone and _point_inside_zone(centroid, preferred_zone):
            return preferred_zone

    for index, zone in enumerate(zones, start=1):
        current_zone_id = zone.get("id", index)
        if preferred_zone_id is not None and current_zone_id == preferred_zone_id:
            continue
        if _point_inside_zone(centroid, zone):
            return zone

    return None


def _new_session(
    track_id: int,
    global_id: int,
    object_type: str,
    zone_id: int,
    camera_id: Optional[int],
    video_path: str,
    entry_time: str,
    video_time: float,
    frame_number: int,
    event_mode: str,
) -> Dict[str, object]:
    event_mode = normalize_event_mode(event_mode)
    return {
        "track_id": track_id,
        "global_id": global_id,
        "object_type": object_type,
        "zone_id": zone_id,
        "camera_id": camera_id,
        "video_path": video_path,
        "cameras": {camera_id} if camera_id is not None else set(),
        "video_paths": {video_path} if video_path else set(),
        "entry_time": entry_time,
        "start_time": entry_time,
        "entry_video_time": video_time,
        "last_seen_time": entry_time,
        "last_seen": entry_time,
        "last_video_time": video_time,
        "frame_start": frame_number,
        "last_frame_number": frame_number,
        "frames": [frame_number],
        "inside_status": True,
        "event_mode": event_mode,
    }


def _session_json_list(session: Dict[str, object], key: str) -> str:
    values = session.get(key)
    if not values:
        return "[]"
    return json.dumps(sorted(values))


def _insert_session_event(
    cursor: sqlite3.Cursor,
    session: Dict[str, object],
    exit_time: str,
    frame_number: int,
    video_time: float,
    duration: float,
    stayed: bool,
) -> None:
    cursor.execute(
        """
        INSERT INTO events
        (timestamp, object_type, track_id, global_id, camera_id, video_path,
         frame_number, frame_start, frame_end, video_time, zone_id,
         event_type, entry_time, exit_time, duration, stayed, cameras, video_paths,
         event_mode, mode_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            exit_time,
            session["object_type"],
            session["track_id"],
            session["global_id"],
            session["camera_id"],
            session["video_path"],
            frame_number,
            session["frame_start"],
            frame_number,
            video_time,
            session["zone_id"],
            "leaving",
            session["entry_time"],
            exit_time,
            duration,
            int(stayed),
            _session_json_list(session, "cameras"),
            _session_json_list(session, "video_paths"),
            session.get("event_mode", "multi"),
            session.get("event_mode", "multi"),
        ),
    )


def _write_session_event(
    session: Dict[str, object],
    exit_time: str,
    frame_number: int,
    video_time: float,
    duration: float,
    stayed: bool,
) -> None:
    conn = _connect()
    cursor = conn.cursor()
    _insert_session_event(
        cursor=cursor,
        session=session,
        exit_time=exit_time,
        frame_number=frame_number,
        video_time=video_time,
        duration=duration,
        stayed=stayed,
    )
    conn.commit()
    conn.close()

    # Trigger custom event callbacks
    for cb in EVENT_CALLBACKS:
        try:
            # Pass details in JSON-serializable structure
            cb({
                "track_id": session.get("track_id"),
                "global_id": session.get("global_id"),
                "object_type": session.get("object_type"),
                "zone_id": session.get("zone_id"),
                "camera_id": session.get("camera_id"),
                "entry_time": session.get("entry_time"),
                "exit_time": exit_time,
                "duration": duration,
                "stayed": bool(stayed)
            })
        except Exception:
            pass


def _finalize_session(session_key: Hashable, session: Dict[str, object]) -> None:
    duration = _calculate_duration(session.get("entry_video_time"), float(session.get("last_video_time", 0.0)))
    stayed = duration >= STAYING_THRESHOLD_SECONDS
    if duration >= MIN_EVENT_DURATION_SECONDS:
        _write_session_event(
            session=session,
            exit_time=str(session.get("last_seen_time") or _utc_now_iso()),
            frame_number=int(session.get("last_frame_number", session.get("frame_start", 0))),
            video_time=float(session.get("last_video_time", session.get("entry_video_time", 0.0))),
            duration=duration,
            stayed=stayed,
        )
    sessions.pop(session_key, None)


def _finalize_expired_sessions(current_video_time: float) -> None:
    expired_session_keys = []
    for session_key, session in sessions.items():
        last_video_time = float(session.get("last_video_time", session.get("entry_video_time", 0.0)))
        if float(current_video_time) - last_video_time > GLOBAL_EVENT_TIMEOUT_SECONDS:
            expired_session_keys.append(session_key)

    for session_key in expired_session_keys:
        session = sessions.get(session_key)
        if session is not None:
            _finalize_session(session_key, session)


def update_session_event(
    track_id: int,
    global_id: int,
    object_type: str,
    bbox: Tuple[int, int, int, int],
    zones: List[Dict],
    video_time: float,
    camera_id: Optional[int],
    video_path: str,
    frame_number: int,
    event_mode: str = "single",
    assigned_zone_id: Optional[int] = None,
) -> Optional[str]:
    global_id = _require_global_id(global_id)
    event_mode = normalize_event_mode(event_mode)
    _finalize_expired_sessions(video_time)
    object_type = normalize_object_type(object_type)

    current_session_key = _session_key(event_mode, camera_id, global_id)
    session = sessions.get(current_session_key)
    preferred_zone_id = int(session["zone_id"]) if session is not None else None
    
    if assigned_zone_id is not None:
        matched_zone = _find_zone_by_id(zones, assigned_zone_id)
    else:
        centroid = _calculate_centroid(bbox)
        matched_zone = _find_matching_zone(centroid, zones, preferred_zone_id)

    if session is None and matched_zone is not None:
        zone_id = int(matched_zone.get("id", 1))
        entry_time = _utc_now_iso()
        sessions[current_session_key] = _new_session(
            track_id=track_id,
            global_id=global_id,
            object_type=object_type,
            zone_id=zone_id,
            camera_id=camera_id,
            video_path=video_path,
            entry_time=entry_time,
            video_time=video_time,
            frame_number=frame_number,
            event_mode=event_mode,
        )
        print(
            f"ENTERING [{event_mode}] [{object_type}] GID {global_id} "
            f"(track {track_id}, camera {camera_id}) in zone {zone_id}"
        )
        return "entering"

    if session is not None and matched_zone is not None:
        last_seen_time = _utc_now_iso()
        if camera_id is not None:
            session.setdefault("cameras", set()).add(camera_id)
        if video_path:
            session.setdefault("video_paths", set()).add(video_path)
        session["last_seen_time"] = last_seen_time
        session["last_seen"] = last_seen_time
        session["last_video_time"] = video_time
        session["last_frame_number"] = frame_number
        session.setdefault("frames", []).append(frame_number)
        session["track_id"] = track_id
        session["global_id"] = global_id
        session["object_type"] = object_type
        session["inside_status"] = True
        session["event_mode"] = event_mode

        duration = _calculate_duration(session.get("entry_video_time"), video_time)
        stayed = duration >= STAYING_THRESHOLD_SECONDS
        return "staying" if stayed else None

    if session is not None and matched_zone is None:
        session["inside_status"] = False

    return None


def finalize_camera_sessions(camera_id: Optional[int], video_path: Optional[str] = None) -> None:
    stale_keys = []
    for session_key, session in sessions.items():
        cameras = session.get("cameras") or set()
        video_paths = session.get("video_paths") or set()
        if camera_id is not None and camera_id not in cameras:
            continue
        if video_path is not None and video_path not in video_paths:
            continue

        stale_keys.append(session_key)

    for session_key in stale_keys:
        session = sessions.get(session_key)
        if session is not None:
            _finalize_session(session_key, session)


def flush_tracking_data() -> None:
    if not tracking_write_buffer:
        return

    conn = _connect()
    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT OR REPLACE INTO tracking_data
        (camera_id, video_path, frame_number, track_id, global_id, object_type,
         bbox_x1, bbox_y1, bbox_x2, bbox_y2, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tracking_write_buffer,
    )
    conn.commit()
    conn.close()
    tracking_write_buffer.clear()


def log_tracking_data(
        track_id: int,
        global_id: int,
        object_type: str,
        bbox: Tuple[int, int, int, int],
        frame_number: int,
        camera_id: Optional[int],
        video_path: str,
    ) -> None:
    global_id = _require_global_id(global_id)
    x1, y1, x2, y2 = bbox
    tracking_write_buffer.append(
        (
            camera_id,
            video_path,
            frame_number,
            track_id,
            global_id,
            object_type,
            x1,
            y1,
            x2,
            y2,
            _utc_now_iso(),
        )
    )
    if len(tracking_write_buffer) >= TRACKING_BATCH_SIZE:
        flush_tracking_data()


def get_tracking_data(
        video_path: str,
        track_id: Optional[int],
        start_frame: int,
        end_frame: int,
        camera_id: Optional[int] = None,
        global_id: Optional[int] = None,
    ) -> List[Dict]:
    flush_tracking_data()
    conn = _connect()
    cursor = conn.cursor()

    sql = """
        SELECT frame_number, track_id, global_id, object_type,
               bbox_x1, bbox_y1, bbox_x2, bbox_y2
        FROM tracking_data
        WHERE video_path = ?
          AND frame_number BETWEEN ? AND ?
    """
    params: List[object] = [video_path, start_frame, end_frame]

    if global_id is not None:
        sql += " AND global_id = ?"
        params.append(global_id)
    elif track_id is not None:
        sql += " AND track_id = ?"
        params.append(track_id)
    else:
        conn.close()
        raise ValueError("Either track_id or global_id must be provided.")

    if camera_id is None:
        sql += " AND camera_id IS NULL"
    else:
        sql += " AND camera_id = ?"
        params.append(camera_id)

    sql += " ORDER BY frame_number ASC"

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "frame_number": row[0],
            "track_id": row[1],
            "global_id": row[2],
            "object_type": row[3],
            "bbox": (row[4], row[5], row[6], row[7]),
        }
        for row in rows
    ]


def get_distinct_camera_sources() -> List[Dict[str, object]]:
    flush_tracking_data()
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT DISTINCT camera_id, video_path
        FROM tracking_data
        WHERE video_path IS NOT NULL
        ORDER BY camera_id ASC, video_path ASC
        """
    )
    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "camera_id": row[0],
            "video_path": row[1],
        }
        for row in rows
    ]


def get_global_id_camera_presence(global_id: int) -> List[Dict[str, object]]:
    flush_tracking_data()
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT camera_id, video_path, MIN(frame_number), MAX(frame_number)
        FROM tracking_data
        WHERE global_id = ?
        GROUP BY camera_id, video_path
        ORDER BY camera_id ASC, video_path ASC
        """,
        (global_id,),
    )
    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "camera_id": row[0],
            "video_path": row[1],
            "first_frame": row[2],
            "last_frame": row[3],
        }
        for row in rows
    ]


def get_playback_segments(
    video_path: Optional[str] = None,
    frame_start: Optional[int] = None,
    frame_end: Optional[int] = None,
    camera_id: Optional[int] = None,
    track_id: Optional[int] = None,
    global_id: Optional[int] = None,
    entry_time: Optional[str] = None,
    exit_time: Optional[str] = None,
    event_mode: Optional[str] = None,
) -> List[Dict[str, object]]:
    flush_tracking_data()
    conn = _connect()
    cursor = conn.cursor()

    sql = """
        SELECT camera_id,
               video_path,
               MIN(frame_start),
               MAX(COALESCE(frame_end, frame_number, frame_start)),
               MIN(COALESCE(entry_time, timestamp)),
               MAX(COALESCE(exit_time, entry_time, timestamp))
        FROM events
        WHERE frame_start IS NOT NULL
    """
    params: List[object] = []

    if isinstance(global_id, int):
        sql += " AND global_id = ?"
        params.append(global_id)
    elif isinstance(track_id, int):
        sql += " AND track_id = ?"
        params.append(track_id)
    else:
        conn.close()
        return []

    if track_id is not None and global_id is None and camera_id is None:
        sql += " AND camera_id IS NULL"
    elif track_id is not None and global_id is None:
        sql += " AND camera_id = ?"
        params.append(camera_id)

    if track_id is not None and global_id is None and video_path is not None:
        sql += " AND video_path = ?"
        params.append(video_path)

    normalized_event_mode = normalize_event_mode(event_mode) if event_mode else None
    if normalized_event_mode:
        sql += " AND COALESCE(event_mode, mode_type, 'multi') = ?"
        params.append(normalized_event_mode)

    if normalized_event_mode == "single":
        if camera_id is None:
            sql += " AND camera_id IS NULL"
        else:
            sql += " AND camera_id = ?"
            params.append(camera_id)
        if video_path:
            sql += " AND video_path = ?"
            params.append(video_path)

    window_entry = _iso_with_offset(entry_time, -SESSION_LINK_WINDOW_SECONDS)
    window_exit = _iso_with_offset(exit_time or entry_time, SESSION_LINK_WINDOW_SECONDS)
    if window_entry:
        sql += " AND COALESCE(exit_time, entry_time, timestamp) >= ?"
        params.append(window_entry)
    if window_exit:
        sql += " AND COALESCE(entry_time, timestamp) <= ?"
        params.append(window_exit)

    sql += " GROUP BY camera_id, video_path ORDER BY MIN(COALESCE(entry_time, timestamp)) ASC, camera_id ASC"

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()

    segments = [
        {
            "camera_id": row[0],
            "video_path": row[1],
            "start_frame": row[2],
            "end_frame": row[3],
            "entry_time": row[4],
            "exit_time": row[5],
        }
        for row in rows
        if row[1] is not None and row[2] is not None and row[3] is not None
    ]

    if segments:
        return segments

    fallback_start = frame_start if frame_start is not None else 0
    fallback_end = frame_end if frame_end is not None else fallback_start
    return [
        {
            "camera_id": camera_id,
            "video_path": video_path,
            "start_frame": fallback_start,
            "end_frame": fallback_end,
            "entry_time": entry_time,
            "exit_time": exit_time,
        }
    ]



def log_event(
        track_id,
        object_type,
        event_type,
        zone_id,
        camera_id,
        video_time,
        video_path,
        frame_number,
    ):
    raise RuntimeError(
        "log_event() is deprecated in session mode; use update_session_event() instead."
    )


def _write_occupancy_alert_to_db(
    camera_id: int,
    zone_id: int,
    zone_name: str,
    current_count: int,
    limit: int,
    frame_number: int,
    video_time: float,
    video_path: str
) -> None:
    timestamp = _utc_now_iso()
    try:
        conn = connect_db(validate_schema=False)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO events (
                timestamp, object_type, track_id, global_id, camera_id, video_path,
                frame_number, frame_start, frame_end, video_time, zone_id,
                event_type, event_mode, mode_type, entry_time, exit_time, duration, stayed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                "zone",
                -1,
                -1,
                camera_id,
                video_path,
                frame_number,
                frame_number,
                frame_number,
                video_time,
                zone_id,
                "occupancy_alert",
                "single",
                "system",
                timestamp,
                timestamp,
                float(current_count),
                limit
            )
        )
        conn.commit()
    except Exception as e:
        print(f"Error logging occupancy alert: {e}")
    finally:
        if "conn" in locals():
            conn.close()

    alert_event = {
        "event_id": -1,
        "timestamp": timestamp,
        "type": "occupancy_alert",
        "camera_id": camera_id,
        "zone_id": zone_id,
        "zone_name": zone_name,
        "count": current_count,
        "limit": limit,
        "description": f"Occupancy limit exceeded in {zone_name}: {current_count} (limit: {limit})"
    }
    for cb in EVENT_CALLBACKS:
        try:
            cb(alert_event)
        except Exception as e:
            print(f"Error executing callback: {e}")


def check_occupancy_alerts(
    camera_id: int,
    pixel_zones: List[Dict],
    frame_number: int,
    video_time: float,
    video_path: str = ""
) -> None:
    if not pixel_zones:
        return

    zone_counts = {}
    for session in sessions.values():
        if (
            session.get("inside_status")
            and session.get("last_frame_number") == frame_number
            and session.get("camera_id") == camera_id
        ):
            zid = session.get("zone_id")
            if zid is not None:
                zone_counts[zid] = zone_counts.get(zid, 0) + 1

    for zone in pixel_zones:
        zone_id = zone.get("id")
        limit = zone.get("max_occupancy", 3)
        current_count = zone_counts.get(zone_id, 0)
        
        alert_key = (camera_id, zone_id)
        
        if current_count > limit:
            if alert_key not in active_occupancy_alerts:
                active_occupancy_alerts.add(alert_key)
                print(f"🚨 [OCCUPANCY ALERT] Camera {camera_id} Zone {zone_id} ({zone.get('name')}) occupancy: {current_count} (limit: {limit})")
                _write_occupancy_alert_to_db(camera_id, zone_id, zone.get("name", f"Zone {zone_id}"), current_count, limit, frame_number, video_time, video_path)
        else:
            if alert_key in active_occupancy_alerts:
                active_occupancy_alerts.remove(alert_key)
                print(f"✅ [OCCUPANCY CLEAN] Camera {camera_id} Zone {zone_id} ({zone.get('name')}) back to normal: {current_count} (limit: {limit})")
