"""events times are stored as naive UTC; hour-of-day logic must be server-local."""
import os
import sqlite3
import time
import unittest

from db_schema import local_hour_sql


@unittest.skipUnless(hasattr(time, "tzset"), "needs time.tzset (POSIX) to switch timezone")
class TestLocalHour(unittest.TestCase):
    def setUp(self):
        self._old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Kolkata"  # UTC+05:30, no DST
        time.tzset()

    def tearDown(self):
        if self._old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._old_tz
        time.tzset()

    def _hour(self, ts):
        conn = sqlite3.connect(":memory:")
        try:
            return conn.execute(f"SELECT {local_hour_sql('?', dialect='sqlite')}", (ts,)).fetchone()[0]
        finally:
            conn.close()

    def test_utc_converted_to_local_hour(self):
        self.assertEqual(self._hour("2026-11-03T04:30:00"), "10")  # 04:30 UTC = 10:00 IST
        self.assertEqual(self._hour("2026-11-03T20:00:00"), "01")  # crosses midnight

    def test_postgres_expression_uses_offset(self):
        expr = local_hour_sql("entry_time", dialect="postgres")
        self.assertIn("19800 seconds", expr)  # +05:30
        self.assertIn("HH24", expr)


if __name__ == "__main__":
    unittest.main()
