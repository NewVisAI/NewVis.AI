"""
Automated daily / monthly / yearly summary reports.

Works for both live feeds and processed downloaded videos, because it reads
straight from the events / alerts / tracking tables that every ingest path
writes to. Each report highlights the things a school actually cares about:

  - Headcount (distinct people) and total visits, overall and per zone
  - Crowd density / congestion: peak simultaneous people in view, per-zone
    occupancy breaches, and the busiest hour
  - Safety alert breakdown (intrusion, after-hours, fall, running, dress code,
    occupancy) with counts
  - Dwell / loitering: longest and average stays

A background scheduler can call generate("daily") once a day; the same
function backs the on-demand REST endpoint and the CLI.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from db_schema import adapt_query, connect_db

PERIODS = {
    "daily": timedelta(days=1),
    "monthly": timedelta(days=30),
    "yearly": timedelta(days=365),
    "all": None,
}
PERIOD_LABELS = {
    "daily": "Last 24 Hours",
    "monthly": "Last 30 Days",
    "yearly": "Last 365 Days",
    "all": "All Time",
}


def _cutoffs(period: str):
    delta = PERIODS.get(period)
    if delta is None:
        return None, None
    now_utc = datetime.utcnow()
    now_local = datetime.now()
    return (now_utc - delta).replace(microsecond=0).isoformat(), (now_local - delta).replace(microsecond=0).isoformat()


def generate(period: str = "daily") -> Dict[str, Any]:
    if period not in PERIODS:
        period = "daily"
    events_cutoff, alerts_cutoff = _cutoffs(period)

    conn = connect_db(validate_schema=False)
    cur = conn.cursor()

    def ev_clause(col="entry_time"):
        return (f" AND {col} >= ?", [events_cutoff]) if events_cutoff else ("", [])

    report: Dict[str, Any] = {
        "period": period,
        "period_label": PERIOD_LABELS.get(period, period),
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
    }

    # --- Headcount + visits --------------------------------------------------
    clause, params = ev_clause()
    cur.execute(adapt_query(
        f"SELECT COUNT(DISTINCT global_id) FROM events WHERE object_type='person'{clause}"), params)
    report["headcount"] = cur.fetchone()[0] or 0

    cur.execute(adapt_query(
        f"SELECT COUNT(*) FROM events WHERE object_type='person' AND event_type='leaving'{clause}"), params)
    report["total_person_visits"] = cur.fetchone()[0] or 0

    cur.execute(adapt_query(
        f"SELECT object_type, COUNT(*) FROM events WHERE object_type IN "
        f"('car','truck','bus','motorcycle','bicycle'){clause} GROUP BY object_type"), params)
    vehicles = {r[0]: r[1] for r in cur.fetchall()}
    report["vehicles"] = {"total": sum(vehicles.values()), "by_type": vehicles}

    # --- Per-zone headcount --------------------------------------------------
    cur.execute(adapt_query(
        f"SELECT zone_id, COUNT(DISTINCT global_id) FROM events WHERE object_type='person'{clause} "
        f"GROUP BY zone_id ORDER BY zone_id"), params)
    report["zone_headcount"] = {str(r[0]): r[1] for r in cur.fetchall()}

    # --- Crowd density / congestion -----------------------------------------
    # Peak simultaneous people in any single frame (a good congestion proxy).
    cur.execute(
        """
        SELECT MAX(cnt) FROM (
            SELECT COUNT(DISTINCT global_id) cnt
            FROM tracking_data
            WHERE object_type='person'
            GROUP BY video_path, camera_id, frame_number
        )
        """
    )
    row = cur.fetchone()
    report["peak_simultaneous_people"] = (row[0] if row and row[0] is not None else 0)

    # Per-zone occupancy breaches (crowding events). Occupancy alerts store the
    # observed count in `duration` and the limit in `stayed`.
    clause_ts, params_ts = ev_clause("timestamp")
    cur.execute(adapt_query(
        f"SELECT zone_id, COUNT(*), MAX(duration), MAX(stayed) FROM events "
        f"WHERE event_type='occupancy_alert'{clause_ts} GROUP BY zone_id"), params_ts)
    breaches = []
    for zid, cnt, peak_count, limit in cur.fetchall():
        breaches.append({
            "zone_id": zid,
            "breach_events": cnt,
            "peak_occupancy": int(peak_count) if peak_count is not None else None,
            "limit": int(limit) if limit is not None else None,
        })
    report["occupancy_breaches"] = breaches
    report["total_occupancy_breaches"] = sum(b["breach_events"] for b in breaches)

    # Busiest hour by session entries.
    cur.execute(adapt_query(
        f"SELECT substr(entry_time,12,2) hr, COUNT(*) c FROM events "
        f"WHERE entry_time IS NOT NULL AND event_type='leaving'{clause} "
        f"GROUP BY hr ORDER BY c DESC LIMIT 1"), params)
    busiest = cur.fetchone()
    report["busiest_hour"] = ({"hour": busiest[0], "sessions": busiest[1]} if busiest and busiest[0] else None)

    # --- Dwell / loitering ---------------------------------------------------
    cur.execute(adapt_query(
        f"SELECT AVG(duration), MAX(duration) FROM events WHERE event_type='leaving'{clause}"), params)
    avg_d, max_d = cur.fetchone()
    report["avg_dwell_seconds"] = round(avg_d or 0.0, 1)
    report["longest_dwell_seconds"] = round(max_d or 0.0, 1)
    cur.execute(adapt_query(
        f"SELECT COUNT(*) FROM events WHERE event_type='leaving' AND stayed=1{clause}"), params)
    report["loitering_sessions"] = cur.fetchone()[0] or 0

    # --- Safety alerts breakdown --------------------------------------------
    alert_counts: Dict[str, int] = {}

    def _table_exists(name):
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
        return cur.fetchone() is not None

    if _table_exists("alerts"):
        a_clause = " WHERE timestamp >= ?" if alerts_cutoff else ""
        a_params = [alerts_cutoff] if alerts_cutoff else []
        cur.execute(adapt_query(
            f"SELECT alert_type, COUNT(*) FROM alerts{a_clause} GROUP BY alert_type"), a_params)
        for atype, c in cur.fetchall():
            alert_counts[atype] = c
    # Event-table alerts (dress code / running) live in events.
    for etype in ("dress_code_violation", "running_detected"):
        cur.execute(adapt_query(
            f"SELECT COUNT(*) FROM events WHERE event_type=?{clause_ts}"), [etype] + params_ts)
        c = cur.fetchone()[0] or 0
        if c:
            alert_counts[etype] = c
    report["alerts"] = alert_counts
    report["total_alerts"] = sum(alert_counts.values())

    conn.close()
    return report


def render_text(report: Dict[str, Any]) -> str:
    lines = []
    lines.append("=" * 56)
    lines.append(f"SENTINEL AI — {report['period_label'].upper()} SUMMARY")
    lines.append(f"Generated: {report['generated_at']}")
    lines.append("=" * 56)
    lines.append(f"Headcount (unique people) : {report['headcount']}")
    lines.append(f"Total person visits       : {report['total_person_visits']}")
    lines.append(f"Vehicles                  : {report['vehicles']['total']}")
    lines.append("")
    lines.append("CROWD DENSITY / CONGESTION")
    lines.append(f"  Peak simultaneous people : {report['peak_simultaneous_people']}")
    lines.append(f"  Occupancy breach events  : {report['total_occupancy_breaches']}")
    for b in report["occupancy_breaches"]:
        lines.append(f"    - Zone {b['zone_id']}: peak {b['peak_occupancy']} (limit {b['limit']}), "
                     f"{b['breach_events']} breach(es)")
    if report.get("busiest_hour"):
        bh = report["busiest_hour"]
        lines.append(f"  Busiest hour             : {bh['hour']}:00 ({bh['sessions']} sessions)")
    lines.append("")
    lines.append("HEADCOUNT BY ZONE")
    for zid, cnt in report["zone_headcount"].items():
        lines.append(f"  - Zone {zid}: {cnt} unique people")
    lines.append("")
    lines.append("DWELL TIME")
    lines.append(f"  Avg dwell      : {report['avg_dwell_seconds']}s")
    lines.append(f"  Longest dwell  : {report['longest_dwell_seconds']}s")
    lines.append("")
    lines.append("SAFETY ALERTS")
    if report["alerts"]:
        for atype, c in report["alerts"].items():
            lines.append(f"  - {atype.replace('_',' ').title()}: {c}")
    else:
        lines.append("  - None")
    lines.append(f"  Total alerts: {report['total_alerts']}")
    lines.append("=" * 56)
    return "\n".join(lines)
