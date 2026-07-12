"""
Sentinel AI — automated feature test driver.

Runs the real production pipeline (detector -> tracker -> re-id -> zones ->
event/alert logic) headlessly over a single video file, writes an annotated
output video, captures snapshot evidence, then dumps every feature's results
straight out of the SQLite database. Also runs the unit-test suites, the
natural-language search service, the headcount report, and the licensing
governance layer, so a single run exercises every implemented feature.

Nothing here is a mock: it imports and calls the same functions app.py uses.
"""

import io
import json
import os
import sys
import contextlib
from datetime import datetime

# UTF-8 console (matches app.py) so emoji log lines never crash on Windows.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

import cv2

REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feature_test_report")
FRAMES_DIR = os.path.join(REPORT_DIR, "frames")
os.makedirs(FRAMES_DIR, exist_ok=True)

VIDEO_PATH = sys.argv[1] if len(sys.argv) > 1 else r"D:\Downloads\view-IP2 (1).mp4"
MAX_PROCESS_FRAMES = int(os.environ.get("MAX_PROCESS_FRAMES", "0"))  # 0 = whole video


def probe_video(path):
    cap = cv2.VideoCapture(path)
    info = {
        "path": path,
        "opened": cap.isOpened(),
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps": cap.get(cv2.CAP_PROP_FPS),
        "frame_count": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
    }
    info["duration_sec"] = round(info["frame_count"] / info["fps"], 1) if info["fps"] else None
    cap.release()
    return info


