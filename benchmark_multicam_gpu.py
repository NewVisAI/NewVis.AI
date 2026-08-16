"""
Sentinel 2.0 — Multi-Camera GPU Batching & Performance Benchmark
Simulates 4, 8, 16, and 32 concurrent camera streams running through the
batched GPU inference pipeline on real hardware.
"""

import time
import torch
import cv2
import numpy as np
from detector import HumanDetector
from live_analytics import BatchInferenceManager

def run_multicam_benchmark(video_path: str, num_cameras_list=[4, 8, 16, 32], duration_seconds=5):
    print("=" * 65)
    print(" Sentinel 2.0 — Multi-Camera GPU Batching Benchmark ")
    print("=" * 65)
    
    if not torch.cuda.is_available():
        print("[!] CUDA not available. Run on GPU machine.")
        return

    device_name = torch.cuda.get_device_name(0)
    print(f"[*] GPU Device      : {device_name}")
    print(f"[*] PyTorch Version : {torch.__version__}")
    print(f"[*] Test Video      : {video_path}")
    print("-" * 65)

    detector = HumanDetector(model_type="yolo", weights="yolov8n.pt")
    
    import glob
    frame_paths = glob.glob("feature_test_report/frames/*.jpg") + glob.glob("feature_test_report/frames/*.png")
    sample_frames = []
    for path in frame_paths[:100]:
        img = cv2.imread(path)
        if img is not None:
            sample_frames.append(cv2.resize(img, (1280, 720)))

    if not sample_frames:
        print("[*] Synthesizing 50 test 720p frames...")
        for i in range(50):
            blank = np.zeros((720, 1280, 3), dtype=np.uint8)
            cv2.putText(blank, f"Simulated Frame {i}", (100, 100), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 0), 2)
            sample_frames.append(blank)

    if not sample_frames:
        print("[!] Failed to load sample frames from video.")
        return

    print(f"[*] Loaded {len(sample_frames)} sample 720p frames for simulation.")
    print("-" * 65)

    for num_cams in num_cameras_list:
        manager = BatchInferenceManager(timeout=0.015, max_batch=min(16, num_cams))
        stop_event = False
        frame_counts = [0] * num_cams

        def camera_thread(cam_idx):
            nonlocal stop_event
            frame_idx = 0
            while not stop_event:
                frame = sample_frames[(frame_idx + cam_idx) % len(sample_frames)]
                _ = manager.submit(detector, frame)
                frame_counts[cam_idx] += 1
                frame_idx += 1
                time.sleep(0.005)

        import threading
        threads = [threading.Thread(target=camera_thread, args=(i,), daemon=True) for i in range(num_cams)]
        
        # Warmup
        start_time = time.time()
        for t in threads:
            t.start()
        time.sleep(1.0)
        
        # Reset counters for clean timing
        for i in range(num_cams):
            frame_counts[i] = 0
        
        t0 = time.time()
        time.sleep(duration_seconds)
        stop_event = True
        
        for t in threads:
            t.join(timeout=1.0)

        elapsed = time.time() - t0
        total_frames = sum(frame_counts)
        fps = total_frames / elapsed
        latency_ms = (1.0 / fps) * 1000 if fps > 0 else 0
        vram_allocated_mb = torch.cuda.memory_allocated(0) / (1024 * 1024)
        vram_reserved_mb = torch.cuda.memory_reserved(0) / (1024 * 1024)

        print(f"Cameras: {num_cams:2d} | Total Processed: {total_frames:5d} frames | Throughput: {fps:6.1f} FPS | Latency: {latency_ms:5.2f} ms/frame | VRAM Allocated: {vram_allocated_mb:5.1f} MB (Reserved: {vram_reserved_mb:5.1f} MB)")

    print("=" * 65)
    print("[+] Benchmark complete!")

if __name__ == "__main__":
    run_multicam_benchmark("backend/static/uploads/view-HC4.mp4")
