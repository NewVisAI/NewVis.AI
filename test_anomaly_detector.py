"""
Unit tests for the heuristic altercation / anomaly detector.

Exercise the accuracy-shaping additions:
  * Duration threshold: brief spikes cannot fire regardless of streak count.
  * Oscillation gate: sustained proximity + high rel-motion is not enough by
    itself — the relative velocity must reverse direction (real shoving).
  * Crowd-context annotation: nearby people are counted into details (never
    suppresses the alert).
  * Alert dedup via cooldown: sustained jostle logs one event, not one per
    frame.

Every test forces the tunable knobs to deterministic values so a
deployment.json on the host machine cannot break the fixtures.

The alerts pipeline is stubbed — this file tests DETECTOR LOGIC, not the DB
write path (that is covered by the integration tests).
"""

import os
import unittest
from unittest import mock

import inference_config
import anomaly_detector
from anomaly_detector import check_anomaly, reset_anomaly_state


BBOX_W = 40
BBOX_H = 160


def _person(track_id, global_id, cx, cy, w=BBOX_W, h=BBOX_H):
    return {
        "track_id": track_id,
        "global_id": global_id,
        "cx": float(cx),
        "cy": float(cy),
        "w": float(w),
        "h": float(h),
        "bbox": (int(cx - w / 2), int(cy - h / 2), int(cx + w / 2), int(cy + h / 2)),
    }


