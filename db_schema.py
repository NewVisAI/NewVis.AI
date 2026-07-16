import os
import sqlite3
from pathlib import Path

try:
    import psycopg2
except ImportError:
    psycopg2 = None

# SQLite DB location. Defaults to next to this file, but can be overridden with
# SENTINEL_DB_PATH — important under WSL, where a DB on /mnt/* (drvfs) makes every
# connection-open take ~3s; pointing it at native ext4 storage is ~1000x faster.
DB_PATH = Path(os.environ.get("SENTINEL_DB_PATH") or Path(__file__).resolve().with_name("cctv_logs.db"))
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


# Shared multiprocessing lock for database write safety
db_lock = None

class LockedConnection:
    def __init__(self, conn, lock):
        self.conn = conn
        self.lock = lock
        self.lock.acquire()
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
        
    def cursor(self, *args, **kwargs):
        return self.conn.cursor(*args, **kwargs)
        
    def commit(self, *args, **kwargs):
        return self.conn.commit(*args, **kwargs)
        
    def rollback(self, *args, **kwargs):
        return self.conn.rollback(*args, **kwargs)
        
    def execute(self, *args, **kwargs):
        return self.conn.execute(*args, **kwargs)
        
    def close(self):
        try:
            self.conn.close()
        finally:
            try:
                self.lock.release()
            except ValueError:
                pass # Already released

def _connect_absolute_db():
    if get_db_type() == "postgres":
        if psycopg2 is None:
            raise ImportError("psycopg2 is required for PostgreSQL but not installed.")
        return psycopg2.connect(DATABASE_URL)
    else:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        db_file = get_db_path()
        try:
            conn = sqlite3.connect(db_file, check_same_thread=False, timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            # Run a fast integrity check on connection to verify the database is not corrupted
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check(1);")
            row = cur.fetchone()
            if row and "ok" not in str(row[0]).lower():
                raise sqlite3.DatabaseError("SQLite integrity check failed")
            return conn
        except (sqlite3.DatabaseError, sqlite3.OperationalError) as err:
            print(f"[DATABASE EMERGENCY] SQLite corruption detected on connection: {err}. Initiating auto-repair...", flush=True)
            try:
                if 'conn' in locals():
                    conn.close()
            except Exception:
                pass
            import time as t_mod
            backup_path = f"{db_file}.corrupt_{int(t_mod.time())}"
            try:
                if os.path.exists(db_file):
                    os.rename(db_file, backup_path)
                    print(f"[DATABASE EMERGENCY] Moved corrupted database file to: {backup_path}", flush=True)
                # Retry connection on a fresh file
                conn = sqlite3.connect(db_file, check_same_thread=False, timeout=30.0)
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                return conn
            except Exception as backup_err:
                print(f"[DATABASE CRITICAL ERROR] Database auto-repair failed: {backup_err}", flush=True)
                raise err


def connect_db(validate_schema: bool = True):
    if validate_schema:
        try:
            ensure_valid_schema()
        except Exception as schema_err:
            print(f"[DATABASE EMERGENCY] Schema validation failed: {schema_err}. Attempting auto-repair...", flush=True)
            db_file = get_db_path()
            if get_db_type() == "sqlite" and os.path.exists(db_file):
                import time as t_mod
                backup_path = f"{db_file}.corrupt_{int(t_mod.time())}"
                try:
                    os.rename(db_file, backup_path)
                    print(f"[DATABASE EMERGENCY] Schema repair success: moved corrupted DB to {backup_path}", flush=True)
                    ensure_valid_schema()
                except Exception as repair_err:
                    print(f"[DATABASE CRITICAL ERROR] Schema repair failed: {repair_err}", flush=True)
    # Exponential backoff auto-retry logic to handle database locking / concurrency spikes
    import time as t_mod
    max_retries = 3
    delay = 0.5
    for attempt in range(max_retries):
        try:
            conn = _connect_absolute_db()
            break
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"[DATABASE CRITICAL ERROR] Database connection attempts exhausted: {e}", flush=True)
                raise e
            print(f"[DATABASE WARNING] Connection attempt {attempt + 1} failed: {e}. Retrying in {delay}s...", flush=True)
            t_mod.sleep(delay)
            delay *= 2

    if db_lock is not None:
        return LockedConnection(conn, db_lock)
    return conn


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
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_video_time ON events (video_path, video_time)")
            
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
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_video_time
                ON events (video_path, video_time)
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



