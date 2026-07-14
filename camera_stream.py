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

import time
from datetime import datetime
from typing import Dict, Iterator, Optional
import cv2
from detector import HumanDetector

BOUNDARY = "frame"
_detector = None

def _get_detector():
    global _detector
    if _detector is None:
        # It will automatically detect .pt, .onnx, or .tflite weights and select NPU/TPU/CPU backend
        _detector = HumanDetector(model_type="yolo", weights="yolov8n.pt")
    return _detector


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
        frame = backend_runner.latest_frames[camera_id].copy()
        frame = _annotate(frame, camera, width, run_yolo=False)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else None

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
            frame = backend_runner.latest_frames[camera_id].copy()
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
            
        # Safe fallback: read directly from the NVR source if background engine has not booted yet
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
