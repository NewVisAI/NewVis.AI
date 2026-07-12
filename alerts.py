"""
Alert engine + principal notifications.

Evaluates zone-entry alerts (restricted areas, after-hours / non-school-day
entries via the campus holiday calendar) and records them with:
  - a snapshot photo of the exact moment (red box around the subject),
  - enough metadata (video_path, frame_number, track_id, global_id) to jump
    straight to the recorded footage,
  - an in-app notification inbox for the principal (unread/read state).

All storage goes through db_schema.connect_db so it works on both SQLite
and PostgreSQL, in the same database as the events/tracking tables.
"""

import os
from datetime import date, datetime, time as dt_time
from typing import Dict, List, Optional

import cv2

import school_calendar
from db_schema import adapt_query, connect_db, get_db_type

SNAPSHOT_DIR = "alert_snapshots"

RESTRICTED_ZONE_ENTRY = "restricted_zone_entry"
AFTER_HOURS_ENTRY = "after_hours_entry"
FALL_DETECTED = "fall_detected"
RUNNING_DETECTED = "running_detected"
VIOLENCE_DETECTED = "violence_detected"

WEEKDAY_CODES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
PRINCIPAL_RECIPIENT = "principal"


def _safe_print(message: str) -> None:
    """Console output must never break alert/notification recording
    (Windows consoles with legacy code pages can't encode emoji)."""
    try:
        print(message)
    except UnicodeEncodeError:
        print(message.encode("ascii", errors="replace").decode("ascii"))


def _insert_returning_id(cursor, sql: str, params) -> int:
    """Runs an INSERT and returns the new row id on both SQLite and Postgres."""
    if get_db_type() == "postgres":
        cursor.execute(adapt_query(sql) + " RETURNING id", params)
        return cursor.fetchone()[0]
    cursor.execute(sql, params)
    return cursor.lastrowid


