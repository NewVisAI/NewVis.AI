import os
import unittest

import inference_config
from fall_detector import (
    check_fall,
    is_currently_fallen,
    reset_fall_state,
    stillness_status,
)


def _standing_bbox(x_offset=0, y=100):
    # w=50, h=150 -> aspect ratio 0.33 (upright)
    return (100 + x_offset, y, 150 + x_offset, y + 150)


def _fallen_bbox(y=300, x_offset=0):
    # w=150, h=50 -> aspect ratio 3.0 (horizontal), centroid far below standing
    return (80 + x_offset, y, 230 + x_offset, y + 50)


def _sitting_bbox(x_offset=0, y=200):
    # w=80, h=100 -> aspect ratio 0.8 (borderline; a sitting person is not "clearly upright")
    return (100 + x_offset, y, 180 + x_offset, y + 100)


class FallDetectorTests(unittest.TestCase):
    def setUp(self):
        reset_fall_state()
        # Force POSE_VERIFY off for the bbox-only tests so pose_verify.verify_fall
        # is never invoked (there's no real model available in this environment).
        os.environ["POSE_VERIFY"] = "0"
        inference_config.reload()

    def tearDown(self):
        os.environ.pop("POSE_VERIFY", None)
        inference_config.reload()
        reset_fall_state()

    def test_normal_standing_and_walking_does_not_trigger(self):
        track_key = (1, 101)
        video_time = 0.0

        for i in range(10):
            details = check_fall(
                track_key=track_key,
                global_id=1,
                bbox=_standing_bbox(x_offset=i * 5),
                video_time=video_time,
            )
            self.assertIsNone(details)
            self.assertFalse(is_currently_fallen(global_id=1, video_time=video_time))
            video_time += 0.04

    def test_rapid_fall_triggers_fall_detected(self):
        track_key = (1, 102)
        video_time = 0.0

        # Standing phase
        for _ in range(4):
            self.assertIsNone(check_fall(track_key, 2, _standing_bbox(), video_time))
            video_time += 0.04

        # Sudden fall: box flips horizontal and centroid drops well below
        details = None
        for _ in range(6):
            result = check_fall(track_key, 2, _fallen_bbox(), video_time)
            if result:
                details = result
            video_time += 0.04

        self.assertIsNotNone(details, "Fall was not detected after standing→fallen transition")
        self.assertGreater(details["vertical_drop_ratio"], 0.5)
        self.assertIn(details["confidence_class"], ("medium", "high"))
        self.assertTrue(is_currently_fallen(global_id=2, video_time=video_time))

    def test_cooldown_suppresses_immediate_retrigger(self):
        track_key = (1, 103)
        video_time = 0.0

        for _ in range(4):
            check_fall(track_key, 3, _standing_bbox(), video_time)
            video_time += 0.04

        detections = 0
        for _ in range(20):
            if check_fall(track_key, 3, _fallen_bbox(), video_time):
                detections += 1
            video_time += 0.04

        self.assertEqual(detections, 1, "Sustained lying bboxes should alert only once within cooldown")

    def test_already_lying_person_does_not_trigger(self):
        # No standing→fallen transition: person enters the frame horizontal.
        track_key = (1, 104)
        video_time = 0.0

        for _ in range(10):
            self.assertIsNone(check_fall(track_key, 4, _fallen_bbox(), video_time))
            video_time += 0.04

    def test_sitting_person_bbox_flicker_does_not_trigger(self):
        # A sitting person whose bbox momentarily narrows (borderline aspect ratio)
        # must NOT satisfy the was-standing gate. The tightened check requires a
        # MAJORITY of the pre-transition samples to be clearly upright, not just one.
        track_key = (1, 106)
        video_time = 0.0

        # Feed mostly-sitting frames with a single narrow frame in the middle.
        boxes = [
            _sitting_bbox(),
            _sitting_bbox(x_offset=2),
            _standing_bbox(),           # one lone upright sample
            _sitting_bbox(x_offset=-1),
        ]
        for b in boxes:
            self.assertIsNone(check_fall(track_key, 6, b, video_time))
            video_time += 0.04

        # Now flip horizontal. Old logic (earliest-only) could have fired; new
        # majority-gate must not.
        details = None
        for _ in range(6):
            r = check_fall(track_key, 6, _fallen_bbox(), video_time)
            if r:
                details = r
            video_time += 0.04

        self.assertIsNone(details, "Sitting → fallen without a clearly-standing majority must not fire")

    def test_reset_clears_history(self):
        track_key = (1, 105)
        check_fall(track_key, 5, _standing_bbox(), 0.0)
        reset_fall_state()
        self.assertFalse(is_currently_fallen(5, 0.0))


class StillnessConfirmationTests(unittest.TestCase):
    """Post-fall stillness augments the alert: still = confirmed high, moved = low."""

    def setUp(self):
        reset_fall_state()
        os.environ["POSE_VERIFY"] = "0"
        inference_config.reload()

    def tearDown(self):
        os.environ.pop("POSE_VERIFY", None)
        inference_config.reload()
        reset_fall_state()

    def _drive_fall(self, gid=301):
        track_key = (1, gid)
        t = 0.0
        for _ in range(4):
            check_fall(track_key, gid, _standing_bbox(), t)
            t += 0.04
        for _ in range(6):
            check_fall(track_key, gid, _fallen_bbox(), t)
            t += 0.04
        return track_key, gid, t

    def test_stillness_pending_returns_none_before_window(self):
        _, gid, t = self._drive_fall(301)
        # Immediately after: window hasn't elapsed, so result is None.
        self.assertIsNone(stillness_status(gid, t + 0.5))

    def test_stillness_confirmed_when_person_stays_down(self):
        track_key, gid, t = self._drive_fall(302)
        # Continue feeding roughly the same fallen bbox for 4 seconds > window (3s default).
        for _ in range(30):
            check_fall(track_key, gid, _fallen_bbox(), t)
            t += 0.15
        status = stillness_status(gid, t)
        self.assertIsNotNone(status)
        self.assertTrue(status["stillness_confirmed"])
        self.assertEqual(status["confidence_class"], "high")

    def test_stillness_refuted_when_person_gets_up(self):
        track_key, gid, t = self._drive_fall(303)
        # Person walks away quickly — centroid moves several body-heights.
        for step in range(30):
            # y jumps back up by 250 pixels (way more than a body-height allowance)
            bbox = _standing_bbox(x_offset=step * 20, y=80)
            check_fall(track_key, gid, bbox, t)
            t += 0.15
        status = stillness_status(gid, t)
        self.assertIsNotNone(status)
        self.assertFalse(status["stillness_confirmed"])
        self.assertEqual(status["confidence_class"], "low")


if __name__ == "__main__":
    unittest.main()
