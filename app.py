from dataclasses import dataclass, field
from typing import Dict, List, Optional

import cv2
import numpy as np
import supervision as sv

from detector import HumanDetector
from incident_manager import IncidentManager
from event import (
    check_occupancy_alerts,
    clear_event_logs,
    finalize_camera_sessions,
    flush_tracking_data,
    init_db,
    log_tracking_data,
    reset_runtime_state,
    update_session_event,
)

def _draw_corner_rect(img, pt1, pt2, color, thickness, r, d):
    x1, y1 = pt1
    x2, y2 = pt2

    # Top Left
    cv2.line(img, (x1, y1), (x1 + r, y1), color, thickness)
    cv2.line(img, (x1, y1), (x1, y1 + r), color, thickness)

    # Top Right
    cv2.line(img, (x2, y1), (x2 - r, y1), color, thickness)
    cv2.line(img, (x2, y1), (x2, y1 + r), color, thickness)

    # Bottom Left
    cv2.line(img, (x1, y2), (x1 + r, y2), color, thickness)
    cv2.line(img, (x1, y2), (x1, y2 - r), color, thickness)

    # Bottom Right
    cv2.line(img, (x2, y2), (x2 - r, y2), color, thickness)
    cv2.line(img, (x2, y2), (x2, y2 - r), color, thickness)
from intent_manager import IntentManager
from query_engine import QueryEngine
from reid import GlobalIdentityManager
from tracker import PersonTracker
from video_player import play_event
from zone_manager import build_pixel_zones, draw_camera_zones, get_camera_zones, overwrite_zones

box_annotator = sv.BoxAnnotator(thickness=2)
label_annotator = sv.LabelAnnotator(text_scale=0.5, text_thickness=1)

TRACKED_CLASSES = {"person", "bicycle", "car", "motorcycle", "bus", "truck"}
DEFAULT_FPS = 25.0
TARGET_PROCESS_FPS = 20.0
MIN_FRAME_STRIDE = 2
MAX_FRAME_STRIDE = 3
PLAYBACK_JUMP_SECONDS = 2
LEFT_ARROW_KEYS = {81, 2424832, 65361}
RIGHT_ARROW_KEYS = {83, 2555904, 65363}


@dataclass
class CameraRuntime:
    camera_id: int
    name: str
    source: object
    cap: cv2.VideoCapture
    fps: float
    frame_skip: int
    jump_frames: int
    total_frames: int
    zone_defs: List[Dict]
    tracker: PersonTracker = field(default_factory=PersonTracker)
    track_type_locks: Dict[int, str] = field(default_factory=dict)
    pixel_zones: Optional[List[Dict]] = None
    display_frame: Optional[object] = None
    current_frame_number: int = 0
    finished: bool = False
    consecutive_failures: int = 0
    max_reconnect_attempts: int = 5
    is_live_stream: bool = False
    trace_annotator: sv.TraceAnnotator = field(default_factory=sv.TraceAnnotator)
    prev_gray_frame: Optional[np.ndarray] = None
    last_tracked_objects: List[Tuple] = field(default_factory=list)
    sv_zones: Optional[Dict[int, sv.PolygonZone]] = None

    def close(self) -> None:
        self.cap.release()

    def reset_tracking(self) -> None:
        self.tracker = PersonTracker()
        self.track_type_locks = {}
        self.display_frame = None
        self.trace_annotator = sv.TraceAnnotator()
        self.prev_gray_frame = None
        self.last_tracked_objects = []
        self.sv_zones = None


def _prompt_non_empty(prompt_text):
    while True:
        value = input(prompt_text).strip()
        if value:
            return value
        print("Input cannot be empty.")


def prompt_camera_configs():
    source = _prompt_non_empty("Enter video path for camera: ")
    return (
        [
            {
                "camera_id": 1,
                "name": "Camera 1",
                "source": source,
            }
        ],
        "single",
    )


def resolve_capture_source(source):
    if isinstance(source, str) and source.isdigit():
        return int(source)
    return source


