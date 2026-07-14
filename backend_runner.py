import time
import multiprocessing
import queue
import threading
import numpy as np
import os
from typing import Dict

# Shared latest frames manager
_manager = None
latest_frames = {}

def init_shared_state():
    """Initializes the multiprocessing manager dictionary for sharing frames between processes."""
    global _manager, latest_frames
    if _manager is None:
        if multiprocessing.current_process().name == 'MainProcess':
            try:
                _manager = multiprocessing.Manager()
                latest_frames = _manager.dict()
                print("[AI ENGINE MANAGER] Multiprocessing Shared Memory Manager initialized successfully.", flush=True)
            except Exception as e:
                print(f"[AI ENGINE MANAGER WARNING] Failed to initialize Manager. Falling back to local dict. Error: {e}", flush=True)
                latest_frames = {}

def reader_thread_func(source, frame_queue, stop_event):
    """
    Background thread that continuously grabs raw frames from the VideoCapture source.
    This separates network frame acquisition from YOLO inference, preventing OpenCV deadlocks.
    """
    import cv2
    from app import resolve_capture_source
    cap = None
    
    while not stop_event.is_set():
        if cap is None or not cap.isOpened():
            resolved = resolve_capture_source(source)
            cap = cv2.VideoCapture(resolved)
            if not cap.isOpened():
                time.sleep(2.0)
                continue
        
        try:
            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(0.1)
                continue
                
            # Keep only the latest frame in queue (size=1)
            if frame_queue.full():
                try:
                    frame_queue.get_nowait()
                except queue.Empty:
                    pass
            frame_queue.put(frame)
        except Exception:
            time.sleep(0.5)
            
    if cap is not None:
        cap.release()

def run_camera_process(camera_config, shared_frames_dict):
    """
    Subprocess worker running the detection pipeline for a single camera.
    Each camera runs in its own Process, completely bypassing Python's GIL.
    """
    import os
    # Limit internal numpy/opencv threading to avoid resource fighting on Windows
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    
    import cv2
    import queue
    import threading
    from app import _create_camera_runtime, _process_camera_frame, HumanDetector, GlobalIdentityManager, IncidentManager
    
    config = camera_config.copy()
    config["camera_id"] = config["id"]
    
    print(f"[AI ENGINE WORKER] Booting isolated process for {config['name']} (ID {config['id']})", flush=True)
    
    # Load PyTorch models inside the worker process context
    detector = HumanDetector(model_type="yolo", weights="yolov8n.pt")
    identity_manager = GlobalIdentityManager()
    incident_manager = IncidentManager()
    
    # Initialize the camera runtime structure
    camera_state = _create_camera_runtime(config)
    if not camera_state:
        print(f"[AI ENGINE WORKER] Error: Could not open runtime for {config['name']}", flush=True)
        return

    # Start the non-blocking reader thread with the watchdog queue
    frame_queue = queue.Queue(maxsize=1)
    stop_event = threading.Event()
    reader_thread = threading.Thread(
        target=reader_thread_func,
        args=(config["source"], frame_queue, stop_event),
        daemon=True
    )
    reader_thread.start()

    print(f"[AI ENGINE WORKER] Watchdog Queue Reader active for {config['name']}", flush=True)
    
    last_process_time = 0.0
    # Process at 1.0 FPS target in the background to ensure low CPU/VRAM usage and 24/7 stability
    target_interval = 1.0 
    
    try:
        while not camera_state.finished:
            now = time.time()
            if now - last_process_time < target_interval:
                time.sleep(0.05)
                continue
                
            try:
                # Read from watchdog queue with strict 5-second timeout to handle RTSP freezes
                frame = frame_queue.get(timeout=5.0)
            except queue.Empty:
                print(f"[WATCHDOG TIMEOUT] Camera '{config['name']}' has not yielded a frame in 5 seconds! Reconnecting stream...", flush=True)
                # Restart reader thread
                stop_event.set()
                reader_thread.join(timeout=2.0)
                
                # Reinitialize connection
                stop_event = threading.Event()
                frame_queue = queue.Queue(maxsize=1)
                reader_thread = threading.Thread(
                    target=reader_thread_func,
                    args=(config["source"], frame_queue, stop_event),
                    daemon=True
                )
                reader_thread.start()
                continue
                
            # Intercept camera_state.cap.read() to return the watchdog queue frame
            def fake_read():
                return True, frame.copy()
            camera_state.cap.read = fake_read
            
            # Run tracking, alerts and zones
            _process_camera_frame(camera_state, detector, identity_manager, incident_manager, "multi")
            
            if camera_state.display_frame is not None:
                # Store the fully annotated frame in shared memory
                shared_frames_dict[config["id"]] = camera_state.display_frame
                
            last_process_time = time.time()
            
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"[AI ENGINE WORKER ERROR] Process crash on {config['name']}: {e}", flush=True)
    finally:
        stop_event.set()
        camera_state.close()

def start_surveillance_threads():
    """Reads camera registry and starts isolated background processes for all active cameras."""
    import camera_registry
    
    init_shared_state()
    
    registry = camera_registry._load()
    cameras = registry.get("cameras", [])

    for cam in cameras:
        source = cam.get("source")
        if not source:
            continue
            
        is_rtsp = str(source).startswith(("rtsp://", "http://", "https://"))
        if is_rtsp or os.path.exists(str(source)):
            p = multiprocessing.Process(
                target=run_camera_process,
                args=(cam, latest_frames),
                daemon=True
            )
            p.start()
            print(f"[AI ENGINE MANAGER] Spawned Multiprocessing Process for {cam['name']} (PID: {p.pid})", flush=True)
