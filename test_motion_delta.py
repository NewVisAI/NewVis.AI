"""
Tests for the motion-delta temporal-redundancy skip (compute-reduction lever).

Verifies the cheap change-ratio signal that decides whether an IDLE camera's frame is
'static' (detector pass skippable) behaves correctly:
  * sensor-noise-level jitter stays below the skip threshold (would be skipped),
  * a person-sized change goes well above it (never skipped),
and that the feature is OFF by default with env overrides honoured.

Run: ./.venv/bin/python -m unittest test_motion_delta
"""

import os
import unittest

import numpy as np

import inference_config
import live_analytics as la

FRAME_H, FRAME_W = 1080, 1920
DEFAULT_THRESH = 0.002  # inference_config.motion_delta_thresh() default


def _blank():
    return np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8)


class MotionDeltaSignal(unittest.TestCase):
    def test_identical_frames_are_static(self):
        a = _blank()
        small = la._delta_downscale(a)
        self.assertEqual(la._frame_change_ratio(small, small), 0.0)

    def test_sensor_noise_stays_below_threshold(self):
        # Low-amplitude noise (< per-pixel tolerance) must read as ~static, so a healthy
        # but empty camera keeps getting skipped rather than burning the detector.
        rng = np.random.default_rng(0)
        ref = _blank()
        noisy = np.clip(ref.astype(int) + rng.integers(-10, 11, ref.shape), 0, 255).astype(np.uint8)
        ratio = la._frame_change_ratio(la._delta_downscale(noisy), la._delta_downscale(ref))
        self.assertLess(ratio, DEFAULT_THRESH)

    def test_person_sized_change_is_not_static(self):
        # A person-sized bright region must exceed the threshold so it is NEVER skipped.
        ref = _blank()
        person = _blank()
        person[600:900, 900:1000] = 220  # ~300x100 px, person-ish
        ratio = la._frame_change_ratio(la._delta_downscale(person), la._delta_downscale(ref))
        self.assertGreater(ratio, DEFAULT_THRESH)

    def test_change_ratio_bounds(self):
        ref = _blank()
        allwhite = np.full_like(ref, 255)
        self.assertAlmostEqual(
            la._frame_change_ratio(la._delta_downscale(allwhite), la._delta_downscale(ref)),
            1.0, places=5,
        )


class MotionDeltaConfig(unittest.TestCase):
    def setUp(self):
        for k in ("MOTION_DELTA", "MOTION_DELTA_THRESH", "MOTION_DELTA_FULLSCAN_S"):
            os.environ.pop(k, None)
        inference_config.reload()

    def tearDown(self):
        for k in ("MOTION_DELTA", "MOTION_DELTA_THRESH", "MOTION_DELTA_FULLSCAN_S"):
            os.environ.pop(k, None)
        inference_config.reload()

    def test_off_by_default(self):
        self.assertFalse(inference_config.motion_delta())

    def test_env_overrides(self):
        os.environ["MOTION_DELTA"] = "1"
        os.environ["MOTION_DELTA_THRESH"] = "0.05"
        os.environ["MOTION_DELTA_FULLSCAN_S"] = "6"
        self.assertTrue(inference_config.motion_delta())
        self.assertEqual(inference_config.motion_delta_thresh(), 0.05)
        self.assertEqual(inference_config.motion_delta_fullscan_s(), 6.0)


class MotionDeltaLoopSimulation(unittest.TestCase):
    """Reproduce the idle-gate skip/heartbeat orchestration from live_analytics'
    _analytics_loop over a synthetic timeline, to lock its two guarantees:
      * a fully static scene IS skipped (compute saved), and
      * the full-scan heartbeat forces a real detector pass at least every fullscan_s
        (so a low-motion newcomer is caught within a bounded delay — the safety property)."""

    def _run(self, frames_with_time, thresh, fullscan_s):
        delta_ref = None
        last_fullscan = 0.0
        processed, skipped = [], []
        for now, frame in frames_with_time:
            small = la._delta_downscale(frame)
            if delta_ref is not None and (now - last_fullscan) < fullscan_s:
                if la._frame_change_ratio(small, delta_ref) < thresh:
                    skipped.append(now)
                    delta_ref = small
                    continue
            delta_ref = small
            last_fullscan = now
            processed.append(now)
        return processed, skipped

    def test_static_scene_skipped_but_heartbeat_holds(self):
        # 1 fps idle rate, 20 s of a static empty scene, 4 s heartbeat.
        static = _blank()
        timeline = [(float(t), static) for t in range(0, 20)]
        processed, skipped = self._run(timeline, DEFAULT_THRESH, fullscan_s=4.0)
        self.assertTrue(skipped, "a static scene should skip detector passes")
        # No two consecutive full scans are more than fullscan_s apart.
        gaps = [b - a for a, b in zip(processed, processed[1:])]
        self.assertTrue(all(g <= 4.0 + 1e-9 for g in gaps), f"heartbeat gap exceeded: {gaps}")

    def test_person_appearing_is_processed_not_skipped(self):
        static = _blank()
        person = _blank()
        person[600:900, 900:1000] = 220
        timeline = [(0.0, static), (1.0, static), (2.0, person)]
        processed, _ = self._run(timeline, DEFAULT_THRESH, fullscan_s=4.0)
        self.assertIn(2.0, processed, "a frame with a person must be processed, never skipped")


if __name__ == "__main__":
    unittest.main()
