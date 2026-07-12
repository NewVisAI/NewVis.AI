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

BOUNDARY = "frame"


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


def _annotate(frame, camera: Dict, width: int):
    h, w = frame.shape[:2]
    if w > width:
        scale = width / float(w)
        frame = cv2.resize(frame, (width, int(h * scale)))
    fh, fw = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (fw, 26), (20, 20, 20), cv2.FILLED)
    cv2.circle(frame, (12, 13), 5, (0, 0, 255), cv2.FILLED)  # red "LIVE" dot
    label = f"LIVE  {camera.get('name', 'Cam')}"
    cv2.putText(frame, label, (24, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 220, 255), 1)
    ts = datetime.now().strftime("%H:%M:%S")
    cv2.putText(frame, ts, (fw - 78, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)
    return frame


def grab_snapshot(camera: Dict, width: int = 480) -> Optional[bytes]:
    """One annotated JPEG frame (used for grid thumbnails and tests)."""
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
        frame = _annotate(frame, camera, width)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else None
    finally:
        cap.release()


def mjpeg_generator(camera: Dict, fps: int = 8, width: int = 480) -> Iterator[bytes]:
    """Infinite looping MJPEG byte stream for one camera."""
    cap, vfps, total = _open(camera)
    if cap is None:
        return
    frame_skip = max(1, int(round(vfps / max(1, fps))))
    delay = 1.0 / max(1, fps)
    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # loop the clip
                continue
            frame = _annotate(frame, camera, width)
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
