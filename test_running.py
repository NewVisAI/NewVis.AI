"""
Unit tests for running detection.

Cover the accuracy-shaping additions on top of the raw speed threshold:
  * Sustained-speed requirement: a lone jittery spike does NOT count.
  * Direction coherence: same-heading motion is running; oscillation is jitter.
  * Edge-of-frame suppression: bbox distortion at the boundary must not fire.
  * Cooldown: sustained running only logs one event, not one per frame.
  * Confidence class: the fired event carries a low/medium/high label.

Every test forces the running detector's config knobs into deterministic values
so a deployment.json on the host machine cannot silently break the fixtures.
"""

import os
import unittest

import inference_config
from running import (
    check_running,
    is_currently_running,
    reset_running_state,
    _confidence_class,
)


DEFAULT_HEIGHT = 150


def _bbox(cx, cy, h=DEFAULT_HEIGHT, w=50):
    x1 = int(cx - w / 2)
    y1 = int(cy - h / 2)
    return (x1, y1, x1 + w, y1 + h)


def _drive_track(gid, positions, dt=0.04, frame_shape=(720, 1280, 3)):
    """Feed a sequence of (cx, cy) samples to check_running. Returns count of
    newly_detected events fired across the sequence."""
    fired = 0
    t = 0.0
    for cx, cy in positions:
        detected = check_running(
            track_key=(1, gid),
            global_id=gid,
            camera_id=1,
            bbox=_bbox(cx, cy),
            video_time=t,
            frame_shape=frame_shape,
        )
        if detected:
            fired += 1
        t += dt
    return fired


class RunningDetectorTests(unittest.TestCase):
    def setUp(self):
        reset_running_state()
        for k in (
            "RUNNING_SPEED_THRESHOLD",
            "RUNNING_MIN_SAMPLES",
            "RUNNING_SUSTAINED_SAMPLES",
            "RUNNING_DIRECTION_CHECK",
            "RUNNING_EDGE_MARGIN_PCT",
        ):
            os.environ.pop(k, None)
        inference_config.reload()

    tearDown = setUp

    def test_walking_slowly_does_not_trigger(self):
        # ~2 px/frame -> ~0.33 body-heights/sec, well below default 2.5.
        positions = [(400, 400 + i * 2) for i in range(30)]
        fired = _drive_track(gid=701, positions=positions)
        self.assertEqual(fired, 0)
        self.assertFalse(is_currently_running(701, video_time=30 * 0.04))

    def test_sustained_running_fires_once(self):
        # ~20 px/frame vertically -> ~3.3 body-heights/sec once EMA warms up.
        positions = [(400, 400 + i * 20) for i in range(30)]
        fired = _drive_track(gid=702, positions=positions)
        self.assertEqual(fired, 1, "sustained sprint should log exactly one event")
        self.assertTrue(is_currently_running(702, video_time=30 * 0.04))

    def test_single_jittery_spike_does_not_fire(self):
        # Warm up walking, then ONE big jump, then walking again.
        walk = [(400, 400 + i * 2) for i in range(10)]
        spike = [(400, 600)]  # 180 pixel jump in one frame
        after = [(400, 602 + i * 2) for i in range(10)]
        positions = walk + spike + after
        fired = _drive_track(gid=703, positions=positions)
        self.assertEqual(fired, 0, "one jittery bbox spike must not count as running")

    def test_oscillating_direction_does_not_fire(self):
        # Fast motion but the direction flips every frame (bbox jitter, not running).
        positions = [(400, 400 + (25 if i % 2 == 0 else -25) * (i + 1)) for i in range(30)]
        os.environ["RUNNING_DIRECTION_CHECK"] = "1"
        inference_config.reload()
        fired = _drive_track(gid=704, positions=positions)
        self.assertEqual(fired, 0, "direction oscillation must fail the coherence check")

    def test_edge_of_frame_is_suppressed(self):
        # Fast motion but the bbox is right at the frame edge.
        # Frame shape 720x1280 -> 5% margin = 64px in y, 36px above frame boundary.
        # Place bbox centre at y=30 (well within top margin) and move fast horizontally.
        positions = [(400 + i * 20, 30) for i in range(30)]
        fired = _drive_track(gid=705, positions=positions, frame_shape=(720, 1280, 3))
        self.assertEqual(fired, 0, "edge-of-frame running must be suppressed")

        # Same motion in the safe centre must fire.
        reset_running_state()
        positions = [(400 + i * 20, 400) for i in range(30)]
        fired = _drive_track(gid=706, positions=positions, frame_shape=(720, 1280, 3))
        self.assertEqual(fired, 1)

    def test_reset_clears_state(self):
        positions = [(400, 400 + i * 20) for i in range(30)]
        _drive_track(gid=707, positions=positions)
        self.assertTrue(is_currently_running(707, video_time=30 * 0.04))
        reset_running_state()
        self.assertFalse(is_currently_running(707, video_time=30 * 0.04))

    def test_env_overrides_lower_threshold(self):
        # Force threshold way down so walking will trigger. Proves the config path works.
        os.environ["RUNNING_SPEED_THRESHOLD"] = "0.1"
        os.environ["RUNNING_MIN_SAMPLES"] = "3"
        os.environ["RUNNING_SUSTAINED_SAMPLES"] = "2"
        inference_config.reload()
        positions = [(400, 400 + i * 2) for i in range(15)]
        fired = _drive_track(gid=708, positions=positions)
        self.assertGreaterEqual(fired, 1)


class ConfidenceClassTests(unittest.TestCase):
    """Unit tests for the local confidence bucketing helper."""

    def test_high_confidence_when_speed_well_above_threshold(self):
        self.assertEqual(
            _confidence_class(speed=5.0, threshold=2.5, direction_coherent=True),
            "high",
        )

    def test_direction_incoherence_caps_confidence(self):
        # Even a huge speed can't reach "high" if direction is jittery.
        self.assertNotEqual(
            _confidence_class(speed=10.0, threshold=2.5, direction_coherent=False),
            "high",
        )

    def test_barely_over_threshold_is_low(self):
        self.assertEqual(
            _confidence_class(speed=2.55, threshold=2.5, direction_coherent=True),
            "low",
        )


if __name__ == "__main__":
    unittest.main()
