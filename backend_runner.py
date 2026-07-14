import time
import threading
from typing import Dict
import numpy as np
import os

latest_frames: Dict[int, np.ndarray] = {}

def camera_worker(camera_config, detector, identity_manager, incident_manager):
    config = camera_config.copy()
    config["camera_id"] = config["id"]
    
    from app import _create_camera_runtime, _process_camera_frame
    
    print(f"[AI ENGINE] Initializing stream for {config['name']} (ID {config['id']})...", flush=True)
    camera_state = _create_camera_runtime(config)
    if not camera_state:
        print(f"[AI ENGINE] Error: Could not open stream for {config['name']}", flush=True)
        return
        
    print(f"[AI ENGINE] Started processing {config['name']}", flush=True)
    while not camera_state.finished:
        try:
            _process_camera_frame(camera_state, detector, identity_manager, incident_manager, "multi")
            if camera_state.display_frame is not None:
                # Store the fully annotated frame in memory
                latest_frames[config["id"]] = camera_state.display_frame
        except Exception as e:
            print(f"[AI ENGINE ERROR] Camera {config['id']}: {e}", flush=True)
            time.sleep(1.0)
        time.sleep(0.01) # Prevent CPU thrashing

def start_surveillance_threads():
    import camera_registry
    from app import HumanDetector, GlobalIdentityManager, IncidentManager
    
    # Load shared AI modules once
    print("[AI ENGINE] Loading YOLO detector...", flush=True)
    detector = HumanDetector(model_type="yolo", weights="yolov8n.pt")
    identity_manager = GlobalIdentityManager()
    incident_manager = IncidentManager()

    registry = camera_registry._load()
    cameras = registry.get("cameras", [])

    for cam in cameras:
        source = cam.get("source")
        if not source:
            continue
        
        is_rtsp = str(source).startswith(("rtsp://", "http://", "https://"))
        if is_rtsp or os.path.exists(str(source)):
            t = threading.Thread(
                target=camera_worker, 
                args=(cam, detector, identity_manager, incident_manager), 
                daemon=True
            )
            t.start()
            print(f"[AI ENGINE] Started background thread for {cam['name']}", flush=True)
