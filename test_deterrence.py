import unittest
import deterrence


class DeterrenceGatingTests(unittest.TestCase):
    def setUp(self):
        deterrence._last_fire.clear()
        self._orig_cfg = deterrence._cfg_for

    def tearDown(self):
        deterrence._cfg_for = self._orig_cfg

    def _patch_cfg(self, cfg):
        deterrence._cfg_for = lambda camera_id: cfg

    def test_non_after_hours_alert_does_not_fire(self):
        fired = []
        orig = deterrence.trigger
        deterrence.trigger = lambda cid, payload=None: fired.append(cid)
        try:
            deterrence.alert_callback({"alert_type": "fall_detected", "camera_id": 1})
            deterrence.alert_callback({"alert_type": "running_detected", "camera_id": 1})
        finally:
            deterrence.trigger = orig
        self.assertEqual(fired, [], "deterrence must only fire on after-hours intrusion")

    def test_after_hours_alert_fires(self):
        fired = []
        orig = deterrence.trigger
        deterrence.trigger = lambda cid, payload=None: fired.append(cid)
        try:
            deterrence.alert_callback({"alert_type": "after_hours_entry", "camera_id": 3})
        finally:
            deterrence.trigger = orig
        self.assertEqual(fired, [3])

    def test_cooldown_suppresses_second_fire(self):
        self._patch_cfg({"enabled": True, "driver": "log", "cooldown_seconds": 999})
        self.assertTrue(deterrence.trigger(1), "first fire should dispatch")
        self.assertFalse(deterrence.trigger(1), "second fire within cooldown must be suppressed")

    def test_disabled_camera_is_suppressed(self):
        self._patch_cfg({"enabled": False, "driver": "log"})
        self.assertFalse(deterrence.trigger(2))

    def test_missing_camera_id_is_ignored(self):
        self._patch_cfg({"enabled": True, "driver": "log"})
        # alert_callback with no camera_id must not raise
        deterrence.alert_callback({"alert_type": "after_hours_entry"})


if __name__ == "__main__":
    unittest.main()