class AnomalyDetectorTests(unittest.TestCase):
    def setUp(self):
        reset_anomaly_state()
        for k in (
            "ANOMALY_DETECTION",
            "ANOMALY_STREAK",
            "ANOMALY_MIN_DURATION_S",
            "ANOMALY_OSCILLATION_MIN_FLIPS",
            "ANOMALY_REL_MOTION",
            "ANOMALY_MOVE_FLOOR",
            "ANOMALY_PROXIMITY",
            "ANOMALY_COOLDOWN_S",
            "RAISED_ARMS_CHECK",
        ):
            os.environ.pop(k, None)
        # Deterministic tunables for the fixtures.
        os.environ["ANOMALY_STREAK"] = "3"
        os.environ["ANOMALY_MIN_DURATION_S"] = "0.3"
        os.environ["ANOMALY_OSCILLATION_MIN_FLIPS"] = "2"
        os.environ["ANOMALY_REL_MOTION"] = "1.0"
        os.environ["ANOMALY_MOVE_FLOOR"] = "0.2"
        os.environ["ANOMALY_PROXIMITY"] = "3.0"
        inference_config.reload()
        self._alert_patch = mock.patch.object(anomaly_detector, "raise_violence_alert")
        self.mock_alert = self._alert_patch.start()

    def tearDown(self):
        self._alert_patch.stop()
        for k in (
            "ANOMALY_STREAK",
            "ANOMALY_MIN_DURATION_S",
            "ANOMALY_OSCILLATION_MIN_FLIPS",
            "ANOMALY_REL_MOTION",
            "ANOMALY_MOVE_FLOOR",
            "ANOMALY_PROXIMITY",
            "ANOMALY_COOLDOWN_S",
            "RAISED_ARMS_CHECK",
        ):
            os.environ.pop(k, None)
        inference_config.reload()
        reset_anomaly_state()

    def _drive(self, frames, dt=0.1):
        """frames = list of lists of person dicts. Returns list of all fired events."""
        fired = []
        t = 0.0
        for people in frames:
            events = check_anomaly(camera_id=1, people=people, video_time=t, video_path="")
            fired.extend(events)
            t += dt
        return fired

    def _oscillating_pair(self, steps, amplitude=30, base_x=400, base_y=400):
        """Two people close together whose relative velocity reverses each 2 frames."""
        frames = []
        for i in range(steps):
            # Person A oscillates left-right; B oscillates right-left. Rel-velocity flips.
            offset = amplitude if (i // 2) % 2 == 0 else -amplitude
            a = _person(track_id=1, global_id=101, cx=base_x - offset, cy=base_y)
            b = _person(track_id=2, global_id=102, cx=base_x + offset, cy=base_y)
            frames.append([a, b])
        return frames

    def _co_directional_pair(self, steps, step_px=25, base_x=200, base_y=400):
        """Two people close, moving in the same direction at the same speed. No rel-motion,
        no oscillation — must never fire."""
        frames = []
        for i in range(steps):
            a = _person(track_id=1, global_id=101, cx=base_x + i * step_px, cy=base_y)
            b = _person(track_id=2, global_id=102, cx=base_x + 50 + i * step_px, cy=base_y)
            frames.append([a, b])
        return frames

    def _straight_line_fast_pair(self, steps, base_x=200, base_y=400):
        """Close proximity, both moving fast, high rel-motion (against each other) but
        NEVER reversing direction — a straight-line jostle. Should fail oscillation gate."""
        frames = []
        for i in range(steps):
            a = _person(track_id=1, global_id=101, cx=base_x + i * 20, cy=base_y)
            b = _person(track_id=2, global_id=102, cx=base_x + 100 - i * 20, cy=base_y)
            frames.append([a, b])
        return frames

    def test_co_directional_pair_does_not_fire(self):
        frames = self._co_directional_pair(steps=20)
        events = self._drive(frames)
        self.assertEqual(events, [])
        self.mock_alert.assert_not_called()

    def test_straight_line_high_rel_motion_needs_oscillation_to_fire(self):
        # Two people converging then passing — no direction reversal.
        frames = self._straight_line_fast_pair(steps=20)
        events = self._drive(frames)
        # With min_flips=2, this must NOT fire (no oscillation).
        self.assertEqual(events, [])

    def test_oscillating_jostle_fires_with_confidence_class(self):
        frames = self._oscillating_pair(steps=30)
        events = self._drive(frames)
        self.assertGreaterEqual(len(events), 1, "sustained oscillating jostle must fire")
        first = events[0]
        self.assertIn(first["confidence_class"], ("low", "medium", "high"))
        self.assertGreaterEqual(first["oscillation_flips"], 2)
        self.assertGreater(first["duration_s"], 0.3 - 0.001)

    def test_cooldown_deduplicates_sustained_alerts(self):
        os.environ["ANOMALY_COOLDOWN_S"] = "5.0"
        inference_config.reload()
        frames = self._oscillating_pair(steps=40)
        events = self._drive(frames)
        self.assertEqual(len(events), 1, "one sustained fight logs one alert within cooldown")

    def test_crowd_context_is_counted(self):
        frames = self._oscillating_pair(steps=20)
        # Add two bystanders inside the crowd radius around the pair.
        for i, people in enumerate(frames):
            people.append(_person(track_id=3, global_id=103, cx=380, cy=410))
            people.append(_person(track_id=4, global_id=104, cx=420, cy=410))
        events = self._drive(frames)
        self.assertGreaterEqual(len(events), 1)
        self.assertGreaterEqual(events[0]["crowd_context_count"], 1)

    def test_duration_threshold_blocks_brief_bursts(self):
        os.environ["ANOMALY_MIN_DURATION_S"] = "2.0"
        inference_config.reload()
        # Only 6 frames at dt=0.1 = 0.6s of oscillation — under 2.0s duration.
        frames = self._oscillating_pair(steps=6)
        events = self._drive(frames)
        self.assertEqual(events, [], "brief bursts under min_duration must not fire")

    def test_master_switch_disables(self):
        os.environ["ANOMALY_DETECTION"] = "0"
        inference_config.reload()
        frames = self._oscillating_pair(steps=30)
        events = self._drive(frames)
        self.assertEqual(events, [])


class OscillationCountTests(unittest.TestCase):
    """Unit test for the sign-flip counter in isolation."""

    def test_no_flips_when_monotonic(self):
        hist = [(1.0, 0.0), (2.0, 0.0), (3.0, 0.0), (4.0, 0.0)]
        self.assertEqual(anomaly_detector._count_sign_flips(hist), 0)

    def test_flips_counted_across_reversals(self):
        hist = [(1.0, 0.0), (-1.0, 0.0), (1.0, 0.0), (-1.0, 0.0)]
        self.assertEqual(anomaly_detector._count_sign_flips(hist), 3)

    def test_zero_axis_is_skipped(self):
        # A zero velocity between reversals doesn't break the flip count.
        hist = [(1.0, 0.0), (0.0, 0.0), (-1.0, 0.0)]
        self.assertEqual(anomaly_detector._count_sign_flips(hist), 1)


if __name__ == "__main__":
    unittest.main()
