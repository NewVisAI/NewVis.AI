"""
Reset runtime/detection data for a clean live test run.

Clears the accumulated detection/alert tables (alerts, events, tracking, counts,
notifications) so a fresh live run starts from zero — WITHOUT touching config
(cameras, zones) or the audit log.

Usage:
    SENTINEL_DB_PATH=/root/sentinel_data/cctv_logs.db python reset_live_data.py --yes
"""

import os
import sqlite3
import sys

DB = os.environ.get("SENTINEL_DB_PATH") or "cctv_logs.db"

# Detection/runtime data — safe to wipe for a clean run.
DATA_TABLES = [
    "alerts", "events", "tracking_data",
    "intrusion_logs", "line_crossings",
    "notifications", "person_logs",
]
# Kept: cameras, zones (config), audit_log (history).


def _existing_tables(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def main(assume_yes: bool = False) -> dict:
    """Wipe detection tables. Returns counts of rows removed per table.

    Called from CLI (pass `--yes` in argv) or programmatically
    (pass assume_yes=True). Config tables (cameras, zones) and audit_log
    are preserved.
    """
    if not os.path.exists(DB):
        print(f"[!] DB not found: {DB}")
        if __name__ == "__main__":
            sys.exit(1)
        return {}

    conn = sqlite3.connect(DB)
    present = _existing_tables(conn)

    print(f"DB: {DB}")
    print("Before:")
    to_clear = [t for t in DATA_TABLES if t in present]
    before_counts: dict = {}
    for t in to_clear:
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        before_counts[t] = n
        print(f"  {t:16s} {n} rows")

    if not assume_yes and "--yes" not in sys.argv:
        print("\nDry run (pass --yes to actually clear). Nothing changed.")
        conn.close()
        return {"dry_run": True, "before": before_counts}

    for t in to_clear:
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    conn.execute("VACUUM")
    conn.commit()

    print("\nAfter:")
    for t in to_clear:
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:16s} {n} rows")
    conn.close()
    print("\n[+] Reset complete. Config (cameras, zones) untouched.")
    return {"cleared": before_counts}


if __name__ == "__main__":
    main()