def init_alerts_db() -> None:
    conn = connect_db(validate_schema=False)
    cursor = conn.cursor()
    pk = (
        "id SERIAL PRIMARY KEY"
        if get_db_type() == "postgres"
        else "id INTEGER PRIMARY KEY AUTOINCREMENT"
    )
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS alerts (
            {pk},
            timestamp TEXT,
            alert_type TEXT,
            zone_id INTEGER,
            zone_name TEXT,
            camera_id INTEGER,
            global_id INTEGER,
            object_type TEXT,
            message TEXT,
            acknowledged INTEGER DEFAULT 0,
            video_path TEXT,
            frame_number INTEGER,
            track_id INTEGER,
            snapshot_path TEXT
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts (timestamp)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_zone ON alerts (zone_id, camera_id)")

    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS notifications (
            {pk},
            timestamp TEXT,
            recipient TEXT,
            title TEXT,
            message TEXT,
            alert_id INTEGER,
            read INTEGER DEFAULT 0
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON notifications (recipient, read)"
    )
    conn.commit()
    conn.close()


def _parse_time(value: str) -> Optional[dt_time]:
    try:
        hours, minutes = value.split(":")
        return dt_time(int(hours), int(minutes))
    except (ValueError, AttributeError):
        return None


def _is_outside_active_hours(active_hours: Dict[str, str], now: Optional[dt_time] = None) -> bool:
    start = _parse_time(active_hours.get("start", ""))
    end = _parse_time(active_hours.get("end", ""))
    if start is None or end is None:
        return False

    current = now or datetime.now().time()

    if start <= end:
        return not (start <= current <= end)
    # Window wraps past midnight (e.g. 22:00 - 06:00).
    return not (current >= start or current <= end)


def _non_school_day_reason(zone: Dict, today: date) -> Optional[str]:
    if school_calendar.is_holiday(today):
        return "holiday"

    school_days = zone.get("school_days")
    if school_days:
        today_code = WEEKDAY_CODES[today.weekday()]
        if today_code not in school_days:
            return "weekend/non-school day"

    return None


def evaluate_entry_alert(
    zone: Dict,
    camera_id: Optional[int],
    global_id: int,
    object_type: str,
) -> Optional[Dict]:
    zone_id = zone.get("id")
    zone_name = zone.get("name", f"Zone {zone_id}")

    if zone.get("restricted"):
        return {
            "alert_type": RESTRICTED_ZONE_ENTRY,
            "zone_id": zone_id,
            "zone_name": zone_name,
            "camera_id": camera_id,
            "global_id": global_id,
            "object_type": object_type,
            "message": (
                f"{object_type.title()} (GID {global_id}) entered restricted zone "
                f"'{zone_name}' on camera {camera_id}"
            ),
        }

    active_hours = zone.get("active_hours")
    if active_hours:
        now = datetime.now()
        non_school_reason = _non_school_day_reason(zone, now.date())
        outside_hours = _is_outside_active_hours(active_hours, now.time())

        if non_school_reason or outside_hours:
            reason = (
                f"it's a {non_school_reason}"
                if non_school_reason
                else f"outside allowed hours ({active_hours.get('start')}-{active_hours.get('end')})"
            )
            return {
                "alert_type": AFTER_HOURS_ENTRY,
                "zone_id": zone_id,
                "zone_name": zone_name,
                "camera_id": camera_id,
                "global_id": global_id,
                "object_type": object_type,
                "message": (
                    f"{object_type.title()} (GID {global_id}) entered '{zone_name}' on camera "
                    f"{camera_id} while {reason}"
                ),
            }

    return None


def _save_snapshot(frame, bbox, alert_id: int, zone_name: str, object_type: str) -> Optional[str]:
    try:
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        annotated = frame.copy()
        x1, y1, x2, y2 = [int(v) for v in bbox]
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 255), 2)
        label = f"{object_type} - {zone_name}" if zone_name else object_type
        cv2.putText(
            annotated, label, (x1, max(0, y1 - 10)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2,
        )
        path = os.path.join(SNAPSHOT_DIR, f"alert_{alert_id}.jpg")
        cv2.imwrite(path, annotated)
        return path
    except Exception as exc:
        _safe_print(f"⚠️ Could not save alert snapshot: {exc}")
        return None


def record_alert(alert: Dict, frame=None, bbox=None) -> int:
    conn = connect_db(validate_schema=False)
    cursor = conn.cursor()
    timestamp = datetime.now().replace(microsecond=0).isoformat()
    alert_id = _insert_returning_id(
        cursor,
        """
        INSERT INTO alerts
        (timestamp, alert_type, zone_id, zone_name, camera_id, global_id, object_type, message,
         video_path, frame_number, track_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            timestamp,
            alert["alert_type"],
            alert.get("zone_id"),
            alert.get("zone_name"),
            alert.get("camera_id"),
            alert.get("global_id"),
            alert.get("object_type"),
            alert.get("message"),
            alert.get("video_path"),
            alert.get("frame_number"),
            alert.get("track_id"),
        ),
    )

    snapshot_path = None
    if frame is not None and bbox is not None:
        snapshot_path = _save_snapshot(
            frame, bbox, alert_id, alert.get("zone_name", ""), alert.get("object_type", "")
        )
        if snapshot_path:
            cursor.execute(
                adapt_query("UPDATE alerts SET snapshot_path = ? WHERE id = ?"),
                (snapshot_path, alert_id),
            )

    conn.commit()
    conn.close()

    # Console output today; real push/email/SMS delivery replaces this sink later.
    _safe_print(f"🚨 ALERT [{alert['alert_type']}]: {alert['message']}")
    if snapshot_path:
        _safe_print(f"   📸 Snapshot saved: {snapshot_path}")
    _record_notification(alert, alert_id)
    _dispatch_alert_callbacks(alert, alert_id, snapshot_path)
    return alert_id


# Optional listeners (e.g. the FastAPI backend broadcasting over WebSocket).
_alert_callbacks = []


def register_alert_callback(callback) -> None:
    _alert_callbacks.append(callback)


def _dispatch_alert_callbacks(alert: Dict, alert_id: int, snapshot_path: Optional[str]) -> None:
    payload = dict(alert)
    payload["id"] = alert_id
    payload["snapshot_path"] = snapshot_path
    for callback in list(_alert_callbacks):
        try:
            callback(payload)
        except Exception as exc:
            _safe_print(f"⚠️ Alert callback failed: {exc}")


def _alert_title(alert_type: str) -> str:
    if alert_type == RESTRICTED_ZONE_ENTRY:
        return "Restricted area entry"
    if alert_type == AFTER_HOURS_ENTRY:
        return "After-hours / non-school-day entry"
    if alert_type == FALL_DETECTED:
        return "Possible fall detected"
    if alert_type == RUNNING_DETECTED:
        return "Running detected"
    if alert_type == VIOLENCE_DETECTED:
        return "Possible altercation / anomaly"
    return alert_type.replace("_", " ").title()


def _record_notification(alert: Dict, alert_id: int) -> int:
    conn = connect_db(validate_schema=False)
    cursor = conn.cursor()
    timestamp = datetime.now().replace(microsecond=0).isoformat()
    title = _alert_title(alert["alert_type"])
    notification_id = _insert_returning_id(
        cursor,
        """
        INSERT INTO notifications (timestamp, recipient, title, message, alert_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (timestamp, PRINCIPAL_RECIPIENT, title, alert.get("message"), alert_id),
    )
    conn.commit()
    conn.close()

    _safe_print(f"🔔 PRINCIPAL NOTIFICATION [{title}]: {alert.get('message')}")
    return notification_id


def get_notifications(recipient: str = PRINCIPAL_RECIPIENT, unread_only: bool = False, limit: int = 50) -> List[Dict]:
    conn = connect_db(validate_schema=False)
    cursor = conn.cursor()
    sql = """
        SELECT n.id, n.timestamp, n.recipient, n.title, n.message, n.alert_id, n.read,
               a.camera_id, a.zone_name, a.global_id, a.object_type, a.snapshot_path, a.video_path
        FROM notifications n
        LEFT JOIN alerts a ON a.id = n.alert_id
        WHERE n.recipient = ?
    """
    params: List[object] = [recipient]
    if unread_only:
        sql += " AND n.read = 0"
    sql += " ORDER BY n.timestamp DESC LIMIT ?"
    params.append(limit)

    cursor.execute(adapt_query(sql), params)
    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "id": row[0],
            "timestamp": row[1],
            "recipient": row[2],
            "title": row[3],
            "message": row[4],
            "alert_id": row[5],
            "read": bool(row[6]),
            "camera_id": row[7],
            "zone_name": row[8],
            "global_id": row[9],
            "object_type": row[10],
            "snapshot_path": row[11],
            "video_path": row[12],
        }
        for row in rows
    ]


