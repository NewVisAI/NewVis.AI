from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from db_schema import adapt_query, connect_db

TIME_FRAME_DELTAS = {
    "day": timedelta(days=1),
    "week": timedelta(days=7),
    "month": timedelta(days=30),
}
PERIOD_LABELS = {
    "day": "Last 24 Hours",
    "week": "Last 7 Days",
    "month": "Last 30 Days",
    "all": "All Time",
}


class ModeManager:
    """
    Statistical reporting over the events database (headcount, vehicles,
    prolonged stays), optionally scoped to a day/week/month window.
    """

    def _cutoff_iso(self, time_frame: str) -> Optional[str]:
        delta = TIME_FRAME_DELTAS.get(time_frame)
        if delta is None:
            return None
        # event.py stores entry_time as UTC; match that here.
        return (datetime.utcnow() - delta).replace(microsecond=0).isoformat()

    def get_summary(self, time_frame: str = "all") -> Dict[str, Any]:
        """
        Headcount is a distinct-person count (unique global_id), not a raw
        count of zone-entry events/sessions — re-entries and tracker ID
        fragmentation would otherwise inflate the number.
        """
        conn = connect_db(validate_schema=False)
        cursor = conn.cursor()

        cutoff = self._cutoff_iso(time_frame)
        time_clause = " AND entry_time >= ?" if cutoff else ""
        params = [cutoff] if cutoff else []

        summary: Dict[str, Any] = {
            "time_frame": time_frame,
            "headcount": 0,
            "total_vehicles": 0,
            "total_people_visits": 0,
            "vehicle_types": {},
            "suspicious_activities": [],
            "zone_headcount": {},
        }

        cursor.execute(
            adapt_query(
                f"SELECT COUNT(DISTINCT global_id) FROM events WHERE object_type = 'person'{time_clause}"
            ),
            params,
        )
        row = cursor.fetchone()
        summary["headcount"] = row[0] if row and row[0] is not None else 0

        cursor.execute(
            adapt_query(
                f"SELECT object_type, COUNT(*) FROM events WHERE 1=1{time_clause} GROUP BY object_type"
            ),
            params,
        )
        for obj_type, count in cursor.fetchall():
            if obj_type in ("car", "truck", "bus", "motorcycle", "bicycle"):
                summary["total_vehicles"] += count
                summary["vehicle_types"][obj_type] = summary["vehicle_types"].get(obj_type, 0) + count
            elif obj_type == "person":
                summary["total_people_visits"] = count

        cursor.execute(
            adapt_query(
                f"""
                SELECT zone_id, COUNT(DISTINCT global_id)
                FROM events
                WHERE object_type = 'person'{time_clause}
                GROUP BY zone_id
                """
            ),
            params,
        )
        for zone_id, count in cursor.fetchall():
            summary["zone_headcount"][zone_id] = count

        cursor.execute(
            adapt_query(
                f"SELECT object_type, zone_id, entry_time, duration FROM events WHERE stayed = 1{time_clause}"
            ),
            params,
        )
        for obj_type, zone_id, entry_time, duration in cursor.fetchall():
            summary["suspicious_activities"].append({
                "type": "Extended Dwell",
                "object": obj_type,
                "zone": zone_id,
                "time": entry_time,
                "duration": f"{duration:.1f}s",
            })

        conn.close()
        return summary

    def print_summary_report(self, use_zones: bool = False, time_frame: str = "all"):
        """
        Prints a formatted AI summary report to the console for the given period
        ("day", "week", "month", or "all").
        """
        data = self.get_summary(time_frame=time_frame)
        period_label = PERIOD_LABELS.get(time_frame, time_frame)

        print("\n" + "=" * 50)
        print("🤖 AI SECURITY SUMMARY")
        print(f"📅 Period: {period_label}")
        print("=" * 50)
        print(f"👥 Headcount (unique people): {data['headcount']}")

        if use_zones:
            print("📍 ZONE-SPECIFIC ACTIVITY REPORT")

            if data["zone_headcount"]:
                print("\nHeadcount by zone:")
                for zone_id, count in data["zone_headcount"].items():
                    print(f" • Zone {zone_id}: {count} unique people")

            zones_activity: Dict[Any, list] = {}
            for activity in data['suspicious_activities']:
                z_id = activity['zone']
                zones_activity.setdefault(z_id, []).append(activity)

            if not zones_activity:
                print("\n   No significant activity detected in defined zones.")
            else:
                for z_id, activities in zones_activity.items():
                    print(f"\n--- Zone {z_id} ---")
                    for act in activities:
                        print(f" • {act['object'].capitalize()} detected for {act['duration']} starting at {act['time']}")
        else:
            print("🌍 FULL-FRAME SCENE DESCRIPTION")
            print("\nOverview: The surveillance system has analyzed the entire frame.")
            print(f"• Unique People (headcount): {data['headcount']}")
            print(f"• Total Person Visits (incl. re-entries): {data['total_people_visits']}")
            print(f"• Total Vehicles: {data['total_vehicles']}")
            if data['vehicle_types']:
                v_list = [f"{count} {vtype}(s)" for vtype, count in data['vehicle_types'].items()]
                print(f"  (Breakdown: {', '.join(v_list)})")

            print("\nSuspicious Activities (Whole Frame):")
            if not data['suspicious_activities']:
                print(" • All clear. No unusual prolonged stays detected.")
            else:
                for act in data['suspicious_activities']:
                    print(f" • Unusual {act['type']}: {act['object'].capitalize()} observed at {act['time']} for {act['duration']}.")

        print("\n" + "=" * 50)
