import unittest
from unittest.mock import patch

import alerts
import camera_registry
from db_schema import connect_db
from event import clear_event_logs, init_db
from event_graph import EventGraph

# Small adjacency chain standing in for camera_registry.get_topology():
# 1 - 2 - 3 - 4 - 5 - 6   (a wing, e.g. Library -> ... -> Gym -> GymStorage)
# 7 - 8                   (an unrelated corridor pair)
TEST_TOPOLOGY = [
    {"a": 1, "b": 2}, {"a": 2, "b": 3}, {"a": 3, "b": 4},
    {"a": 4, "b": 5}, {"a": 5, "b": 6},
    {"a": 7, "b": 8},
]


def _insert_events(rows):
    conn = connect_db(validate_schema=False)
    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT INTO events
        (timestamp, object_type, track_id, global_id, camera_id, video_path,
         frame_number, frame_start, frame_end, video_time, zone_id,
         event_type, entry_time, exit_time, duration, stayed)
        VALUES (?, 'person', 1, ?, ?, 'cam.mp4', 1, 1, 1, 1.0, ?, 'entering', ?, NULL, 0, 0)
        """,
        rows,
    )
    conn.commit()
    conn.close()


def _insert_alert(alert_id_ts_type_camera_gid_zone):
    """Inserts alert rows and returns their assigned ids, in insertion order.

    `alerts.id` is AUTOINCREMENT against the shared on-disk cctv_logs.db, whose
    sequence counter persists across test runs and never resets to 1 - callers
    must use the returned ids rather than assuming any particular value.
    """
    conn = connect_db(validate_schema=False)
    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT INTO alerts (timestamp, alert_type, camera_id, global_id, zone_id, zone_name)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        alert_id_ts_type_camera_gid_zone,
    )
    conn.commit()
    cursor.execute(
        "SELECT id FROM alerts ORDER BY id DESC LIMIT ?",
        (len(alert_id_ts_type_camera_gid_zone),),
    )
    ids = [row[0] for row in cursor.fetchall()][::-1]
    conn.close()
    return ids


@patch.object(camera_registry, "get_topology", return_value=TEST_TOPOLOGY)
class EventGraphTests(unittest.TestCase):
    def setUp(self):
        init_db()
        clear_event_logs()
        alerts.init_alerts_db()
        alerts.clear_alerts_log()

    def tearDown(self):
        clear_event_logs()
        alerts.clear_alerts_log()

    def test_trace_reconstructs_path_before_an_alert(self, _mock_topology):
        # (timestamp, global_id, camera_id, zone_id, entry_time) - P4 walks
        # cam1 -> cam2 -> cam3 in the hour before a fall at cam4.
        _insert_events([
            ("2026-07-01T10:00:00", 4, 1, 10, "2026-07-01T10:00:00"),
            ("2026-07-01T10:20:00", 4, 2, 11, "2026-07-01T10:20:00"),
            ("2026-07-01T10:40:00", 4, 3, 12, "2026-07-01T10:40:00"),
        ])
        _insert_alert([("2026-07-01T11:00:00", "fall_detected", 4, 4, 13, "Gym")])

        graph = EventGraph()
        trail = graph.trace(global_id=4, before="2026-07-01T11:00:00", window_seconds=7200)

        self.assertEqual([row["camera_id"] for row in trail], [1, 2, 3])
        self.assertEqual(trail[0]["ts"], "2026-07-01T10:00:00")

    def test_trace_excludes_sightings_outside_the_window(self, _mock_topology):
        _insert_events([
            ("2026-07-01T05:00:00", 4, 1, 10, "2026-07-01T05:00:00"),  # too early
        ])
        _insert_alert([("2026-07-01T11:00:00", "fall_detected", 4, 4, 13, "Gym")])

        graph = EventGraph()
        trail = graph.trace(global_id=4, before="2026-07-01T11:00:00", window_seconds=3600)

        self.assertEqual(trail, [])

    def test_recurring_actors_finds_persons_spanning_multiple_days(self, _mock_topology):
        _insert_alert([
            ("2026-07-01T09:00:00", "restricted_zone_entry", 6, 4, 20, "GymStorage"),
            ("2026-07-10T09:00:00", "fall_detected", 6, 4, 20, "GymStorage"),
            ("2026-07-05T09:00:00", "after_hours_entry", 7, 11, 21, "CorridorB"),
            ("2026-07-05T09:10:00", "loiter", 8, 11, 22, "CorridorB2"),
        ])

        graph = EventGraph()
        recurring = graph.recurring_actors(min_alerts=2, min_span_seconds=86400)

        global_ids = {row["global_id"] for row in recurring}
        self.assertIn(4, global_ids)
        # 11's two alerts are 10 minutes apart - shouldn't clear a 1-day span.
        self.assertNotIn(11, global_ids)

    def test_recurring_actors_ignores_single_alert_persons(self, _mock_topology):
        _insert_alert([("2026-07-01T09:00:00", "fall_detected", 1, 99, 10, "Library")])

        graph = EventGraph()
        recurring = graph.recurring_actors(min_alerts=2, min_span_seconds=86400)

        self.assertEqual(recurring, [])

    def test_cascades_finds_alert_on_adjacent_camera_within_window(self, _mock_topology):
        ids = _insert_alert([
            ("2026-07-05T13:00:00", "violence_detected", 7, 4, 30, "CorridorB"),
            ("2026-07-05T13:02:00", "loiter", 8, 22, 31, "CorridorB2"),
        ])

        graph = EventGraph()
        cascades = graph.cascades(alert_id=ids[0], max_hops=2, window_seconds=600)

        self.assertEqual(len(cascades), 1)
        self.assertEqual(cascades[0]["alert_id"], ids[1])
        self.assertEqual(cascades[0]["camera_id"], 8)
        self.assertEqual(cascades[0]["camera_hops"], 1)

    def test_cascades_ignores_alerts_outside_the_time_window(self, _mock_topology):
        ids = _insert_alert([
            ("2026-07-05T13:00:00", "violence_detected", 7, 4, 30, "CorridorB"),
            ("2026-07-05T14:00:00", "loiter", 8, 22, 31, "CorridorB2"),  # 1h later
        ])

        graph = EventGraph()
        cascades = graph.cascades(alert_id=ids[0], max_hops=2, window_seconds=600)

        self.assertEqual(cascades, [])

    def test_cascades_ignores_alerts_beyond_max_hops(self, _mock_topology):
        ids = _insert_alert([
            ("2026-07-01T09:00:00", "fall_detected", 1, 4, 10, "Library"),
            ("2026-07-01T09:01:00", "loiter", 6, 22, 13, "GymStorage"),  # 5 hops away
        ])

        graph = EventGraph()
        cascades = graph.cascades(alert_id=ids[0], max_hops=2, window_seconds=600)

        self.assertEqual(cascades, [])


if __name__ == "__main__":
    unittest.main()
