import time
import multiprocessing
import queue
import threading
import numpy as np
import os
from typing import Dict, List

# Shared latest frames manager
_manager = None
latest_frames = {}
active_workers = {}

class DummyVideoCapture:
    """Mock OpenCV VideoCapture to allow offline camera initialization at startup."""
    def isOpened(self):
        return True
    def read(self):
        return False, None
    def get(self, propId):
        return 0.0
    def set(self, propId, value):
        return True
    def release(self):
        pass

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

def overlay_live_status(frame, camera_name):
    """Draws standard live status overlays directly onto the frame at source."""
    import cv2
    from datetime import datetime
    h, w = frame.shape[:2]
    # Draw top banner
    cv2.rectangle(frame, (0, 0), (w, 26), (20, 20, 20), cv2.FILLED)
    # Red "LIVE" dot
    cv2.circle(frame, (12, 13), 5, (0, 0, 255), cv2.FILLED)
    label = f"LIVE  {camera_name}"
    cv2.putText(frame, label, (24, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 220, 255), 1)
    # Current timestamp
    ts = datetime.now().strftime("%H:%M:%S")
    cv2.putText(frame, ts, (w - 78, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)
    return frame

def run_camera_pool_worker(worker_id: int, cameras_list: List[dict], shared_frames_dict, db_lock):
    """
    Subprocess worker running the detection pipeline sequentially for a group of cameras.
    This reduces process/VRAM overhead from 200 instances to just N instances.
    """
    import os
    # Limit internal numpy/opencv threading to avoid resource fighting on Windows
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    
    import cv2
    import queue
    import threading
    import db_schema
    
    # Configure the shared database lock for safe writes across child processes (Task 8!)
    db_schema.db_lock = db_lock
    
    from app import _create_camera_runtime, _process_camera_frame, HumanDetector, GlobalIdentityManager, IncidentManager, resolve_capture_source
    
    print(f"[AI WORKER POOL {worker_id}] Booting pool worker for {len(cameras_list)} cameras...", flush=True)
    
    # Load PyTorch models exactly ONCE per pool worker process (saving VRAM/RAM)
    detector = HumanDetector(model_type="yolo", weights="yolov8n.pt")
    identity_manager = GlobalIdentityManager()
    incident_manager = IncidentManager()
    
    # Initialize runtimes and watchdog threads for all assigned cameras
    runtimes = {}
    reader_queues = {}
    stop_events = {}
    reader_threads = {}
    last_frame_times = {}
    
    orig_video_capture = cv2.VideoCapture
    
    for cam in cameras_list:
        config = cam.copy()
        config["camera_id"] = config["id"]
        
        # Test if the camera is openable.
        # If it's a network RTSP stream, do a fast socket connection test first
        # to avoid the 30-second OpenCV hang when cameras are offline.
        is_open = False
        source_str = str(config["source"])
        is_rtsp = source_str.startswith(("rtsp://", "http://", "https://"))
        
        if is_rtsp:
            try:
                # Extract host and port
                if "@" in source_str:
                    host_part = source_str.split("@")[1].split("/")[0]
                else:
                    host_part = source_str.split("//")[1].split("/")[0]
                
                if ":" in host_part:
                    host, port = host_part.split(":")
                    port = int(port)
                else:
                    host = host_part
                    port = 554
                
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5) # Fast 500ms timeout
                result = s.connect_ex((host, port))
                s.close()
                if result == 0:
                    src = resolve_capture_source(config["source"])
                    cap = orig_video_capture(src)
                    is_open = cap.isOpened()
                    cap.release()
            except Exception:
                pass
        else:
            src = resolve_capture_source(config["source"])
            cap = orig_video_capture(src)
            is_open = cap.isOpened()
            cap.release()
        
        if not is_open:
            print(f"[AI WORKER POOL {worker_id}] Camera '{cam['name']}' is offline/inaccessible at boot. Applying Dummy Capture proxy.", flush=True)
            cv2.VideoCapture = lambda *args, **kwargs: DummyVideoCapture()
            
        camera_state = _create_camera_runtime(config)
        cv2.VideoCapture = orig_video_capture # Restore original OpenCV capture class
        
        if not camera_state:
            print(f"[AI WORKER POOL {worker_id}] Error: Could not open runtime for {cam['name']}", flush=True)
            continue
            
        runtimes[cam["id"]] = camera_state
        last_frame_times[cam["id"]] = time.time()
        
        # Start watchdog queue reader thread
        frame_queue = queue.Queue(maxsize=1)
        stop_event = threading.Event()
        reader_thread = threading.Thread(
            target=reader_thread_func,
            args=(config["source"], frame_queue, stop_event),
            daemon=True
        )
        reader_thread.start()
        
        reader_queues[cam["id"]] = frame_queue
        stop_events[cam["id"]] = stop_event
        reader_threads[cam["id"]] = reader_thread
        
    print(f"[AI WORKER POOL {worker_id}] All camera runtimes initialized. Entering detection loop.", flush=True)
    
    last_process_times = {cam["id"]: 0.0 for cam in cameras_list if cam["id"] in runtimes}
    target_interval = 1.0 # 1 FPS target
    
    frames_processed = 0
    # Cycle the entire pool worker process after 10000 total frames processed to clear VRAM fragmentation
    MAX_FRAMES_TOTAL = 10000
    
    try:
        while frames_processed < MAX_FRAMES_TOTAL and any(not r.finished for r in runtimes.values()):
            processed_any = False
            now = time.time()
            
            for cam in cameras_list:
                cam_id = cam["id"]
                if cam_id not in runtimes or runtimes[cam_id].finished:
                    continue
                    
                if now - last_process_times[cam_id] >= target_interval:
                    q = reader_queues[cam_id]
                    try:
                        # Non-blocking get to prevent sequentially blocking the worker process
                        frame = q.get_nowait()
                        last_frame_times[cam_id] = now
                    except queue.Empty:
                        # Watchdog check: has camera stopped yielding frames for 10 seconds?
                        if now - last_frame_times[cam_id] > 10.0:
                            print(f"[WATCHDOG TIMEOUT POOL {worker_id}] Camera '{cam['name']}' stream hung. Reconnecting...", flush=True)
                            # Reinitialize watchdog reader thread
                            stop_events[cam_id].set()
                            reader_threads[cam_id].join(timeout=2.0)
                            
                            stop_event = threading.Event()
                            frame_queue = queue.Queue(maxsize=1)
                            reader_thread = threading.Thread(
                                target=reader_thread_func,
                                args=(cam["source"], frame_queue, stop_event),
                                daemon=True
                            )
                            reader_thread.start()
                            
                            reader_queues[cam_id] = frame_queue
                            stop_events[cam_id] = stop_event
                            reader_threads[cam_id] = reader_thread
                            last_frame_times[cam_id] = now
                        continue
                        
                    camera_state = runtimes[cam_id]
                    
                    # Intercept camera_state.cap.read() to return the watchdog queue frame
                    def fake_read():
                        return True, frame.copy()
                    camera_state.cap.read = fake_read
                    
                    # Run detection, ReID tracking, zones, incident manager
                    _process_camera_frame(camera_state, detector, identity_manager, incident_manager, "multi")
                    frames_processed += 1
                    processed_any = True
                    
                    if camera_state.display_frame is not None:
                        # Draw LIVE overlay directly in child process
                        disp = camera_state.display_frame
                        # Resize to standard width = 480 at source to reduce serialization overhead
                        h, w = disp.shape[:2]
                        if w > 480:
                            scale = 480.0 / w
                            disp = cv2.resize(disp, (480, int(h * scale)))
                        disp = overlay_live_status(disp, cam["name"])
                        
                        # Task 7: Compress to JPEG bytes inside child process to bypass Pickle serialization bottlenecks
                        ret, jpeg_buf = cv2.imencode(".jpg", disp, [cv2.IMWRITE_JPEG_QUALITY, 65])
                        if ret:
                            shared_frames_dict[cam_id] = jpeg_buf.tobytes()
                            
                    last_process_times[cam_id] = time.time()
                    
            if not processed_any:
                time.sleep(0.02) # Prevent CPU thrashing
                
        if frames_processed >= MAX_FRAMES_TOTAL:
            print(f"[AI WORKER POOL {worker_id}] Reached cycle limit ({MAX_FRAMES_TOTAL} frames). Exiting gracefully to cycle memory...", flush=True)
            
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"[AI WORKER POOL ERROR {worker_id}] Process crash: {e}", flush=True)
    finally:
        for cam_id, stop_event in stop_events.items():
            stop_event.set()
        
        # Flush pending tracking data and finalize sessions to prevent data loss
        try:
            from event import flush_tracking_data, finalize_camera_sessions
            flush_tracking_data()
            for cam_id in list(runtimes.keys()):
                finalize_camera_sessions(cam_id)
        except Exception as e:
            print(f"[AI WORKER POOL {worker_id} SHUTDOWN WARNING] Failed to flush events: {e}", flush=True)

        for cam_id, r in runtimes.items():
            r.close()

