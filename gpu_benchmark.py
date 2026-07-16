"""
GPU / CPU throughput benchmark for the Sentinel pipeline.

Measures how fast one machine runs the full analytics pipeline, and estimates how
many cameras it could handle. Run it twice and compare the two modes:

    # full OSNet Re-ID live (heavier):
    REID_DEFERRED=0 python gpu_benchmark.py <video-or-rtsp> 200
    # deferred Re-ID (OSNet skipped live):
    REID_DEFERRED=1 python gpu_benchmark.py <video-or-rtsp> 200

Watch GPU load in another terminal:  nvidia-smi -l 1

The script auto-uses the GPU if a CUDA-enabled PyTorch is installed (see the
GPU setup steps in REPORT_for-friend_optimizations-and-GPU.md).
"""

import os
import sys
import time

import cv2
import numpy as np

try:
    import torch
    DEV = "cuda" if torch.cuda.is_available() else "cpu"
    GPU_NAME = torch.cuda.get_device_name(0) if DEV == "cuda" else "(no CUDA GPU)"
except Exception:
    torch = None
    DEV, GPU_NAME = "cpu", "(torch not importable)"

from app import (_create_camera_runtime, _process_camera_frame, HumanDetector,
                 GlobalIdentityManager, IncidentManager, resolve_capture_source,
                 reset_runtime_state, reset_running_state, reset_fall_state)
from backend_runner import FrameInjector

video = sys.argv[1] if len(sys.argv) > 1 else None
n = int(sys.argv[2]) if len(sys.argv) > 2 else 200
if not video:
    print("usage: python gpu_benchmark.py <video-or-rtsp> [frames]"); sys.exit(1)

print("=" * 60)
print(f"PyTorch device : {DEV}   {GPU_NAME}")
print(f"REID_DEFERRED  : {os.environ.get('REID_DEFERRED', '0')}")
print("=" * 60)

det = HumanDetector(model_type="yolo", weights="yolov8n.pt")
idm = GlobalIdentityManager()
inc = IncidentManager()
print(f"ReID backend   : {idm.embedder.backend_name}   deferred={idm.embedder.deferred}\n")

cfg = {"id": 99, "camera_id": 99, "name": "bench", "source": video}
rt = _create_camera_runtime(cfg)
if rt is None:
    print("cannot open source"); sys.exit(1)
rt.cap.release()
reset_runtime_state(); reset_running_state(); reset_fall_state()

cap = cv2.VideoCapture(resolve_capture_source(video))
times, people, got = [], [], 0
while got < n:
    ok, frame = cap.read()
    if not ok:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)   # loop a short clip
        continue
    if frame.shape[1] > 1280:
        s = 1280 / frame.shape[1]
        frame = cv2.resize(frame, (1280, int(frame.shape[0] * s)))
    rt.cap = FrameInjector(frame); rt.finished = False
    t0 = time.perf_counter()
    _process_camera_frame(rt, det, idm, inc, "multi")
    if DEV == "cuda":
        torch.cuda.synchronize()             # count real GPU time
    dt = time.perf_counter() - t0
    if got > 3:                              # skip warmup frames
        times.append(dt); people.append(getattr(rt, "last_person_count", 0))
    got += 1
cap.release()

ms = 1000 * np.mean(times)
fps = 1.0 / np.mean(times)
print(f"  avg {ms:6.1f} ms/frame   ->   {fps:5.1f} fps sustained   (avg {np.mean(people):.1f} people/frame)")
print(f"  at 3 fps analytics per camera, ~{fps/3:.0f} cameras per machine")
print()
print("  Run again with the other REID_DEFERRED value and compare the fps /")
print("  cameras-per-machine, while watching `nvidia-smi -l 1`.")