def mark_notifications_read(notification_ids: Optional[List[int]] = None, recipient: str = PRINCIPAL_RECIPIENT) -> None:
    conn = connect_db(validate_schema=False)
    cursor = conn.cursor()
    if notification_ids:
        placeholders = ",".join("?" * len(notification_ids))
        cursor.execute(
            adapt_query(f"UPDATE notifications SET read = 1 WHERE id IN ({placeholders})"),
            notification_ids,
        )
    else:
        cursor.execute(
            adapt_query("UPDATE notifications SET read = 1 WHERE recipient = ?"),
            (recipient,),
        )
    conn.commit()
    conn.close()


def raise_zone_alert(
    zone: Dict,
    camera_id: Optional[int],
    global_id: int,
    object_type: str,
    track_id: Optional[int] = None,
    video_path: Optional[str] = None,
    frame_number: Optional[int] = None,
    frame=None,
    bbox=None,
) -> Optional[int]:
    alert = evaluate_entry_alert(zone, camera_id, global_id, object_type)
    if alert is None:
        return None

    alert["track_id"] = track_id
    alert["video_path"] = video_path
    alert["frame_number"] = frame_number
    return record_alert(alert, frame=frame, bbox=bbox)


def raise_fall_alert(
    camera_id: Optional[int],
    global_id: int,
    track_id: Optional[int] = None,
    video_path: Optional[str] = None,
    frame_number: Optional[int] = None,
    frame=None,
    bbox=None,
    details: Optional[Dict] = None,
) -> Optional[int]:
    detail_note = ""
    if details:
        detail_note = (
            f" (aspect ratio {details.get('aspect_ratio_before')} -> "
            f"{details.get('aspect_ratio_after')}, drop {details.get('vertical_drop_ratio')}x body height)"
        )

    alert = {
        "alert_type": FALL_DETECTED,
        "zone_id": None,
        "zone_name": None,
        "camera_id": camera_id,
        "global_id": global_id,
        "object_type": "person",
        "message": f"Possible fall detected for person (GID {global_id}) on camera {camera_id}{detail_note}",
        "track_id": track_id,
        "video_path": video_path,
        "frame_number": frame_number,
    }
    return record_alert(alert, frame=frame, bbox=bbox)


