"""Camera credentials must never appear in API output (see url_redaction.py)."""
import unittest

from url_redaction import redact_url_credentials, redact_url_credentials_bytes


class TestUrlRedaction(unittest.TestCase):
    def test_plain_rtsp_credentials_removed(self):
        self.assertEqual(redact_url_credentials("rtsp://admin:S3cret@10.0.0.5:554/h264/ch1"),
                         "rtsp://10.0.0.5:554/h264/ch1")

    def test_empty_password_and_user_only(self):
        self.assertEqual(redact_url_credentials("rtsp://admin:@192.168.1.20:554/x"), "rtsp://192.168.1.20:554/x")
        self.assertEqual(redact_url_credentials("http://user@cam.local/mjpeg"), "http://cam.local/mjpeg")

    def test_percent_encoded_inside_clip_link(self):
        link = "/api/clip/video?path=rtsp%3A//admin%3AS3cret%4010.0.0.5%3A554/h264"
        out = redact_url_credentials(link)
        self.assertNotIn("S3cret", out)
        self.assertEqual(out, "/api/clip/video?path=rtsp%3A//10.0.0.5%3A554/h264")
        self.assertNotIn("S3cret", redact_url_credentials("rtsp%3A%2F%2Fadmin%3AS3cret%40h/p"))

    def test_inside_json_body(self):
        body = b'{"video_path":"rtsp://admin:S3cret@10.0.0.5/a","n":1}'
        self.assertEqual(redact_url_credentials_bytes(body), b'{"video_path":"rtsp://10.0.0.5/a","n":1}')

    def test_leaves_other_text_alone(self):
        for s in ["rtsp://10.0.0.5/x?u=a%40b", "https://gensolve.in/a@b", r"D:\videos\a.mp4",
                  "/mnt/d/Downloads/view-IP5 (1).mp4", "person entered zone @ 10:12", ""]:
            self.assertEqual(redact_url_credentials(s), s)
        self.assertIsNone(redact_url_credentials(None))


if __name__ == "__main__":
    unittest.main()
