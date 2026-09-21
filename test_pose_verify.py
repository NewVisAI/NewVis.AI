"""
Tests for event-gated pose verification of falls (POSE_VERIFY).

Covers three things without needing the real pose model (it's stubbed):
  * the pure keypoint geometry (upright vs lying torso),
  * config flags (off by default + env overrides),
  * the SAFETY contract: when POSE_VERIFY is on, pose AUGMENTS a fall's details but
    NEVER suppresses the fall — even if pose says "not fallen" or the model errors.

Run: ./.venv/bin/python -m unittest test_pose_verify
"""

import os
import unittest

import numpy as np

import inference_config
import pose_verify
import fall_detector


def _kpts(pairs):
    """Build a 17x3 COCO keypoint list; `pairs` maps index -> (x, y, conf)."""
    k = [[0.0, 0.0, 0.0] for _ in range(17)]
    for i, (x, y, c) in pairs.items():
        k[i] = [float(x), float(y), float(c)]
    return k


def _standing_kpts():
    return _kpts({
        pose_verify.L_SHOULDER: (90, 100, 0.9), pose_verify.R_SHOULDER: (110, 100, 0.9),
        pose_verify.L_HIP: (90, 300, 0.9), pose_verify.R_HIP: (110, 300, 0.9),
    })


def _lying_kpts():
    return _kpts({
        pose_verify.L_SHOULDER: (100, 190, 0.9), pose_verify.R_SHOULDER: (100, 210, 0.9),
        pose_verify.L_HIP: (300, 190, 0.9), pose_verify.R_HIP: (300, 210, 0.9),
    })


class PoseGeometry(unittest.TestCase):
    def test_upright_torso_low_confidence(self):
        conf, _ = pose_verify.fallen_confidence_from_keypoints(_standing_kpts())
        self.assertLess(conf, 0.1)

    def test_lying_torso_high_confidence(self):
        conf, _ = pose_verify.fallen_confidence_from_keypoints(_lying_kpts())
        self.assertGreater(conf, 0.9)

    def test_insufficient_keypoints(self):
        low = _kpts({pose_verify.L_SHOULDER: (90, 100, 0.1), pose_verify.L_HIP: (90, 300, 0.1)})
        conf, reason = pose_verify.fallen_confidence_from_keypoints(low)
        self.assertEqual(conf, 0.0)
        self.assertIn("insufficient", reason)


class PoseConfig(unittest.TestCase):
    def setUp(self):
        for k in ("POSE_VERIFY", "POSE_VERIFY_THRESH", "POSE_VERIFY_WEIGHTS"):
            os.environ.pop(k, None)
        inference_config.reload()

    tearDown = setUp

    def test_on_by_default(self):
        # Pose is augment-only (never suppresses), so the default is ON — the
        # recall gain on falls toward/away from camera is worth the ~zero cost
        # (pose only runs on the crop of an already-triggered fall).
        self.assertTrue(inference_config.pose_verify())

    def test_defaults(self):
        self.assertEqual(inference_config.pose_verify_thresh(), 0.5)
        self.assertEqual(inference_config.pose_verify_weights(), "yolov8n-pose.pt")

    def test_env_overrides(self):
        os.environ["POSE_VERIFY"] = "0"
        os.environ["POSE_VERIFY_THRESH"] = "0.7"
        inference_config.reload()
        self.assertFalse(inference_config.pose_verify())
        self.assertEqual(inference_config.pose_verify_thresh(), 0.7)


class RaisedArmsGeometry(unittest.TestCase):
    """Pure geometry for the optional altercation pose augment."""

    def test_arm_raised_when_wrist_above_shoulder(self):
        k = _kpts({
            pose_verify.L_SHOULDER: (100, 200, 0.9),
            pose_verify.L_WRIST: (100, 100, 0.9),  # y smaller = higher in image
        })
        raised, reason = pose_verify.raised_arms_from_keypoints(k)
        self.assertTrue(raised)
        self.assertIn("left_wrist_above_shoulder", reason)

    def test_arm_down_when_wrist_below_shoulder(self):
        k = _kpts({
            pose_verify.L_SHOULDER: (100, 200, 0.9),
            pose_verify.L_WRIST: (100, 400, 0.9),
            pose_verify.R_SHOULDER: (150, 200, 0.9),
            pose_verify.R_WRIST: (150, 400, 0.9),
        })
        raised, reason = pose_verify.raised_arms_from_keypoints(k)
        self.assertFalse(raised)
        self.assertIn("below", reason)

    def test_low_confidence_wrist_ignored(self):
        k = _kpts({
            pose_verify.L_SHOULDER: (100, 200, 0.9),
            pose_verify.L_WRIST: (100, 100, 0.1),  # below KP_CONF_MIN
        })
        raised, reason = pose_verify.raised_arms_from_keypoints(k)
        self.assertFalse(raised)
        self.assertIn("insufficient", reason)


def _standing_bbox():
    return (100, 100, 150, 300)   # w=50 h=200 ratio 0.25 (upright)


def _fallen_bbox():
    return (100, 400, 400, 470)   # w=300 h=70 ratio ~4.3, centroid dropped (fallen)


class CheckFallPoseIntegration(unittest.TestCase):
    def setUp(self):
        fall_detector.reset_fall_state()
        os.environ["POSE_VERIFY"] = "1"
        inference_config.reload()
        self.frame = np.zeros((480, 640, 3), dtype=np.uint8)
        self._orig = pose_verify.verify_fall

    def tearDown(self):
        pose_verify.verify_fall = self._orig
        os.environ.pop("POSE_VERIFY", None)
        inference_config.reload()
        fall_detector.reset_fall_state()

    def _drive_fall(self, gid):
        t = 0.0
        for _ in range(4):
            fall_detector.check_fall((1, gid), gid, _standing_bbox(), t, frame=self.frame)
            t += 0.04
        details = None
        for _ in range(6):
            r = fall_detector.check_fall((1, gid), gid, _fallen_bbox(), t, frame=self.frame)
            if r:
                details = r
            t += 0.04
        return details

    def test_pose_augments_details(self):
        pose_verify.verify_fall = lambda frame, bbox: {
            "pose_verified": True, "pose_confidence": 0.95, "pose_reason": "stub"}
        details = self._drive_fall(201)
        self.assertIsNotNone(details)
        self.assertTrue(details["pose_verified"])
        self.assertEqual(details["pose_confidence"], 0.95)

    def test_pose_disagreement_never_suppresses(self):
        # Pose says "not fallen" -> the fall alert MUST still fire (safety).
        pose_verify.verify_fall = lambda frame, bbox: {
            "pose_verified": False, "pose_confidence": 0.0, "pose_reason": "stub-reject"}
        details = self._drive_fall(202)
        self.assertIsNotNone(details, "fall must fire even when pose disagrees")
        self.assertFalse(details["pose_verified"])

    def test_pose_error_never_breaks_fall(self):
        def boom(frame, bbox):
            raise RuntimeError("pose model exploded")
        pose_verify.verify_fall = boom
        details = self._drive_fall(203)
        self.assertIsNotNone(details, "fall must fire even if pose raises")

    def test_pose_off_adds_no_fields(self):
        os.environ["POSE_VERIFY"] = "0"
        inference_config.reload()
        details = self._drive_fall(204)
        self.assertIsNotNone(details)
        self.assertNotIn("pose_verified", details)


if __name__ == "__main__":
    unittest.main()