def raise_violence_alert(
    camera_id: Optional[int],
    global_id: int,
    other_global_id: Optional[int] = None,
    track_id: Optional[int] = None,
    video_path: Optional[str] = None,
    frame_number: Optional[int] = None,
    frame=None,
    bbox=None,
    details: Optional[Dict] = None,
) -> Optional[int]:
    who = f"GID {global_id}" + (f" & GID {other_global_id}" if other_global_id is not None else "")
    note = ""
    if details:
        note = f" (proximity {details.get('distance_px')}px, combined motion {details.get('energy')}px/s)"
    alert = {
        "alert_type": VIOLENCE_DETECTED,
        "zone_id": None,
        "zone_name": None,
        "camera_id": camera_id,
        "global_id": global_id,
        "object_type": "person",
        "message": f"Possible altercation between {who} on camera {camera_id}{note}",
        "track_id": track_id,
        "video_path": video_path,
        "frame_number": frame_number,
    }
    return record_alert(alert, frame=frame, bbox=bbox)


def search_alerts(
    alert_type: Optional[str] = None,
    camera_id: Optional[int] = None,
    zone_id: Optional[int] = None,
    limit: int = 50,
) -> List[Dict]:
    conn = connect_db(validate_schema=False)
    cursor = conn.cursor()
    sql = """
        SELECT id, timestamp, alert_type, zone_id, zone_name, camera_id, global_id,
               track_id, object_type, message, video_path, frame_number, snapshot_path
        FROM alerts WHERE 1=1
    """
    params: List[object] = []
    if alert_type:
        sql += " AND alert_type = ?"
        params.append(alert_type)
    if camera_id is not None:
        sql += " AND camera_id = ?"
        params.append(camera_id)
    if zone_id is not None:
        sql += " AND zone_id = ?"
        params.append(zone_id)
    sql += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    cursor.execute(adapt_query(sql), params)
    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        timestamp = row[1]
        frame_number = row[11]
        results.append({
            "id": row[0],
            "timestamp": timestamp,
            "alert_type": row[2],
            "zone_id": row[3],
            "zone_name": row[4] or "-",
            "camera_id": row[5],
            "global_id": row[6],
            "track_id": row[7],
            "object_type": row[8],
            "message": row[9],
            "video_path": row[10],
            "frame_number": frame_number,
            "frame_start": frame_number,
            "frame_end": frame_number,
            "snapshot_path": row[12],
            "entry_time": timestamp,
            "exit_time": None,
            "display_label": "timestamp",
            "display_value": timestamp,
            "event_type": row[2],
        })
    return results


def get_alert_playback_entry(alert_id: int) -> Optional[Dict]:
    conn = connect_db(validate_schema=False)
    cursor = conn.cursor()
    cursor.execute(
        adapt_query(
            """
            SELECT camera_id, global_id, track_id, object_type, video_path, frame_number, timestamp
            FROM alerts WHERE id = ?
            """
        ),
        (alert_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None or not row[4]:
        return None

    return {
        "camera_id": row[0],
        "global_id": row[1],
        "track_id": row[2],
        "object_type": row[3],
        "video_path": row[4],
        "frame_number": row[5],
        "frame_start": row[5],
        "frame_end": row[5],
        "entry_time": row[6],
        "exit_time": None,
    }


def get_recent_alerts(limit: int = 50) -> List[Dict]:
    conn = connect_db(validate_schema=False)
    cursor = conn.cursor()
    cursor.execute(
        adapt_query(
            """
            SELECT id, timestamp, alert_type, zone_id, zone_name, camera_id, global_id,
                   object_type, message, acknowledged, video_path, frame_number, track_id, snapshot_path
            FROM alerts
            ORDER BY timestamp DESC
            LIMIT ?
            """
        ),
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "id": row[0],
            "timestamp": row[1],
            "alert_type": row[2],
            "zone_id": row[3],
            "zone_name": row[4],
            "camera_id": row[5],
            "global_id": row[6],
            "object_type": row[7],
            "message": row[8],
            "acknowledged": bool(row[9]),
            "video_path": row[10],
            "frame_number": row[11],
            "track_id": row[12],
            "snapshot_path": row[13],
        }
        for row in rows
    ]
