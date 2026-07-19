"""
Looping MJPEG streamer for the live multi-camera grid.

Each camera's video source is decoded, downscaled and re-encoded as JPEG,
yielded as a multipart/x-mixed-replace MJPEG stream that a plain <img> tag can
render — so the browser grid needs no plugins or WebRTC. When the file ends it
loops, which is how a finite downloaded clip stands in for a "live" feed. A
per-camera start offset lets several tiles sharing one source look distinct.

Streaming is intentionally lightweight (no inference): decode -> resize ->
overlay a banner -> JPEG. Heavy annotation belongs to the processing pipeline,
not the live wall.
"""

import os
import time
from datetime import datetime
from typing import Dict, Iterator, Optional
import cv2
import numpy as np
import inference_config
from detector import HumanDetector

# Harden OpenCV's FFMPEG backend for RTSP BEFORE any VideoCapture is created:
# force TCP transport and a finite read timeout so a stalled camera can never
# block a decode thread forever. Also selects the hardware decoder (NVDEC/QuickSync)
# when DECODE_BACKEND is set (lever ②); defaults to software decode.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    inference_config.ffmpeg_capture_options(),
)

BOUNDARY = "frame"
_detector = None


def _is_live_source(source) -> bool:
    """RTSP/HTTP camera vs. a local video file."""
    return str(source).lower().startswith(("rtsp://", "http://", "https://"))


def _get_detector():
    global _detector
    if _detector is None:
        # It will automatically detect .pt, .onnx, or .tflite weights and select NPU/TPU/CPU backend
        _detector = HumanDetector(model_type="yolo", weights="yolov8n.pt")
    return _detector


def _placeholder_jpeg(camera: Dict, width: int = 480, message: str = "Connecting to camera...") -> bytes:
    """A lightweight 'no signal yet' tile, so live cameras never force the web
    process to decode RTSP itself (which could block or crash the server)."""
    height = int(width * 9 / 16)
    frame = np.full((height, width, 3), 30, dtype=np.uint8)
    cv2.putText(frame, message, (16, height // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (170, 170, 170), 1)
    cv2.rectangle(frame, (0, 0), (width, 26), (20, 20, 20), cv2.FILLED)
    cv2.circle(frame, (12, 13), 5, (0, 140, 220), cv2.FILLED)  # amber = connecting
    cv2.putText(frame, f"LIVE  {camera.get('name', 'Cam')}", (24, 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 220, 255), 1)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return buf.tobytes()


def _open(camera: Dict):
    cap = cv2.VideoCapture(str(camera.get("source")))
    if not cap.isOpened():
        return None, 0.0, 0
    vfps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    offset = float(camera.get("start_offset_seconds", 0) or 0)
    if total > 0 and offset > 0:
        start_frame = min(total - 1, int(offset * vfps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    return cap, vfps, total


def _annotate(frame, camera: Dict, width: int, run_yolo: bool = True):
    h, w = frame.shape[:2]
    if w > width:
        scale = width / float(w)
        frame = cv2.resize(frame, (width, int(h * scale)))
    fh, fw = frame.shape[:2]
    
    # Run live detector fallback if not pre-annotated in the background threads
    if run_yolo:
        try:
            det = _get_detector()
            results = det.model(frame, verbose=False)[0]
            for box in results.boxes:
                cls = int(box.cls[0])
                if cls == 0: # Person class
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(frame, "PERSON", (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
        except Exception as e:
            print(f"[camera_stream] Frame AI detection failed: {e}")

    cv2.rectangle(frame, (0, 0), (fw, 26), (20, 20, 20), cv2.FILLED)
    cv2.circle(frame, (12, 13), 5, (0, 0, 255), cv2.FILLED)  # red "LIVE" dot
    label = f"LIVE  {camera.get('name', 'Cam')}"
    cv2.putText(frame, label, (24, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 220, 255), 1)
    ts = datetime.now().strftime("%H:%M:%S")
    cv2.putText(frame, ts, (fw - 78, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)
    return frame


def grab_snapshot(camera: Dict, width: int = 480) -> Optional[bytes]:
    """One annotated JPEG frame (used for grid thumbnails and tests)."""
    camera_id = int(camera.get("id"))
    import backend_runner
    if camera_id in backend_runner.latest_frames and backend_runner.latest_frames[camera_id] is not None:
        data = backend_runner.latest_frames[camera_id]
        if isinstance(data, bytes):
            return data
        frame = data.copy()
        frame = _annotate(frame, camera, width, run_yolo=False)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else None

    # Live cameras (RTSP) are NEVER decoded in the web process — that path can
    # block a worker thread or crash the server on a stalled/offline camera. The
    # live_analytics runner is the sole RTSP decoder and fills the frame cache
    # above; until it does, show a placeholder.
    if _is_live_source(camera.get("source")):
        return _placeholder_jpeg(camera, width)

    cap, _vfps, _total = _open(camera)
    if cap is None:
        return None
    try:
        frame = None
        for _ in range(5):
            ret, f = cap.read()
            if ret and f is not None:
                frame = f
                break
        if frame is None:
            return None
        frame = _annotate(frame, camera, width, run_yolo=True)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else None
    finally:
        cap.release()


def mjpeg_generator(camera: Dict, fps: int = 8, width: int = 480) -> Iterator[bytes]:
    """Infinite looping MJPEG byte stream for one camera."""
    camera_id = int(camera.get("id"))
    import backend_runner
    delay = 1.0 / max(1, fps)
    
    # If the background thread is active, stream from memory cache!
    # This prevents running extra YOLO model instances for multiple web stream requests.
    while True:
        if camera_id in backend_runner.latest_frames and backend_runner.latest_frames[camera_id] is not None:
            data = backend_runner.latest_frames[camera_id]
            if isinstance(data, bytes):
                # Yield pre-compressed JPEG data directly for 10x lower CPU usage
                yield (
                    b"--" + BOUNDARY.encode() + b"\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + data + b"\r\n"
                )
            else:
                frame = data.copy()
                # Draw standard UI annotations (LIVE dot, timestamp, name)
                frame = _annotate(frame, camera, width, run_yolo=False)
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                if ok:
                    yield (
                        b"--" + BOUNDARY.encode() + b"\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
                    )
            time.sleep(delay)
            continue

        # No cached frame yet. LIVE cameras are NEVER decoded here — the
        # live_analytics runner owns the RTSP stream and fills the cache above.
        # Until it does (or if the camera is offline), stream a placeholder so a
        # stalled/offline camera can never block a worker or crash the server.
        if _is_live_source(camera.get("source")):
            yield (
                b"--" + BOUNDARY.encode() + b"\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + _placeholder_jpeg(camera, width) + b"\r\n"
            )
            time.sleep(delay)
            continue

        # Safe fallback for local video FILES only: decode/loop in-process.
        cap, vfps, total = _open(camera)
        if cap is None:
            time.sleep(delay)
            continue
            
        frame_skip = max(1, int(round(vfps / max(1, fps))))
        try:
            while True:
                # If background worker has booted while running fallback, switch to it!
                if camera_id in backend_runner.latest_frames and backend_runner.latest_frames[camera_id] is not None:
                    break
                    
                ret, frame = cap.read()
                if not ret or frame is None:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # loop the clip
                    continue
                frame = _annotate(frame, camera, width, run_yolo=True)
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                if ok:
                    yield (
                        b"--" + BOUNDARY.encode() + b"\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
                    )
                for _ in range(frame_skip - 1):  # thin to target fps
                    cap.grab()
                time.sleep(delay)
        finally:
            cap.release()
