import sqlite3
from datetime import datetime
from db_schema import connect_db, get_db_path, LockedConnection

# Use the unified database from db_schema
DB_NAME = get_db_path()


def init_db():
    print("INIT_DB CALLED")
    with connect_db(validate_schema=False) as conn:
        cursor = conn.cursor()

        # Person logs
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS person_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id INTEGER,
                entry_time TEXT,
                exit_time TEXT,
                duration REAL
            )
        """)

        # Cameras
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cameras (
                id INTEGER PRIMARY KEY,
                name TEXT,
                location TEXT
            )
        """)

        # Zones
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS zones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                camera_id INTEGER,
                name TEXT,
                x1 INTEGER,
                y1 INTEGER,
                x2 INTEGER,
                y2 INTEGER,
                zone_type TEXT
            )
        """)

        # Intrusion logs with video info
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS intrusion_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id INTEGER,
                camera_id INTEGER,
                zone_id INTEGER,
                timestamp TEXT,
                video_file TEXT,
                video_time REAL,
                object_type TEXT
            )
        """)
        conn.commit()


def log_entry(track_id):
    entry_time = datetime.now().isoformat()
    with connect_db(validate_schema=False) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO person_logs (track_id, entry_time)
            VALUES (?, ?)
        """, (track_id, entry_time))
        conn.commit()


def log_exit(track_id, entry_time):
    # entry_time may be passed as string from DB, parse it safely
    if isinstance(entry_time, str):
        try:
            entry_dt = datetime.fromisoformat(entry_time)
        except ValueError:
            entry_dt = datetime.now()
    else:
        entry_dt = entry_time

    exit_time = datetime.now()
    duration = (exit_time - entry_dt).total_seconds()

    with connect_db(validate_schema=False) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE person_logs
            SET exit_time = ?, duration = ?
            WHERE track_id = ? AND exit_time IS NULL
        """, (exit_time.isoformat(), duration, track_id))
        conn.commit()


def log_intrusion(track_id, camera_id, zone_id, video_file, video_time):
    with connect_db(validate_schema=False) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO intrusion_logs
            (track_id, camera_id, zone_id, timestamp, video_file, video_time)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            track_id,
            camera_id,
            zone_id,
            datetime.now().isoformat(),
            video_file,
            video_time
        ))
        conn.commit()