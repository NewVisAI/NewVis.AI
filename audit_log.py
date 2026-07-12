"""
Basic audit trail — who viewed/did what, and when.

Every sensitive action in the backend (login, running a search, viewing an
alert snapshot, opening a clip, watching a live camera, generating a report,
changing zones/users) is recorded here with the acting user, their role, the
action, the target, and a timestamp. Stored in the shared events database so
it works on both SQLite and PostgreSQL.

The trail is append-only for everyone except the developer role: principal and
tech can view it but cannot delete, and the only purge path is a developer-only
DELETE /api/audit (clear_audit_log below), which itself records who cleared it.
"""

from datetime import datetime
from typing import Dict, List, Optional

from db_schema import adapt_query, connect_db, get_db_type


def init_audit_db() -> None:
    conn = connect_db(validate_schema=False)
    cursor = conn.cursor()
    pk = (
        "id SERIAL PRIMARY KEY"
        if get_db_type() == "postgres"
        else "id INTEGER PRIMARY KEY AUTOINCREMENT"
    )
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS audit_log (
            {pk},
            timestamp TEXT,
            username TEXT,
            role TEXT,
            action TEXT,
            target TEXT,
            details TEXT,
            ip TEXT
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log (timestamp)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log (username)")
    conn.commit()
    conn.close()


def record_audit(
    username: Optional[str],
    role: Optional[str],
    action: str,
    target: Optional[str] = None,
    details: Optional[str] = None,
    ip: Optional[str] = None,
) -> None:
    """Append one entry. Never raises into the request path."""
    try:
        conn = connect_db(validate_schema=False)
        cursor = conn.cursor()
        cursor.execute(
            adapt_query(
                """
                INSERT INTO audit_log (timestamp, username, role, action, target, details, ip)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """
            ),
            (
                datetime.now().replace(microsecond=0).isoformat(),
                username or "-",
                role or "-",
                action,
                target,
                details,
                ip,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # auditing must never break the action it records
        print(f"[AUDIT] failed to record '{action}': {exc}")


def clear_audit_log() -> int:
    """Purge every audit entry and return how many rows were removed.

    Developer-only (exposed via DELETE /api/audit). The caller is expected to
    record a 'clear_audit' entry *after* calling this, so the freshly emptied
    trail still shows who performed the purge.
    """
    conn = connect_db(validate_schema=False)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM audit_log")
    removed = cursor.fetchone()[0]
    cursor.execute("DELETE FROM audit_log")
    conn.commit()
    conn.close()
    return removed


def get_audit_log(
    username: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 200,
) -> List[Dict]:
    conn = connect_db(validate_schema=False)
    cursor = conn.cursor()
    sql = "SELECT id, timestamp, username, role, action, target, details, ip FROM audit_log WHERE 1=1"
    params: List[object] = []
    if username:
        sql += " AND username = ?"
        params.append(username)
    if action:
        sql += " AND action = ?"
        params.append(action)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    cursor.execute(adapt_query(sql), params)
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "timestamp": r[1],
            "username": r[2],
            "role": r[3],
            "action": r[4],
            "target": r[5],
            "details": r[6],
            "ip": r[7],
        }
        for r in rows
    ]
