import os
import sqlite3
from pathlib import Path

try:
    import psycopg2
except ImportError:
    psycopg2 = None

DB_PATH = Path(__file__).resolve().with_name("cctv_logs.db")
DATABASE_URL = os.environ.get("DATABASE_URL")


def get_db_path() -> str:
    return str(DB_PATH)


def get_db_type() -> str:
    return "postgres" if DATABASE_URL else "sqlite"


def get_placeholder() -> str:
    return "%s" if get_db_type() == "postgres" else "?"


def adapt_query(query: str) -> str:
    """Translates SQL query parameter placeholders from '?' to '%s' if using Postgres."""
    if get_db_type() == "postgres":
        return query.replace("?", "%s")
    return query


def _connect_absolute_db():
    if get_db_type() == "postgres":
        if psycopg2 is None:
            raise ImportError("psycopg2 is required for PostgreSQL but not installed.")
        return psycopg2.connect(DATABASE_URL)
    else:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(get_db_path())


def _table_exists(cursor, table_name: str) -> bool:
    if get_db_type() == "postgres":
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s)",
            (table_name,),
        )
        return cursor.fetchone()[0]
    else:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        )
        return cursor.fetchone() is not None


def _get_columns(cursor, table_name: str) -> set:
    if get_db_type() == "postgres":
        cursor.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (table_name,),
        )
        return {row[0] for row in cursor.fetchall()}
    else:
        cursor.execute(f"PRAGMA table_info({table_name})")
        return {row[1] for row in cursor.fetchall()}


def _ensure_column(cursor, table_name: str, column_name: str, definition: str) -> None:
    columns = _get_columns(cursor, table_name)
    if column_name not in columns:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def ensure_valid_schema() -> str:
    db_type = get_db_type()
    if db_type == "postgres":
        if psycopg2 is None:
            raise ImportError("psycopg2 is required for PostgreSQL but not installed.")
        try:
            conn = psycopg2.connect(DATABASE_URL)
            cursor = conn.cursor()
            
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id SERIAL PRIMARY KEY,
                    timestamp TEXT,
                    object_type TEXT,
                    track_id INTEGER,
                    global_id INTEGER,
                    camera_id INTEGER,
                    video_path TEXT,
                    frame_number INTEGER,
                    frame_start INTEGER,
                    frame_end INTEGER,
                    video_time REAL,
                    zone_id INTEGER,
                    event_type TEXT,
                    event_mode TEXT,
                    mode_type TEXT,
                    entry_time TEXT,
                    exit_time TEXT,
                    duration REAL DEFAULT 0,
                    stayed INTEGER DEFAULT 0,
                    cameras TEXT,
                    video_paths TEXT
                )
                """
            )
            
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tracking_data (
                    id SERIAL PRIMARY KEY,
                    camera_id INTEGER,
                    video_path TEXT,
                    frame_number INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    global_id INTEGER,
                    object_type TEXT NOT NULL,
                    bbox_x1 INTEGER NOT NULL,
                    bbox_y1 INTEGER NOT NULL,
                    bbox_x2 INTEGER NOT NULL,
                    bbox_y2 INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tracking_data_unique ON tracking_data (camera_id, video_path, frame_number, track_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracking_lookup ON tracking_data (camera_id, video_path, track_id, frame_number)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracking_global_lookup ON tracking_data (global_id, frame_number)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_global_lookup ON events (global_id, timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_mode_timestamp ON events (mode_type, timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_object_type ON events (object_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_track_id ON events (track_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_stayed ON events (stayed)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracking_camera_global_lookup ON tracking_data (camera_id, video_path, global_id, frame_number)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_session_lookup ON events (camera_id, video_path, zone_id, global_id, entry_time, exit_time)")
            
            conn.commit()
            return DATABASE_URL
        except Exception as exc:
            raise RuntimeError(f"PostgreSQL database validation failed: {exc}")
        finally:
            if "conn" in locals():
                conn.close()
    else:
        try:
            conn = _connect_absolute_db()
            cursor = conn.cursor()

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    object_type TEXT,
                    track_id INTEGER,
                    global_id INTEGER,
                    camera_id INTEGER,
                    video_path TEXT,
                    frame_number INTEGER,
                    frame_start INTEGER,
                    frame_end INTEGER,
                    video_time REAL,
                    zone_id INTEGER,
                    event_type TEXT,
                    event_mode TEXT,
                    mode_type TEXT,
                    entry_time TEXT,
                    exit_time TEXT,
                    duration REAL DEFAULT 0,
                    stayed INTEGER DEFAULT 0,
                    cameras TEXT,
                    video_paths TEXT
                )
                """
            )

            _ensure_column(cursor, "events", "frame_number", "INTEGER")
            _ensure_column(cursor, "events", "frame_start", "INTEGER")
            _ensure_column(cursor, "events", "frame_end", "INTEGER")
            _ensure_column(cursor, "events", "global_id", "INTEGER")
            _ensure_column(cursor, "events", "event_mode", "TEXT")
            _ensure_column(cursor, "events", "mode_type", "TEXT")
            _ensure_column(cursor, "events", "entry_time", "TEXT")
            _ensure_column(cursor, "events", "exit_time", "TEXT")
            _ensure_column(cursor, "events", "duration", "REAL DEFAULT 0")
            _ensure_column(cursor, "events", "stayed", "INTEGER DEFAULT 0")
            _ensure_column(cursor, "events", "cameras", "TEXT")
            _ensure_column(cursor, "events", "video_paths", "TEXT")

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tracking_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    camera_id INTEGER,
                    video_path TEXT,
                    frame_number INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    global_id INTEGER,
                    object_type TEXT NOT NULL,
                    bbox_x1 INTEGER NOT NULL,
                    bbox_y1 INTEGER NOT NULL,
                    bbox_x2 INTEGER NOT NULL,
                    bbox_y2 INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

            _ensure_column(cursor, "tracking_data", "global_id", "INTEGER")

            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_tracking_data_unique
                ON tracking_data (camera_id, video_path, frame_number, track_id)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tracking_lookup
                ON tracking_data (camera_id, video_path, track_id, frame_number)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tracking_global_lookup
                ON tracking_data (global_id, frame_number)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_global_lookup
                ON events (global_id, timestamp)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_mode_timestamp
                ON events (mode_type, timestamp)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_object_type
                ON events (object_type)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_track_id
                ON events (track_id)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_stayed
                ON events (stayed)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tracking_camera_global_lookup
                ON tracking_data (camera_id, video_path, global_id, frame_number)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_session_lookup
                ON events (camera_id, video_path, zone_id, global_id, entry_time, exit_time)
                """
            )

            conn.commit()

            required_tables = ("events", "tracking_data")
            missing_tables = [table for table in required_tables if not _table_exists(cursor, table)]
            if missing_tables:
                raise RuntimeError(
                    f"Database schema validation failed for {get_db_path()}: missing tables {missing_tables}"
                )

            return get_db_path()
        except sqlite3.Error as exc:
            raise RuntimeError(
                f"Database schema validation failed for {get_db_path()}: {exc}"
            ) from exc
        finally:
            if "conn" in locals():
                conn.close()


def connect_db(validate_schema: bool = True):
    if validate_schema:
        ensure_valid_schema()
    return _connect_absolute_db()
