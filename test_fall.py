import unittest

from fall_detector import check_fall, is_currently_fallen, reset_fall_state


def _standing_bbox(x_offset=0, y=100):
    # w=50, h=150 -> aspect ratio 0.33 (upright)
    return (100 + x_offset, y, 150 + x_offset, y + 150)


def _fallen_bbox(y=300):
    # w=150, h=50 -> aspect ratio 3.0 (horizontal), centroid far below standing
    return (80, y, 230, y + 50)


class FallDetectorTests(unittest.TestCase):
    def setUp(self):
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

    def test_reset_clears_history(self):
        track_key = (1, 105)
        check_fall(track_key, 5, _standing_bbox(), 0.0)
        reset_fall_state()
        self.assertFalse(is_currently_fallen(5, 0.0))


if __name__ == "__main__":
    unittest.main()
