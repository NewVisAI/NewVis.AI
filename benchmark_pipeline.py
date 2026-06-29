import os
import time
import cv2
import numpy as np
from detector import HumanDetector
from tracker import PersonTracker
from reid import GlobalIdentityManager


def main():
    # Prompt for input video source
    video_source = input("Enter video path for benchmarking: ").strip()
    if not video_source:
        print("❌ Video path cannot be empty.")
        return
        
    if video_source.isdigit():
        video_source = int(video_source)
        
    print("\nSelect Detection Model:")
    print("1. YOLOv8 Nano")
    print("2. RT-DETR Large")
    choice = input("Enter choice (1/2, default 1): ").strip()
    if choice == "2":
        detector = HumanDetector(model_type="rtdetr", weights="rtdetr-l.pt")
    else:
        detector = HumanDetector(model_type="yolo", weights="yolov8n.pt")
        
    tracker = PersonTracker()
    identity_manager = GlobalIdentityManager()
    
    cap = cv2.VideoCapture(video_source)
    if not cap.isOpened():
        print("❌ Cannot open video source.")
        return
        
    print("\n🚀 Starting Pipeline Benchmarking (processing up to 100 frames)...")
    
    frame_count = 0
    max_frames = 100
    
    # Timing buckets
    t_decodes = []
    t_detections = []
    t_trackings = []
    t_reids = []
    t_totals = []
    
    # Cache metrics
    total_reid_checks = 0
    neural_reid_runs = 0
    
    # Static scene bypass tracking
    prev_gray_frame = None
    bypassed_frames = 0
    last_tracked_objects = []
    
    while frame_count < max_frames:
        t0 = time.time()
        ret, frame = cap.read()
        t1 = time.time()
        if not ret or frame is None:
            break
            
        t_decodes.append(t1 - t0)
        
        # Static scene detection check
        is_static = False
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray_resized = cv2.resize(gray, (320, 240))
        
        if prev_gray_frame is not None:
            diff = cv2.absdiff(prev_gray_frame, gray_resized)
            _, thresh = cv2.threshold(diff, 20, 255, cv2.THRESH_BINARY)
            non_zero_ratio = np.count_nonzero(thresh) / thresh.size
            if non_zero_ratio < 0.003:
                is_static = True
                
        prev_gray_frame = gray_resized
        
        if is_static:
            bypassed_frames += 1
            t_detections.append(0.0)
            t_trackings.append(0.0)
            t_reids.append(0.0)
        else:
            # Detection
            t_det_start = time.time()
            detections = detector.detect(frame)
            t_detections.append(time.time() - t_det_start)
            
            # Tracking
            t_track_start = time.time()
            tracked_objects = tracker.update(frame, detections)
            t_trackings.append(time.time() - t_track_start)
            last_tracked_objects = tracked_objects
            
            # ReID
            t_reid_start = time.time()
            for (x1, y1, x2, y2, track_id, cls_id) in tracked_objects:
                local_key = (1, track_id)
                count = identity_manager.track_frame_counters.get(local_key, 0)
                existing_gid = identity_manager.track_id_to_global_id.get(local_key)
                
                total_reid_checks += 1
                
                # Active tracks dynamic stride computation
                active_count = len(tracked_objects)
                if active_count <= 2:
                    stride = 5
                elif active_count <= 5:
                    stride = 15
                else:
                    stride = 30
                    
                if existing_gid is None or count % stride == 0:
                    neural_reid_runs += 1
                    
                identity_manager.assign_global_id(
                    camera_id=1,
                    track_id=track_id,
                    frame=frame,
                    bbox=(x1, y1, x2, y2),
                    object_type=detector.model.names[cls_id],
                    current_time=frame_count / 25.0,
                    active_track_count=active_count
                )
            t_reids.append(time.time() - t_reid_start)
            
        t_totals.append(time.time() - t0)
        frame_count += 1
        
    cap.release()
    
    if frame_count == 0:
        print("❌ No frames processed.")
        return
        
    # Calculate statistics
    avg_decode = np.mean(t_decodes) * 1000
    avg_detect = np.mean(t_detections) * 1000
    avg_track = np.mean(t_trackings) * 1000
    avg_reid = np.mean(t_reids) * 1000
    avg_total = np.mean(t_totals) * 1000
    fps = 1.0 / (avg_total / 1000.0) if avg_total > 0 else 0.0
    
    cache_hits = total_reid_checks - neural_reid_runs
    hit_ratio = (cache_hits / total_reid_checks * 100.0) if total_reid_checks > 0 else 0.0
    bypass_ratio = (bypassed_frames / frame_count * 100.0) if frame_count > 0 else 0.0
    
    print("\n" + "="*50)
    print("        SENTINEL AI - PIPELINE PROFILE REPORT")
    print("="*50)
    print(f"• Total Frames Processed: {frame_count}")
    print(f"• Effective System FPS:   {fps:.1f} frames/sec")
    print(f"• Mean Total Latency:     {avg_total:.2f} ms / frame\n")
    print("Latency Breakdown per Frame (Average):")
    print(f"  - Decode/Read Frame:    {avg_decode:.2f} ms")
    print(f"  - Object Detection:     {avg_detect:.2f} ms ({(avg_detect/avg_total*100.0):.1f}%)")
    print(f"  - Local Tracking:       {avg_track:.2f} ms ({(avg_track/avg_total*100.0):.1f}%)")
    print(f"  - ReID Identity Check:  {avg_reid:.2f} ms ({(avg_reid/avg_total*100.0):.1f}%)")
    print("\nStatic Scene Bypass Statistics:")
    print(f"  - Static Frames Bypassed: {bypassed_frames} / {frame_count}")
    print(f"  - Frame Bypass Ratio:     {bypass_ratio:.1f}%")
    print("\nLazy ReID Stride Statistics:")
    print(f"  - Total GID Associations: {total_reid_checks}")
    print(f"  - ResNet50 Neural Runs:   {neural_reid_runs}")
    print(f"  - Cache Reuse Matches:    {cache_hits} (Neural forward passes saved!)")
    print(f"  - ReID Cache Hit Ratio:   {hit_ratio:.1f}%")
    print("="*50 + "\n")


if __name__ == "__main__":
    main()