def run_pipeline(video_info):
    """Process the video with the real pipeline, writing an annotated video and snapshots."""
    import app
    from detector import HumanDetector
    from reid import GlobalIdentityManager
    from incident_manager import IncidentManager
    from alerts import init_alerts_db, get_notifications
    from event import (
        clear_event_logs, finalize_camera_sessions, flush_tracking_data,
        reset_runtime_state, init_db as _init_db,
    )
    from running import reset_running_state
    from fall_detector import reset_fall_state

    _init_db()
    init_alerts_db()
    clear_event_logs()

    # Local YOLOv8n weights already ship in the repo -> no network download needed.
    detector = HumanDetector(model_type="yolo", weights="yolov8n.pt")
    identity_manager = GlobalIdentityManager()
    incident_manager = IncidentManager()

    camera_config = {"camera_id": 1, "name": "Camera 1", "source": video_info["path"]}
    camera_state = app._create_camera_runtime(camera_config)
    if camera_state is None:
        raise RuntimeError("Could not open video for the pipeline run.")

    # Optional stride override so a full-length clip can be covered in one CPU pass.
    frame_skip_override = int(os.environ.get("FRAME_SKIP", "0"))
    if frame_skip_override > 0:
        camera_state.frame_skip = frame_skip_override

    print(f"[pipeline] zones loaded: {[z['name'] for z in camera_state.zone_defs]}; "
          f"frame_skip={camera_state.frame_skip}")

    out_path = os.path.join(REPORT_DIR, "annotated_output.mp4")
    w = int(camera_state.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(camera_state.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = camera_state.fps or 25.0
    writer = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), max(1.0, fps / max(1, camera_state.frame_skip)), (w, h))

    identity_manager.clear_camera_track_mappings(camera_state.camera_id)
    reset_runtime_state()
    reset_running_state()
    reset_fall_state()

    total = max(1, camera_state.total_frames)
    processed = 0
    snapshots = []
    last_notif_count = 0
    snapshot_every = max(1, (MAX_PROCESS_FRAMES or total) // (camera_state.frame_skip or 2) // 8)

    real_out = sys.__stdout__
    def progress(msg):
        print(msg, file=real_out, flush=True)

    # The production pipeline prints a very verbose per-frame ReID match trace.
    # Route it to a log file so this run's console stays readable.
    log_path = os.path.join(REPORT_DIR, "pipeline_log.txt")
    logf = open(log_path, "w", encoding="utf-8", errors="replace")
    with contextlib.redirect_stdout(logf):
        while not camera_state.finished:
            app._process_camera_frame(camera_state, detector, identity_manager, incident_manager, "single")
            frame = camera_state.display_frame
            if frame is not None:
                writer.write(frame)

                # Snapshot whenever the principal inbox grows (a real alert fired).
                notif_count = len(get_notifications(unread_only=False, limit=200))
                if notif_count > last_notif_count:
                    last_notif_count = notif_count
                    p = os.path.join(FRAMES_DIR, f"alert_frame_{camera_state.current_frame_number:06d}.jpg")
                    cv2.imwrite(p, frame)
                    snapshots.append(p)

                # Periodic evidence frames spread across the run.
                if processed % snapshot_every == 0:
                    p = os.path.join(FRAMES_DIR, f"periodic_frame_{camera_state.current_frame_number:06d}.jpg")
                    cv2.imwrite(p, frame)
                    snapshots.append(p)

            processed += 1
            if processed % 25 == 0:
                progress(f"[pipeline] processed {processed} frames "
                         f"(video frame {camera_state.current_frame_number}/{total}, "
                         f"alerts so far={last_notif_count})")

            if MAX_PROCESS_FRAMES and processed >= MAX_PROCESS_FRAMES:
                break

            next_frame = camera_state.current_frame_number + camera_state.frame_skip
            if camera_state.total_frames > 0 and next_frame >= camera_state.total_frames:
                break
            app._seek_frame(camera_state.cap, next_frame, camera_state.total_frames)
            camera_state.display_frame = None
    logf.close()

    finalize_camera_sessions(camera_state.camera_id, camera_state.source)
    flush_tracking_data()
    camera_state.close()
    writer.release()
    print(f"[pipeline] done: {processed} frames processed, annotated video -> {out_path}")
    return {"processed_frames": processed, "annotated_video": out_path, "snapshots": snapshots}


def dump_database():
    """Read every feature table straight out of SQLite."""
    import sqlite3
    from db_schema import get_db_path

    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    out = {}

    # Event-type breakdown (sessions + occupancy + dress code + running).
    cur.execute("SELECT event_type, COUNT(*) c FROM events GROUP BY event_type ORDER BY c DESC")
    out["event_type_counts"] = {r["event_type"]: r["c"] for r in cur.fetchall()}

    cur.execute("SELECT object_type, COUNT(*) c FROM events WHERE object_type IN ('person','car','truck','bus','motorcycle','bicycle') GROUP BY object_type")
    out["object_type_counts"] = {r["object_type"]: r["c"] for r in cur.fetchall()}

    def rows(sql, args=()):
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]

    out["zone_sessions_sample"] = rows(
        "SELECT global_id, object_type, camera_id, zone_id, entry_time, exit_time, "
        "ROUND(duration,2) duration, stayed FROM events WHERE event_type='leaving' "
        "ORDER BY duration DESC LIMIT 15")
    out["staying_loitering"] = rows(
        "SELECT global_id, zone_id, ROUND(duration,2) duration, entry_time FROM events "
        "WHERE event_type='leaving' AND stayed=1 ORDER BY duration DESC LIMIT 15")
    out["occupancy_alerts"] = rows(
        "SELECT camera_id, zone_id, duration current_count, stayed limit_val, video_time, timestamp "
        "FROM events WHERE event_type='occupancy_alert' ORDER BY timestamp LIMIT 15")
    out["dress_code_violations"] = rows(
        "SELECT global_id, camera_id, ROUND(video_time,2) video_time, timestamp FROM events "
        "WHERE event_type='dress_code_violation' ORDER BY timestamp LIMIT 15")
    out["running_events"] = rows(
        "SELECT global_id, camera_id, ROUND(video_time,2) video_time, timestamp FROM events "
        "WHERE event_type='running_detected' ORDER BY timestamp LIMIT 15")

    # Alerts table (restricted / after-hours / fall) + notifications.
    def table_exists(name):
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
        return cur.fetchone() is not None

    if table_exists("alerts"):
        cur.execute("SELECT alert_type, COUNT(*) c FROM alerts GROUP BY alert_type")
        out["alert_type_counts"] = {r["alert_type"]: r["c"] for r in cur.fetchall()}
        out["alerts_sample"] = rows(
            "SELECT id, alert_type, zone_name, camera_id, global_id, frame_number, "
            "snapshot_path, message FROM alerts ORDER BY id LIMIT 20")
    if table_exists("notifications"):
        out["notifications_sample"] = rows(
            "SELECT n.id, n.title, n.message, n.read, a.snapshot_path "
            "FROM notifications n LEFT JOIN alerts a ON a.id=n.alert_id ORDER BY n.id LIMIT 20")

    cur.execute("SELECT COUNT(*) c, COUNT(DISTINCT global_id) g, COUNT(DISTINCT track_id) t FROM tracking_data")
    r = cur.fetchone()
    out["tracking_data"] = {"rows": r["c"], "distinct_global_ids": r["g"], "distinct_track_ids": r["t"]}

    conn.close()
    return out


