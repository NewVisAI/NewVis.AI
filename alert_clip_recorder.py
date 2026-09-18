"""
Post-event clip recorder for live-camera alerts.

Records the reader's already-decoded JPEG frames (from
backend_runner.latest_frames) to disk when an alert fires — no new RTSP
sessions, no extra HEVC decoders. Two clips per alert:
  - alert_clips/alert_<id>.mp4        short  (~8 s)
  - alert_clips/alert_<id>_full.mp4   full   (~2 min)

Codec pipeline:
1. Write raw frames to a temp .avi using MJPG (cv2 always handles this).
2. Transcode the .avi to a browser-safe H.264 .mp4 using ffmpeg
   (bundled by the imageio-ffmpeg wheel — no OpenH264 DLL required).
3. Delete the intermediate .avi and publish the .mp4 path to the alert row.

If imageio_ffmpeg is not installed we fall back to mp4v — playback will
fail in most browsers but the file is still on disk for diagnosis. Log a
warning so it's visible in the server terminal.
"""

import os
import subprocess
import threading
import time
from typing import Callable, Optional

import numpy as np
import cv2

try:
    import imageio_ffmpeg
    _FFMPEG_EXE: Optional[str] = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    _FFMPEG_EXE = None


CLIP_DIR = "alert_clips"
CLIP_DURATION_S = 8             # short "clip" — playable ~8 s after alert
FULL_DURATION_S = 120           # "full clip" — playable ~2 min after alert
TARGET_FPS = 15
POLL_INTERVAL_S = 1.0 / TARGET_FPS


def _ensure_dir():
    try:
        os.makedirs(CLIP_DIR, exist_ok=True)
    except OSError:
        pass


def _current_frame_jpeg(camera_id: int) -> Optional[bytes]:
    try:
        import backend_runner
        buf = backend_runner.latest_frames.get(camera_id)
        return buf if buf else None
    except Exception:
        return None


def _decode_bgr(jpeg_bytes: bytes) -> Optional[np.ndarray]:
    try:
        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception:
        return None


def _transcode_to_mp4(src_avi: str, dst_mp4: str) -> bool:
    """Re-encode src (MJPG .avi) as H.264 .mp4 using bundled ffmpeg. Returns
    True on success; False (with the source left intact) on failure."""
    if not _FFMPEG_EXE:
        return False
    try:
        # -y overwrite, -loglevel error to keep the server log clean,
        # -movflags +faststart so the browser can start playing immediately.
        cmd = [
            _FFMPEG_EXE, "-y", "-loglevel", "error",
            "-i", src_avi,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            dst_mp4,
        ]
        subprocess.run(cmd, check=True, timeout=90,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return os.path.exists(dst_mp4) and os.path.getsize(dst_mp4) > 0
    except Exception as e:
        print(f"[alert_clip_recorder] ffmpeg transcode failed: {e}")
        return False


def _record_and_encode(camera_id: int, out_mp4: str, duration_s: int) -> Optional[str]:
    """Poll the reader's JPEG buffer for duration_s seconds, write frames to
    a temp MJPG .avi, then transcode to H.264 mp4. Returns the final path."""
    _ensure_dir()

    temp_avi = out_mp4[:-4] + ".raw.avi"
    writer: Optional[cv2.VideoWriter] = None
    last_jpeg_id: Optional[int] = None
    frames_written = 0

    deadline = time.time() + duration_s
    try:
        while time.time() < deadline:
            jpeg = _current_frame_jpeg(camera_id)
            if jpeg is None:
                time.sleep(POLL_INTERVAL_S); continue
            jid = id(jpeg)
            if jid == last_jpeg_id:
                time.sleep(POLL_INTERVAL_S); continue
            last_jpeg_id = jid
            frame = _decode_bgr(jpeg)
            if frame is None:
                time.sleep(POLL_INTERVAL_S); continue

            if writer is None:
                h, w = frame.shape[:2]
                fourcc = cv2.VideoWriter_fourcc(*"MJPG")
                writer = cv2.VideoWriter(temp_avi, fourcc, TARGET_FPS, (w, h))
                if not writer.isOpened():
                    return None

            writer.write(frame)
            frames_written += 1
            time.sleep(POLL_INTERVAL_S)
    finally:
        if writer is not None:
            writer.release()

    if frames_written == 0 or not os.path.exists(temp_avi):
        return None

    # Transcode to browser-playable H.264 MP4.
    ok = _transcode_to_mp4(temp_avi, out_mp4)
    if ok:
        try:
            os.remove(temp_avi)
        except OSError:
            pass
        return out_mp4

    # No ffmpeg → the browser won't play the raw .avi and the clip modal
    # will hang exactly like before. Refuse the fallback: delete the temp
    # .avi and return None so the alert stays snapshot-only (which the UI
    # renders correctly). Log a loud, actionable warning.
    print("[alert_clip_recorder] WARNING: imageio-ffmpeg not installed — "
          "cannot produce browser-playable H.264 mp4. Alert will remain "
          "snapshot-only. Fix: .\\.venv-win\\Scripts\\python.exe -m pip "
          "install imageio-ffmpeg  (then restart the server).")
    try:
        os.remove(temp_avi)
    except OSError:
        pass
    return None


def start_recording(
    alert_id: int,
    camera_id: int,
    on_done: Optional[Callable[[str], None]] = None,
) -> None:
    """Fire-and-forget: two daemon threads write the short and full clips."""
    short_mp4 = os.path.join(CLIP_DIR, f"alert_{alert_id}.mp4")
    full_mp4 = os.path.join(CLIP_DIR, f"alert_{alert_id}_full.mp4")

    def _short():
        try:
            path = _record_and_encode(camera_id, short_mp4, CLIP_DURATION_S)
            if path and on_done is not None:
                on_done(path)
        except Exception as e:
            print(f"[alert_clip_recorder] short-clip {alert_id} failed: {e}")

    def _full():
        try:
            _record_and_encode(camera_id, full_mp4, FULL_DURATION_S)
        except Exception as e:
            print(f"[alert_clip_recorder] full-clip {alert_id} failed: {e}")

    threading.Thread(target=_short, name=f"alert-clip-{alert_id}",
                     daemon=True).start()
    threading.Thread(target=_full, name=f"alert-clip-{alert_id}-full",
                     daemon=True).start()