def draw_zone_overlays(frame, zones_list):
    for idx, zone in enumerate(zones_list, start=1):
        cv2.rectangle(
            frame,
            (zone["x1"], zone["y1"]),
            (zone["x2"], zone["y2"]),
            (0, 128, 255),
            2,
        )

        label = zone.get("name", f"Zone {idx}")
        cv2.putText(
            frame,
            label,
            (zone["x1"], zone["y1"] - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2,
        )


def _draw_global_status(frame, paused: bool, view_label: str, incident_manager: IncidentManager = None):
    status = f"SENTINEL AI | {view_label}"
    if paused:
        status += " | PAUSED"

    # Draw header
    cv2.rectangle(frame, (0, 0), (frame.shape[1], 60), (20, 20, 20), cv2.FILLED)
    cv2.line(frame, (0, 60), (frame.shape[1], 60), (0, 150, 255), 2)
    
    cv2.putText(frame, status, (20, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
    
    controls = "SPACE: Pause | LEFT/RIGHT: Seek | Q: Quit"
    cv2.putText(frame, controls, (frame.shape[1] - 350, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)

    # Draw Incident Log (Right Side)
    if incident_manager:
        incidents = incident_manager.get_recent_incidents(5)
        panel_x = frame.shape[1] - 300
        cv2.rectangle(frame, (panel_x, 60), (frame.shape[1], 250), (20, 20, 20), cv2.FILLED)
        cv2.rectangle(frame, (panel_x, 60), (frame.shape[1], 250), (0, 150, 255), 1)
        cv2.putText(frame, "INCIDENT LOG", (panel_x + 10, 85), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 150, 255), 1)
        
        for i, inc in enumerate(incidents):
            color = (0, 0, 255) if inc["type"] == "Intrusion" else (0, 255, 255)
            text = f"[{inc['timestamp']}] GID {inc['global_id']} - {inc['type']}"
            cv2.putText(frame, text, (panel_x + 10, 115 + i * 25), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)


def capture_static_frame(camera_config):
    source = resolve_capture_source(camera_config["source"])
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        return None

    ret, frame = cap.read()
    cap.release()
    return frame if ret else None


def _frame_delta_for_seconds(fps, seconds):
    return max(1, int(round(fps * seconds)))


def _frame_stride_for_fps(fps: float) -> int:
    if fps <= 20:
        return MIN_FRAME_STRIDE
    return MAX_FRAME_STRIDE


def _seek_frame(cap, target_frame, total_frames):
    if total_frames > 0:
        target_frame = max(0, min(target_frame, total_frames - 1))
    else:
        target_frame = max(0, target_frame)

    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
    return target_frame


def _is_left_arrow(key):
    return key in LEFT_ARROW_KEYS


def _is_right_arrow(key):
    return key in RIGHT_ARROW_KEYS


def configure_zones_at_startup(camera_configs):
    if not camera_configs:
        return

    print("\n📦 Zone setup starts now. Existing zones will be overwritten in zones.json.")
    overwrite_zones([])
    all_zones = []
    next_zone_id = 1

    for camera in camera_configs:
        print(f"\n🎯 Draw zones for {camera['name']} ({camera['source']})")
        print("   Drag with the mouse, press 's' to save each zone, 'z' to remove the last one, Enter to finish.")

        frame = capture_static_frame(camera)
        if frame is None:
            print(f"⚠️ Unable to capture frame for {camera['name']}; saving no zones for this camera.")
            continue

        drawn_zones = draw_camera_zones(camera, frame, next_zone_id)
        all_zones.extend(drawn_zones)
        next_zone_id += len(drawn_zones)

    overwrite_zones(all_zones)
    print(f"💾 Saved {len(all_zones)} zone(s) to zones.json")


def _print_event_summary(results: list):
    label = results[0]["display_label"] if results else "session"
    print("\nFound events:")
    print(f"Index | {label.title():19} | Camera | Zone | Object | Global")
    for idx, event in enumerate(results):
        cameras = event.get("cameras") or [event["camera_id"]]
        camera_label = ",".join(str(camera) for camera in cameras if camera is not None)
        print(
            f"{idx:5} | {str(event['display_value'])[:19]:19} | "
            f"{camera_label[:6]:6} | {str(event['zone_id']):4} | "
            f"{event['object_type']:6} | {str(event.get('global_id', '-')):6}"
        )


def _select_event(results: list):
    while True:
        choice = input("\nSelect event number to play (blank to new query): ").strip().lower()
        if choice == "" or choice in {"b", "back"}:
            return

        if not choice.isdigit():
            print("  ➜ Please enter a valid index or press Enter to go back.")
            continue

        idx = int(choice)
        if idx < 0 or idx >= len(results):
            print("  ➜ Index out of range.")
            continue

        entry = results[idx]
        flush_tracking_data()
        play_event(entry)
        return


def run_query_mode(query_engine: QueryEngine, intent_manager: IntentManager, session_mode: str):
    while True:
        query = input(f"\nSearch query ({session_mode} mode, Enter to return): ").strip()
        if not query:
            return

        intent_manager.set_intent(query)
        filters = intent_manager.get_filters()
        results = query_engine.run_query(filters=filters, session_mode=session_mode)

        if not results:
            print("No matching events found.")
            continue

        _print_event_summary(results)
        _select_event(results)


def _create_camera_runtime(camera_config: Dict[str, object]) -> Optional[CameraRuntime]:
    source = resolve_capture_source(camera_config["source"])
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"⚠️ Unable to open {camera_config['name']} ({camera_config['source']})")
        return None

    fps = cap.get(cv2.CAP_PROP_FPS) or DEFAULT_FPS
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    zone_defs = get_camera_zones(camera_config["camera_id"])
    if not zone_defs:
        print(f"⚠️ No zones configured for {camera_config['name']}; camera will be skipped.")
        cap.release()
        return None

    is_live = False
    source_val = camera_config["source"]
    if isinstance(source_val, int) or str(source_val).isdigit():
        is_live = True
    elif isinstance(source_val, str):
        s_lower = source_val.lower().strip()
        if s_lower.startswith(("rtsp://", "rtmp://", "http://", "https://")) or s_lower.isdigit():
            is_live = True

    return CameraRuntime(
        camera_id=int(camera_config["camera_id"]),
        name=str(camera_config["name"]),
        source=camera_config["source"],
        cap=cap,
        fps=fps,
        frame_skip=_frame_stride_for_fps(fps),
        jump_frames=_frame_delta_for_seconds(fps, PLAYBACK_JUMP_SECONDS),
        total_frames=total_frames,
        zone_defs=zone_defs,
        is_live_stream=is_live,
    )


def _process_camera_frame(
    camera_state: CameraRuntime,
    detector: HumanDetector,
    identity_manager: GlobalIdentityManager,
    incident_manager: IncidentManager,
    session_mode: str,
) -> None:
    if camera_state.finished:
        return

    ret, frame = camera_state.cap.read()
    if not ret or frame is None:
        if camera_state.is_live_stream and camera_state.consecutive_failures < camera_state.max_reconnect_attempts:
            camera_state.consecutive_failures += 1
            print(f"[RECONNECT] Camera '{camera_state.name}' frame capture failed. "
                  f"Attempting reconnection {camera_state.consecutive_failures}/{camera_state.max_reconnect_attempts}...")
            camera_state.cap.release()
            import time
            time.sleep(1.5)
            resolved_src = resolve_capture_source(camera_state.source)
            camera_state.cap = cv2.VideoCapture(resolved_src)
            return

        camera_state.finished = True
        finalize_camera_sessions(camera_state.camera_id, camera_state.source)
        flush_tracking_data()
        print(f"✅ Finished processing {camera_state.name}.")
        return

    camera_state.consecutive_failures = 0

    camera_state.current_frame_number = max(0, int(camera_state.cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1)
    if camera_state.pixel_zones is None:
        camera_state.pixel_zones = build_pixel_zones(camera_state.zone_defs, frame.shape)
        if not camera_state.pixel_zones:
            print(f"⚠️ Saved zones for {camera_state.name} could not be rendered.")
            camera_state.finished = True
            return
            
        camera_state.sv_zones = {}
        for zone in camera_state.pixel_zones:
            polygon_np = np.array(zone["polygon"], dtype=np.int32)
            camera_state.sv_zones[zone["id"]] = sv.PolygonZone(
                polygon=polygon_np,
                frame_resolution_wh=(frame.shape[1], frame.shape[0])
            )

    video_time = camera_state.current_frame_number / camera_state.fps if camera_state.fps else camera_state.current_frame_number / DEFAULT_FPS

    # Motion Detection / Smart Frame Skipping
    is_static = False
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray_resized = cv2.resize(gray, (320, 240))
    
    if camera_state.prev_gray_frame is not None:
        diff = cv2.absdiff(camera_state.prev_gray_frame, gray_resized)
        _, thresh = cv2.threshold(diff, 20, 255, cv2.THRESH_BINARY)
        non_zero_ratio = np.count_nonzero(thresh) / thresh.size
        if non_zero_ratio < 0.003:
            is_static = True
            
    camera_state.prev_gray_frame = gray_resized

    if is_static and camera_state.last_tracked_objects:
        tracked_objects = camera_state.last_tracked_objects
    else:
        detections = detector.detect(frame)
        tracked_objects = camera_state.tracker.update(frame, detections)
        camera_state.last_tracked_objects = tracked_objects

    # Vectorized zone containment check using sv.PolygonZone
    track_to_zone_id = {}
    if tracked_objects and camera_state.sv_zones:
        xyxy = np.array([[obj[0], obj[1], obj[2], obj[3]] for obj in tracked_objects], dtype=np.float32)
        tracker_ids = np.array([obj[4] for obj in tracked_objects], dtype=np.int32)
        class_ids = np.array([obj[5] for obj in tracked_objects], dtype=np.int32)
        
        sv_detections = sv.Detections(
            xyxy=xyxy,
            tracker_id=tracker_ids,
            class_id=class_ids
        )
        
        for zone in camera_state.pixel_zones:
            zone_id = zone["id"]
            sv_zone = camera_state.sv_zones.get(zone_id)
            if sv_zone:
                is_inside = sv_zone.trigger(sv_detections)
                for idx, inside in enumerate(is_inside):
                    if inside:
                        track_to_zone_id[tracker_ids[idx]] = zone_id

    active_tracked_objects = []
    custom_labels = []

    for (x1, y1, x2, y2, track_id, cls_id) in tracked_objects:
        object_type = detector.model.names[cls_id]
        normalized_type = object_type.lower()

        if normalized_type not in TRACKED_CLASSES:
            continue

        locked_type = camera_state.track_type_locks.setdefault(track_id, normalized_type)
        if locked_type != normalized_type:
            print(
                f"⚠️ Discarding class mismatch for camera {camera_state.camera_id} track {track_id}: "
                f"locked={locked_type}, detected={normalized_type}"
            )
            continue

        global_id = identity_manager.assign_global_id(
            camera_id=camera_state.camera_id,
            track_id=track_id,
            frame=frame,
            bbox=(x1, y1, x2, y2),
            object_type=locked_type,
            current_time=video_time,
            active_track_count=len(tracked_objects),
        )

        log_tracking_data(
            track_id,
            global_id,
            locked_type,
            (x1, y1, x2, y2),
            camera_state.current_frame_number,
            camera_state.camera_id,
            camera_state.source,
        )

        assigned_zone_id = track_to_zone_id.get(track_id)

        update_session_event(
            track_id=track_id,
            global_id=global_id,
            object_type=locked_type,
            bbox=(x1, y1, x2 - x1, y2 - y1),
            zones=camera_state.pixel_zones,
            video_time=video_time,
            camera_id=camera_state.camera_id,
            video_path=camera_state.source,
            frame_number=camera_state.current_frame_number,
            event_mode=session_mode,
            assigned_zone_id=assigned_zone_id,
        )

        # Update Incident Risk
        session_key = ("multi", global_id) if session_mode == "multi" else ("single", camera_state.camera_id, global_id)
        from event import sessions
        session = sessions.get(session_key)
        
        risk_data = {"score": 0, "level": "LOW", "behaviors": []}
        if session:
            zone_id = session.get("zone_id")
            zone_name = "Unknown"
            for z in camera_state.pixel_zones:
                if z.get("id") == zone_id:
                    zone_name = z.get("name", f"Zone {zone_id}")
                    break
            
            risk_data = incident_manager.update_risk(global_id, {
                "duration": float(session.get("last_video_time", 0) - session.get("entry_video_time", 0)),
                "zone_name": zone_name
            })

        active_tracked_objects.append((x1, y1, x2, y2, track_id, cls_id))
        
        label = f"{locked_type} GID {global_id}"
        if risk_data["score"] > 0:
            label += f" | RISK: {risk_data['score']}"
        custom_labels.append(label)

    if active_tracked_objects:
        xyxy = np.array([[obj[0], obj[1], obj[2], obj[3]] for obj in active_tracked_objects], dtype=np.float32)
        tracker_ids = np.array([obj[4] for obj in active_tracked_objects], dtype=np.int32)
        class_ids = np.array([obj[5] for obj in active_tracked_objects], dtype=np.int32)
        
        sv_detections = sv.Detections(
            xyxy=xyxy,
            tracker_id=tracker_ids,
            class_id=class_ids
        )
        
        # 1. Draw trails
        frame = camera_state.trace_annotator.annotate(scene=frame, detections=sv_detections)
        
        # 2. Draw boxes
        frame = box_annotator.annotate(scene=frame, detections=sv_detections)
        
        # 3. Draw labels
        frame = label_annotator.annotate(scene=frame, detections=sv_detections, labels=custom_labels)

    draw_zone_overlays(frame, camera_state.pixel_zones)
    check_occupancy_alerts(
        camera_id=camera_state.camera_id,
        pixel_zones=camera_state.pixel_zones,
        frame_number=camera_state.current_frame_number,
        video_time=video_time,
        video_path=camera_state.source
    )
    camera_state.display_frame = frame


def _seek_all_cameras(camera_states: List[CameraRuntime], identity_manager: GlobalIdentityManager, direction: int) -> None:
    reset_runtime_state()
    for camera_state in camera_states:
        if camera_state.finished:
            continue

        target_frame = camera_state.current_frame_number + (direction * camera_state.jump_frames)
        _seek_frame(camera_state.cap, target_frame, camera_state.total_frames)
        camera_state.reset_tracking()
        identity_manager.clear_camera_track_mappings(camera_state.camera_id)


def _advance_cameras(camera_states: List[CameraRuntime]) -> None:
    for camera_state in camera_states:
        if camera_state.finished:
            continue

        next_frame = camera_state.current_frame_number + camera_state.frame_skip
        if camera_state.total_frames > 0 and next_frame >= camera_state.total_frames:
            camera_state.finished = True
            continue

        _seek_frame(camera_state.cap, next_frame, camera_state.total_frames)
        camera_state.display_frame = None


def run_surveillance_mode(camera_configs, detector, identity_manager, incident_manager, session_mode: str):
    if not camera_configs:
        print("❌ No camera configuration available.")
        return True

    camera_state = _create_camera_runtime(camera_configs[0])
    if camera_state is None:
        print("❌ Could not open camera stream.")
        return True

    identity_manager.clear_camera_track_mappings(camera_state.camera_id)
    print(f"\n▶ Monitoring {camera_state.name} ({session_mode} event mode)")
    print("   Controls: SPACE pause/play, LEFT/RIGHT seek, Q/Esc exit.")

    reset_runtime_state()
    paused = False
    window_name = "CCTV - Surveillance"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

    try:
        while not camera_state.finished:
            if not paused or camera_state.display_frame is None:
                import time
                t_start = time.time()
                _process_camera_frame(camera_state, detector, identity_manager, incident_manager, session_mode)
                t_duration = time.time() - t_start
                
                # Adaptive Frame Skipping based on actual processing time
                target_frame_time = 1.0 / camera_state.fps if camera_state.fps else 0.04
                if t_duration > target_frame_time:
                    camera_state.frame_skip = min(15, camera_state.frame_skip + 1)
                else:
                    base_skip = _frame_stride_for_fps(camera_state.fps)
                    camera_state.frame_skip = max(base_skip, camera_state.frame_skip - 1)

            # Draw the global HUD directly on the frame copy
            canvas = camera_state.display_frame.copy() if camera_state.display_frame is not None else None
            if canvas is not None:
                view_label = f"Frame {camera_state.current_frame_number}"
                _draw_global_status(canvas, paused, view_label, incident_manager)
                cv2.imshow(window_name, canvas)

            key = cv2.waitKeyEx(30 if paused else max(1, int(1000 / TARGET_PROCESS_FPS)))

            if key in (ord("q"), ord("Q"), 27):
                return False

            if key == ord(" "):
                paused = not paused
                continue

            if _is_left_arrow(key):
                _seek_all_cameras([camera_state], identity_manager, -1)
                continue

            if _is_right_arrow(key):
                _seek_all_cameras([camera_state], identity_manager, 1)
                continue

            if not paused:
                _advance_cameras([camera_state])
    finally:
        finalize_camera_sessions(camera_state.camera_id, camera_state.source)
        flush_tracking_data()
        camera_state.close()
        cv2.destroyWindow(window_name)

    return True


def main():
    camera_configs, session_mode = prompt_camera_configs()
    init_db()
    clear_event_logs()
    
    print("\nSelect Detection Model:")
    print("1. YOLOv8 Nano (Default - CPU & Edge optimized)")
    print("2. RT-DETR Large (Transformer-based - High Accuracy, GPU recommended)")
    model_choice = input("Enter choice (1/2, default 1): ").strip()
    if model_choice == "2":
        detector = HumanDetector(model_type="rtdetr", weights="rtdetr-l.pt")
    else:
        detector = HumanDetector(model_type="yolo", weights="yolov8n.pt")
        
    identity_manager = GlobalIdentityManager()
    incident_manager = IncidentManager()
    intent_manager = IntentManager()
    query_engine = QueryEngine()
    configure_zones_at_startup(camera_configs)

    while True:
        print("\nSelect Mode:")
        print("1. Full Surveillance Mode")
        print("2. Query-Based Mode")
        print("3. AI Session Summary")
        print("q. Quit")

        choice = input("Enter choice (1/2/3/q): ").strip().lower()

        if choice == "1":
            should_continue = run_surveillance_mode(camera_configs, detector, identity_manager, incident_manager, session_mode)
            if not should_continue:
                return
        elif choice == "2":
            run_query_mode(query_engine, intent_manager, session_mode)
        elif choice == "3":
            print("\nGenerating AI Security Report...")
            stats_report = query_engine.generate_security_report()
            print("\n" + stats_report)
            if intent_manager.llm_parser:
                summary = intent_manager.llm_parser.summarize_incidents(incident_manager.incidents)
                print("\n" + "="*50)
                print("SENTINEL AI - SECURITY DEBRIEF (LLM)")
                print("="*50)
                print(summary)
                print("="*50)
            else:
                print("\nRecent Incidents (InMemory):")
                for inc in incident_manager.get_recent_incidents(10):
                    print(f"- [{inc['timestamp']}] GID {inc['global_id']}: {inc['type']} - {inc['description']}")
        elif choice == "q":
            break
        else:
            print("Invalid choice. Please enter 1, 2 or q.")


if __name__ == "__main__":
    try:
        main()
    finally:
        cv2.destroyAllWindows()