def run_nl_search():
    """Natural-language search over the populated DB (rule-based, no LLM key needed)."""
    from search_service import SearchService
    queries = [
        "show me people who entered the restricted zone",
        "intrusion",
        "falls",
        "running",
        "uniform violations",
        "people in zone 2",
        "cars",
    ]
    svc = SearchService()
    results = {}
    for q in queries:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                res = svc.search(q, session_mode="single")
                intent = svc.last_parsed_intent()
            except Exception as exc:  # keep going on any single query
                results[q] = {"error": str(exc)}
                continue
        results[q] = {
            "parsed_intent": intent,
            "result_count": len(res),
            "sample": [
                {k: v for k, v in row.items() if k in
                 ("display_label", "display_value", "event_type", "global_id",
                  "zone_id", "camera_id", "object_type", "duration")}
                for row in res[:5]
            ],
        }
    return results


def run_headcount():
    from mode_manager import ModeManager
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ModeManager().print_summary_report(use_zones=True, time_frame="all")
        data = ModeManager().get_summary("all")
    return {"report_text": buf.getvalue(), "summary": data}


def run_licensing():
    from license_validator import (get_license_info, verify_license, is_feature_enabled,
                                   get_hardware_fingerprint, ALL_FEATURES)
    fp = get_hardware_fingerprint()
    out = {"hardware_fingerprint": fp}

    # Evaluation mode (no key) — the state a client machine is in with no license.
    info = get_license_info("")
    ok, msg = verify_license("", 1)
    ok2, msg2 = verify_license("", 2)
    out["evaluation_mode"] = {"mode": info["mode"], "max_cameras": info["max_cameras"],
                              "features": info["features"],
                              "one_camera_ok": ok, "one_camera_msg": msg,
                              "two_cameras_ok": ok2, "two_cameras_msg": msg2}

    # Garbage / old demo-password keys must be rejected (verified with the public key).
    out["password_bypass_rejected"] = {}
    for pw in ("123456789", "SentinelDemo2026", "AdivaDemo2026"):
        okp, msgp = verify_license(pw, 1)
        out["password_bypass_rejected"][pw] = {"ok": okp, "msg": msgp}

    # Minting real signed licenses needs the developer-only private key, which is
    # gitignored and absent on a client machine. Only run those checks if present.
    have_dev_key = os.path.exists(os.path.join("dev_keys", "license_signing_key.pem"))
    out["dev_signing_key_present"] = have_dev_key
    if have_dev_key:
        from generate_key import generate_license
        key = generate_license("SCHOOL_DISTRICT_A", 10, "2099-01-01", fp, ["all"])
        ok3, msg3 = verify_license(key, 5)
        out["valid_license"] = {"ok": ok3, "msg": msg3,
                                "fall_enabled": is_feature_enabled("fall_detection", key)}
        ok4, msg4 = verify_license(key[:-4] + "AAAA", 5)
        out["tampered_license"] = {"ok": ok4, "msg": msg4}
        exp = generate_license("SCHOOL_DISTRICT_A", 10, "2020-01-01", fp, ["all"])
        ok5, msg5 = verify_license(exp, 5)
        out["expired_license"] = {"ok": ok5, "msg": msg5}
    else:
        out["note"] = ("dev_keys/license_signing_key.pem absent — license minting "
                       "checks skipped (developer-only). Signature verification, "
                       "evaluation mode, and bypass rejection still validated above.")
    return out


def run_unit_tests():
    import subprocess
    results = {}
    for mod in ("test_fall", "test_licensing", "test_reid"):
        proc = subprocess.run([sys.executable, "-m", "unittest", mod, "-v"],
                              capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(__file__)))
        tail = (proc.stderr or proc.stdout).strip().splitlines()
        results[mod] = {"returncode": proc.returncode, "tail": tail[-8:]}
    return results


def main():
    report = {"generated_at": datetime.now().isoformat(), "video": None}
    print("=" * 70)
    print("SENTINEL AI — FEATURE TEST DRIVER")
    print("=" * 70)

    report["video"] = probe_video(VIDEO_PATH)
    print("[video]", json.dumps(report["video"]))
    if not report["video"]["opened"]:
        print("ERROR: cannot open video")
        return

    import traceback
    def section(name, fn):
        try:
            report[name] = fn()
        except Exception as exc:
            report[name] = {"error": str(exc), "traceback": traceback.format_exc()}
            print(f"[{name}] ERROR: {exc}")

    section("pipeline", lambda: run_pipeline(report["video"]))
    section("database", dump_database)
    section("nl_search", run_nl_search)
    section("headcount", run_headcount)
    section("licensing", run_licensing)
    section("unit_tests", run_unit_tests)

    out_json = os.path.join(REPORT_DIR, "results.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n[report] wrote {out_json}")
    print("[report] DONE")


if __name__ == "__main__":
    main()