def process_monitor_thread(pools_assignment: Dict[int, List[dict]], db_lock):
    """Monitors child worker process pools and automatically respawns them if they exit."""
    global active_workers
    while True:
        try:
            for pool_id, cameras_list in pools_assignment.items():
                p = active_workers.get(pool_id)
                if p is None or not p.is_alive():
                    if p is not None:
                        try:
                            p.join(timeout=1.0)
                            p.close()
                        except Exception:
                            pass
                            
                    new_p = multiprocessing.Process(
                        target=run_camera_pool_worker,
                        args=(pool_id, cameras_list, latest_frames, db_lock),
                        daemon=True
                    )
                    new_p.start()
                    active_workers[pool_id] = new_p
                    print(f"[AI ENGINE MANAGER] Spawned Process Pool {pool_id} with {len(cameras_list)} cameras (PID: {new_p.pid})", flush=True)
        except Exception as e:
            print(f"[AI ENGINE MANAGER ERROR] Monitor loop error: {e}", flush=True)
        time.sleep(5.0)

def start_surveillance_threads():
    """Groups active cameras and starts worker pools."""
    import camera_registry
    
    init_shared_state()
    
    registry = camera_registry._load()
    cameras = registry.get("cameras", [])
    
    valid_cameras = []
    for cam in cameras:
        source = cam.get("source")
        if not source:
            continue
            
        is_rtsp = str(source).startswith(("rtsp://", "http://", "https://"))
        if is_rtsp or os.path.exists(str(source)):
            valid_cameras.append(cam)
            
    if not valid_cameras:
        print("[AI ENGINE MANAGER] No active camera sources found.", flush=True)
        return
        
    # Group cameras into 4 Worker process pools (Task 6!)
    num_pools = min(4, multiprocessing.cpu_count())
    pools_assignment = {i: [] for i in range(num_pools)}
    
    for idx, cam in enumerate(valid_cameras):
        pool_id = idx % num_pools
        pools_assignment[pool_id].append(cam)
        
    # Remove empty pools if any
    pools_assignment = {k: v for k, v in pools_assignment.items() if v}
            
    # Shared database lock for safe writes across child processes (Task 8!)
    db_lock = multiprocessing.Lock()
    
    # Start the monitor thread
    monitor_thread = threading.Thread(
        target=process_monitor_thread,
        args=(pools_assignment, db_lock),
        daemon=True
    )
    monitor_thread.start()
    print(f"[AI ENGINE MANAGER] Background monitoring thread started successfully with {len(pools_assignment)} pool workers.", flush=True)
