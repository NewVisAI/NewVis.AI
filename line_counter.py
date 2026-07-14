"""Directional line-crossing counters (turnstile-style entry/exit counting).

A "line" is a tripwire drawn across a doorway/corridor. As a tracked object's
centroid moves from one side of the line to the other between frames, that
counts as one crossing in a direction (the line's `in_label` vs `out_label`).

Lines are stored in lines.json (normalised 0..1 coords, like zones.json) and
crossings are appended to a `line_crossings` table in the shared events DB.
"""

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from db_schema import adapt_query, connect_db, get_db_type

LINES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lines.json")


# --------------------------------------------------------------------------- #
# Persistence (lines.json)
# --------------------------------------------------------------------------- #
def _load_all() -> List[Dict]:
    if not os.path.exists(LINES_PATH):
        return []
    try:
        with open(LINES_PATH, encoding="utf-8") as handle:
            return json.load(handle).get("lines", [])
    except (json.JSONDecodeError, OSError):
        return []


def _persist(lines: List[Dict]) -> None:
    temp_path = LINES_PATH + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump({"lines": lines}, handle, indent=2)
        os.replace(temp_path, LINES_PATH)
    except Exception as e:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise e


def get_all_lines() -> List[Dict]:
    return _load_all()


def get_camera_lines(camera_id: int) -> List[Dict]:
    return [l for l in _load_all() if l.get("camera_id") == camera_id]


def _next_id(lines: List[Dict]) -> int:
    return max((l.get("id", 0) for l in lines), default=0) + 1


def save_line(line: Dict) -> Dict:
    lines = _load_all()
    if line.get("id") is None:
        line["id"] = _next_id(lines)
    lines = [l for l in lines if l.get("id") != line["id"]]
    line.setdefault("in_label", "in")
    line.setdefault("out_label", "out")
    line.setdefault("name", f"Line {line['id']}")
    lines.append(line)
    _persist(lines)
    return line


def delete_line(line_id: int) -> bool:
    lines = _load_all()
    remaining = [l for l in lines if l.get("id") != line_id]
    if len(remaining) == len(lines):
        return False
    _persist(remaining)
    return True


def ensure_default_line(camera_id: int) -> None:
    """Seed a centre vertical tripwire if a camera has none, so a reprocess
    produces demonstrable counts (mirrors the default-zone behaviour)."""
    if get_camera_lines(camera_id):
        return
    lines = _load_all()
    lines.append({
        "id": _next_id(lines),
        "camera_id": camera_id,
        "name": "Centre tripwire",
        "p1": [0.5, 0.1],
        "p2": [0.5, 0.9],
        "in_label": "right",
        "out_label": "left",
    })
    _persist(lines)


# --------------------------------------------------------------------------- #
# Storage (line_crossings table)
# --------------------------------------------------------------------------- #
def init_lines_db() -> None:
    conn = connect_db(validate_schema=False)
    cursor = conn.cursor()
    pk = ("id SERIAL PRIMARY KEY" if get_db_type() == "postgres"
          else "id INTEGER PRIMARY KEY AUTOINCREMENT")
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS line_crossings (
            {pk},
            line_id INTEGER,
            camera_id INTEGER,
            global_id INTEGER,
            track_id INTEGER,
            object_type TEXT,
            direction TEXT,
            video_path TEXT,
            frame_number INTEGER,
            video_time REAL,
            timestamp TEXT
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lcross_line ON line_crossings (line_id)")
    conn.commit()
    conn.close()


def clear_line_crossings(video_path: Optional[str] = None) -> None:
    conn = connect_db(validate_schema=False)
    cursor = conn.cursor()
    if video_path:
        cursor.execute(adapt_query("DELETE FROM line_crossings WHERE video_path = ?"), (video_path,))
    else:
        cursor.execute("DELETE FROM line_crossings")
    conn.commit()
    conn.close()


def get_counts(video_path: Optional[str] = None) -> List[Dict]:
    """Aggregated per-line in/out counts, joined with line metadata."""
    conn = connect_db(validate_schema=False)
    cursor = conn.cursor()
    sql = "SELECT line_id, direction, COUNT(*) FROM line_crossings"
    params: List[object] = []
    if video_path:
        sql += " WHERE video_path = ?"
        params.append(video_path)
    sql += " GROUP BY line_id, direction"
    cursor.execute(adapt_query(sql), params)
    rows = cursor.fetchall()
    conn.close()

    per_line: Dict[int, Dict[str, int]] = {}
    for line_id, direction, count in rows:
        per_line.setdefault(line_id, {})[direction] = count

    out = []
    lines_by_id = {l["id"]: l for l in _load_all()}
    for line_id, dirs in per_line.items():
        meta = lines_by_id.get(line_id, {})
        in_lbl = meta.get("in_label", "in")
        out_lbl = meta.get("out_label", "out")
        c_in = dirs.get(in_lbl, 0)
        c_out = dirs.get(out_lbl, 0)
        out.append({
            "line_id": line_id,
            "name": meta.get("name", f"Line {line_id}"),
            "camera_id": meta.get("camera_id"),
            "in_label": in_lbl,
            "out_label": out_lbl,
            "in": c_in,
            "out": c_out,
            "net": c_in - c_out,
            "total": c_in + c_out,
        })
    # include configured lines that have zero crossings
    for line_id, meta in lines_by_id.items():
        if line_id not in per_line:
            out.append({
                "line_id": line_id, "name": meta.get("name", f"Line {line_id}"),
                "camera_id": meta.get("camera_id"),
                "in_label": meta.get("in_label", "in"), "out_label": meta.get("out_label", "out"),
                "in": 0, "out": 0, "net": 0, "total": 0,
            })
    return sorted(out, key=lambda r: r["line_id"])


# --------------------------------------------------------------------------- #
# Crossing detection
# --------------------------------------------------------------------------- #
def build_pixel_lines(line_defs: List[Dict], frame_shape) -> List[Dict]:
    h, w = frame_shape[:2]
    pixel = []
    for ln in line_defs:
        p1, p2 = ln.get("p1"), ln.get("p2")
        if not p1 or not p2:
            continue
        pixel.append({
            "id": ln.get("id"),
            "name": ln.get("name", f"Line {ln.get('id')}"),
            "p1": (p1[0] * w, p1[1] * h),
            "p2": (p2[0] * w, p2[1] * h),
            "in_label": ln.get("in_label", "in"),
            "out_label": ln.get("out_label", "out"),
        })
    return pixel


def _side(p1: Tuple[float, float], p2: Tuple[float, float], pt: Tuple[float, float]) -> float:
    """Signed area — >0 on one side of the directed line p1->p2, <0 on the other."""
    return (p2[0] - p1[0]) * (pt[1] - p1[1]) - (p2[1] - p1[1]) * (pt[0] - p1[0])


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0).isoformat()


class LineCrossingCounter:
    """Tracks each object's side of each line and records sign changes."""

    def __init__(self, pixel_lines: List[Dict]):
        self.lines = pixel_lines
        self._last_side: Dict[Tuple[int, int], int] = {}  # (line_id, track_id) -> -1/1
        self._pending: List[tuple] = []

    def update(self, camera_id, track_id, global_id, object_type,
               centroid, frame_number, video_time, video_path) -> None:
        for ln in self.lines:
            key = (ln["id"], track_id)
            raw = _side(ln["p1"], ln["p2"], centroid)
            sign = 1 if raw > 0 else (-1 if raw < 0 else 0)
            if sign == 0:
                continue
            prev = self._last_side.get(key)
            if prev is not None and prev != sign:
                direction = ln["in_label"] if sign > 0 else ln["out_label"]
                self._pending.append((
                    ln["id"], camera_id, global_id, track_id, object_type,
                    direction, video_path, frame_number, video_time, _now_iso(),
                ))
            self._last_side[key] = sign

    def flush(self) -> None:
        if not self._pending:
            return
        conn = connect_db(validate_schema=False)
        cursor = conn.cursor()
        cursor.executemany(
            adapt_query(
                "INSERT INTO line_crossings (line_id, camera_id, global_id, track_id, "
                "object_type, direction, video_path, frame_number, video_time, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            self._pending,
        )
        conn.commit()
        conn.close()
        self._pending = []
