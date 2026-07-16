import os
import sys
import asyncio
import shutil
import time

# Windows consoles often default to a legacy code page that can't encode the
# emoji used in log output; a failed print must never crash a request.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
from typing import List, Set, Optional, Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, File, UploadFile, BackgroundTasks, Depends, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

# Ensure parent directory is in path for relative imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alerts
import school_calendar
import camera_registry
import camera_stream
import clip_service
import periodic_report
from audit_log import init_audit_db, record_audit, get_audit_log, clear_audit_log
from backend.auth import (
    ROLES,
    authenticate,
    create_token,
    current_user,
    init_users_db,
    list_users,
    require_roles,
    revoke_token,
    upsert_user,
)
from mode_manager import ModeManager, PERIOD_LABELS
from query_engine import QueryEngine
from search_service import SearchService
from event import register_event_callback, clear_event_logs
from zone_manager import get_all_zones, get_camera_zones, overwrite_zones, set_zone_alert_rules
import line_counter
from license_validator import (
    ALL_FEATURES,
    EVALUATION_FEATURES,
    EVALUATION_MAX_CAMERAS,
    LICENSE_FILE,
    decode_license_payload,
    get_hardware_fingerprint,
    get_license_info,
    is_feature_enabled,
    load_license_key,
    verify_license,
)


app = FastAPI(
    title="Sentinel AI CCTV Web Server",
    description="Role-based web service wrapping the Sentinel AI surveillance engine",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup directories for video storage
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
UPLOAD_DIR = os.path.join(STATIC_DIR, "uploads")
OUTPUT_DIR = os.path.join(STATIC_DIR, "outputs")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

active_processing_tasks = {}


def _require_feature(feature: str) -> None:
    if not is_feature_enabled(feature):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Feature '{feature}' is not included in this license tier. "
                "Contact the developers to upgrade."
            ),
        )


def process_uploaded_video_task(file_path: str, output_path: str):
    from app import process_video_headless
    active_processing_tasks[file_path] = "processing:0"

    def progress_cb(current, total):
        percent = min(99, int((current / max(1, total)) * 100))
        active_processing_tasks[file_path] = f"processing:{percent}"

    try:
        process_video_headless(file_path, output_path, progress_cb=progress_cb)
        active_processing_tasks[file_path] = "ready"
    except Exception as e:
        print(f"Error processing uploaded video: {e}")
        active_processing_tasks[file_path] = f"error: {str(e)}"


query_engine = QueryEngine()
search_service = SearchService(query_engine=query_engine)
mode_manager = ModeManager()

_main_loop: Optional[asyncio.AbstractEventLoop] = None


async def storage_cleanup_loop():
    """Background task to delete raw mp4 files older than 30 days and enforce disk quota limits"""
    while True:
        try:
            # 1. Enforce 10% disk space watchdog quota (purging oldest files)
            import shutil
            total, used, free = shutil.disk_usage(".")
            free_ratio = free / total
            if free_ratio < 0.10:
                print(f"[DISK WATCHDOG] Free disk space is critically low at {free_ratio*100:.1f}%. Starting emergency purge...", flush=True)
                target_dirs = ["alert_snapshots", UPLOAD_DIR, OUTPUT_DIR]
                files_to_check = []
                for directory in target_dirs:
                    if os.path.exists(directory):
                        for filename in os.listdir(directory):
                            filepath = os.path.join(directory, filename)
                            if os.path.isfile(filepath):
                                files_to_check.append((filepath, os.path.getmtime(filepath)))
                # Sort by oldest first
                files_to_check.sort(key=lambda x: x[1])
                deleted_count = 0
                for filepath, _ in files_to_check:
                    try:
                        os.remove(filepath)
                        deleted_count += 1
                    except Exception:
                        pass
                    _, _, current_free = shutil.disk_usage(".")
                    if current_free / total >= 0.15:
                        break
                print(f"[DISK WATCHDOG] Purge completed. Deleted {deleted_count} old files.", flush=True)

            # 2. Prune SQLite / Postgres database records older than 30 days
            try:
                from event import connect_db, adapt_query
                import time as t_mod
                thirty_days_ago_sec = t_mod.time() - (30 * 86400)
                cutoff_iso = t_mod.strftime("%Y-%m-%dT%H:%M:%S", t_mod.gmtime(thirty_days_ago_sec))
                
                with connect_db(validate_schema=False) as conn:
                    cursor = conn.cursor()
                    # Prune old tracking coordinates
                    cursor.execute(
                        adapt_query("DELETE FROM tracking_data WHERE created_at < ?"),
                        (cutoff_iso,)
                    )
                    # Prune old parsed event logs
                    cursor.execute(
                        adapt_query("DELETE FROM events WHERE timestamp < ?"),
                        (cutoff_iso,)
                    )
                    conn.commit()
                    print(f"[MAINTENANCE] Pruned events/tracking database records older than {cutoff_iso}")
            except Exception as dbe:
                print(f"[MAINTENANCE ERROR] Database record pruning failed: {dbe}")

            # 3. Traditional 30-day raw MP4 deletion
            now = time.time()
            cutoff_time = now - (30 * 86400)  # 30 days
            for directory in [UPLOAD_DIR, OUTPUT_DIR]:
                if os.path.exists(directory):
                    for filename in os.listdir(directory):
                        filepath = os.path.join(directory, filename)
                        if os.path.isfile(filepath):
                            file_age = os.stat(filepath).st_mtime
                            if file_age < cutoff_time:
                                os.remove(filepath)
                                print(f"[MAINTENANCE] Deleted old video file: {filepath}")
        except Exception as e:
            print(f"[MAINTENANCE ERROR] Storage cleanup failed: {e}")
        await asyncio.sleep(3600)  # check once every hour instead of 24h


async def daily_report_loop():
    """Emit an automated daily summary once every 24h (works off whatever the
    ingest pipeline has logged, live feed or processed video alike)."""
    # Sleep on startup to avoid firing immediately when the server restarts
    await asyncio.sleep(86400)
    while True:
        try:
            report = periodic_report.generate("daily")
            print("\n" + periodic_report.render_text(report) + "\n")
            record_audit("system", "system", "auto_daily_report",
                         target="daily", details=f"headcount={report['headcount']}")
        except Exception as exc:
            print(f"[REPORT SCHEDULER ERROR] {exc}")
        await asyncio.sleep(86400)


@app.on_event("startup")
async def startup_tasks():
    global _main_loop
    _main_loop = asyncio.get_running_loop()

    # 1. Auth + alert + audit storage
    init_users_db()
    alerts.init_alerts_db()
    init_audit_db()
    line_counter.init_lines_db()

    # 2. Background maintenance + automated daily summary
    asyncio.create_task(storage_cleanup_loop())
    asyncio.create_task(daily_report_loop())

    # 3. Initialize multiprocessing shared state
    import backend_runner
    backend_runner.init_shared_state()

    # 4. Automatically start the live AI surveillance engine processes.
    #    Set DISABLE_AI_ENGINE=1 to skip it (e.g. on a low-core dev box where the
    #    worker pools would saturate every CPU and starve live streaming).
    if os.environ.get("DISABLE_AI_ENGINE", "").strip() in ("1", "true", "True"):
        print("[AI ENGINE] Disabled via DISABLE_AI_ENGINE — use per-camera live_analytics instead.", flush=True)
    else:
        def _run_safe_ai_engine():
            try:
                backend_runner.start_surveillance_threads()
            except Exception as err:
                import traceback
                print(f"[AI ENGINE ERROR] Failed to start: {err}", flush=True)
                traceback.print_exc()

        import threading
        threading.Thread(target=_run_safe_ai_engine, daemon=True).start()

    # 4b. Pre-warm the live-analytics models so a start request never blocks the
    #     event loop reloading YOLO/OSNet.
    try:
        import live_analytics
        live_analytics.prewarm()
    except Exception as err:
        print(f"[LIVE ANALYTICS] Pre-warm scheduling failed: {err}", flush=True)

    # 3. Licensing check (empty key = evaluation mode: 1 camera, core features)
    license_key = load_license_key()
    zones = get_all_zones()
    unique_cameras = len(set(z.get("camera_id") for z in zones))
    is_valid, msg = verify_license(license_key, unique_cameras)
    print(f"\n[LICENSING] Startup check: {msg}\n")
    if not is_valid:
        print("[LICENSING ERROR] Access Denied. Web API server entering locked mode.\n")


# Active WebSocket connections for real-time security alerts
active_websockets: Set[WebSocket] = set()


def _broadcast_threadsafe(payload: dict):
    """Callbacks fire from the video-processing thread; hop onto the API loop."""
    if _main_loop is None or not active_websockets:
        return
    try:
        asyncio.run_coroutine_threadsafe(broadcast_alert(payload), _main_loop)
    except RuntimeError:
        pass


def on_new_event(event_data: dict):
    _broadcast_threadsafe({"type": "event", "data": event_data})


def on_new_alert(alert_data: dict):
    _broadcast_threadsafe({"type": "alert", "data": alert_data})


register_event_callback(on_new_event)
alerts.register_alert_callback(on_new_alert)


async def broadcast_alert(message: dict):
    if not active_websockets:
        return
    disconnected = set()
    # Convert to list to avoid 'dictionary/set changed size during iteration' race conditions
    for ws in list(active_websockets):
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.add(ws)
    for ws in disconnected:
        if ws in active_websockets:
            active_websockets.remove(ws)


class LoginRequest(BaseModel):
    username: str
    password: str


class UserRequest(BaseModel):
    username: str
    password: str
    role: str


class LicenseGenerateRequest(BaseModel):
    client_id: str
    max_cameras: int
    expiry: str                       # YYYY-MM-DD
    features: List[str]               # subset of ALL_FEATURES, or ["all"]
    fingerprint: str = "ANY"          # "ANY" = no hardware lock; else the target machine's fingerprint
    apply: bool = False               # also write the key to license.key (activate now)


class LicenseApplyRequest(BaseModel):
    license_key: str


class NLQueryRequest(BaseModel):
    query: str
    session_mode: str = "single"


class ReprocessRequest(BaseModel):
    video_path: str


class HolidayRequest(BaseModel):
    date: str


class MarkReadRequest(BaseModel):
    notification_ids: Optional[List[int]] = None


class ZoneModel(BaseModel):
    id: Optional[int] = None
    camera_id: int
    name: str
    points: List[List[float]]
    max_occupancy: Optional[int] = 3
    restricted: Optional[bool] = None
    active_hours: Optional[Dict[str, str]] = None
    school_days: Optional[List[str]] = None


class ZoneRulesModel(BaseModel):
    camera_id: int
    zone_id: int
    restricted: Optional[bool] = None
    active_hours: Optional[Dict[str, str]] = None
    school_days: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _client_ip(request: Optional[Request]) -> Optional[str]:
    if request is None or request.client is None:
        return None
    return request.client.host


def _user_from_query_token(token: str) -> Optional[dict]:
    """Resolve a login token passed as a query param (for <img>/<video> tags
    that cannot send an Authorization header)."""
    from backend.auth import _resolve_token
    return _resolve_token(token)


@app.post("/api/login")
def login(request: LoginRequest, http_request: Request = None):
    user = authenticate(request.username, request.password)
    ip = _client_ip(http_request)
    if user is None:
        record_audit(request.username, "-", "login_failed", target="auth", ip=ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password.")
    token = create_token(user["username"], user["role"])
    record_audit(user["username"], user["role"], "login", target="auth", ip=ip)
    return {"token": token, "username": user["username"], "role": user["role"]}

@app.post("/api/logout")
def logout(credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False))):
    if credentials:
        from backend.auth import revoke_token
        revoke_token(credentials.credentials)
    return {"status": "success"}


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    return {"username": user["username"], "role": user["role"]}


@app.get("/api/users", dependencies=[Depends(require_roles())])
def get_users():
    return {"users": list_users(), "roles": list(ROLES)}


@app.post("/api/users", dependencies=[Depends(require_roles())])
def create_or_update_user(request: UserRequest):
    try:
        upsert_user(request.username, request.password, request.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "success", "username": request.username, "role": request.role}


# ---------------------------------------------------------------------------
# License (developer only)
# ---------------------------------------------------------------------------

@app.get("/api/license", dependencies=[Depends(require_roles())])
def license_status():
    info = get_license_info()
    info["hardware_fingerprint"] = get_hardware_fingerprint()
    zones = get_all_zones()
    info["cameras_configured"] = len(set(z.get("camera_id") for z in zones))
    return info


@app.get("/api/license/options", dependencies=[Depends(require_roles())])
def license_options():
    """Everything the developer's License panel needs to render the mint form:
    the full feature catalogue, evaluation defaults, the presets from
    generate_key.py, this machine's fingerprint, and whether the private signing
    key is present (minting only works on a developer machine that holds it)."""
    import generate_key
    return {
        "all_features": list(ALL_FEATURES),
        "evaluation_features": list(EVALUATION_FEATURES),
        "evaluation_max_cameras": EVALUATION_MAX_CAMERAS,
        "presets": {name: {"max_cameras": c, "days_valid": d, "features": f}
                    for name, (c, d, f) in generate_key.PRESETS.items()},
        "machine_fingerprint": get_hardware_fingerprint(),
        "signing_key_available": os.path.exists(generate_key.PRIVATE_KEY_PATH),
    }


@app.post("/api/license/generate", dependencies=[Depends(require_roles())])
def license_generate(req: LicenseGenerateRequest, http_request: Request = None,
                     user: dict = Depends(require_roles())):
    """Mint a signed license key (developer only). Optionally activate it by
    writing license.key. Requires the private signing key on this machine."""
    import generate_key
    if not os.path.exists(generate_key.PRIVATE_KEY_PATH):
        raise HTTPException(status_code=409, detail=(
            "Private signing key not found on this machine — license minting is "
            "only available on a developer machine that holds dev_keys/."))

    # validate features
    requested = [f.strip() for f in req.features if f.strip()]
    unknown = [f for f in requested if f != "all" and f not in ALL_FEATURES]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown feature(s): {', '.join(unknown)}")
    if not requested:
        raise HTTPException(status_code=400, detail="Select at least one feature (or 'all').")
    if req.max_cameras < 1:
        raise HTTPException(status_code=400, detail="max_cameras must be at least 1.")
    from datetime import datetime
    try:
        datetime.strptime(req.expiry, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="expiry must be YYYY-MM-DD.")

    fingerprint = (req.fingerprint or "ANY").strip() or "ANY"
    try:
        key = generate_key.generate_license(
            req.client_id, req.max_cameras, req.expiry, fingerprint, requested)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"License generation failed: {exc}")

    applied = False
    if req.apply:
        with open(LICENSE_FILE, "w", encoding="utf-8") as handle:
            handle.write(key)
        applied = True

    record_audit(user["username"], user["role"], "license_generate",
                 target=f"{req.client_id} ({req.max_cameras} cams, {'applied' if applied else 'preview'})",
                 ip=_client_ip(http_request))
    return {
        "license_key": key,
        "applied": applied,
        "info": get_license_info(key),
    }


@app.post("/api/license/apply", dependencies=[Depends(require_roles())])
def license_apply(req: LicenseApplyRequest, http_request: Request = None,
                  user: dict = Depends(require_roles())):
    """Activate a pasted license key by writing it to license.key. Rejects a key
    whose signature does not verify, so a bad paste can't disable the system."""
    key = (req.license_key or "").strip()
    if decode_license_payload(key) is None:
        raise HTTPException(status_code=400, detail=(
            "License signature verification failed — key is malformed, tampered, "
            "or not signed by this deployment's key."))
    with open(LICENSE_FILE, "w", encoding="utf-8") as handle:
        handle.write(key)
    record_audit(user["username"], user["role"], "license_apply",
                 target="license.key replaced", ip=_client_ip(http_request))
    return {"applied": True, "info": get_license_info(key)}


# ---------------------------------------------------------------------------
# Dashboard root
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def read_root():
    index_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h3>Sentinel AI CCTV Engine is online. dashboard index.html missing.</h3>")


# ---------------------------------------------------------------------------
# Video ingest (tech team)
# ---------------------------------------------------------------------------

@app.post("/api/upload", dependencies=[Depends(require_roles("tech"))])
async def upload_video(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    filename = file.filename
    clean_name = "".join(c for c in filename if c.isalnum() or c in (".", "_", "-"))

    file_path = os.path.join(UPLOAD_DIR, clean_name)
    output_filename = f"processed_{clean_name}"
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Initialize a default whole-frame zone for camera_id 1 if no zones exist yet
    try:
        if not get_all_zones():
            overwrite_zones([
                {
                    "id": 1,
                    "camera_id": 1,
                    "name": "Whole Frame",
                    "points": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
                    "max_occupancy": 3
                }
            ])
    except Exception as e:
        print(f"Error writing default zone: {e}")

    try:
        background_tasks.add_task(process_uploaded_video_task, file_path, output_path)
    except Exception as e:
        print(f"Error starting video processing background task: {e}")
        return {
            "status": "error",
            "message": f"Video processing failed to start: {str(e)}"
        }

    return {
        "status": "processing",
        "filename": clean_name,
        "video_url": f"/static/uploads/{clean_name}",
        "output_url": f"/static/outputs/{output_filename}",
        "db_video_path": file_path
    }


@app.post("/api/reprocess", dependencies=[Depends(require_roles("tech"))])
async def reprocess_video(request: ReprocessRequest, background_tasks: BackgroundTasks):
    file_path = request.video_path
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Original video file not found.")

    # Restrict path traversal
    abs_upload_dir = os.path.normcase(os.path.abspath(UPLOAD_DIR))
    abs_file_path = os.path.normcase(os.path.abspath(file_path))
    if not abs_file_path.startswith(abs_upload_dir):
        raise HTTPException(status_code=403, detail="Access denied. Path is outside uploads folder.")

    clean_name = os.path.basename(file_path)
    output_filename = f"processed_{clean_name}"
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    try:
        background_tasks.add_task(process_uploaded_video_task, file_path, output_path)
    except Exception as e:
        print(f"Error starting video re-processing background task: {e}")
        return {
            "status": "error",
            "message": f"Video re-processing failed to start: {str(e)}"
        }

    return {
        "status": "processing",
        "filename": clean_name,
        "video_url": f"/static/uploads/{clean_name}",
        "output_url": f"/static/outputs/{output_filename}",
        "db_video_path": file_path
    }


@app.get("/api/status", dependencies=[Depends(require_roles("tech"))])
def get_processing_status(video_path: str):
    task_status = active_processing_tasks.get(video_path)
    if task_status is None:
        clean_name = os.path.basename(video_path)
        output_filename = f"processed_{clean_name}"
        output_path = os.path.join(OUTPUT_DIR, output_filename)
        if os.path.exists(output_path):
            return {"status": "ready"}
        return {"status": "unknown"}

    if isinstance(task_status, str) and task_status.startswith("processing:"):
        parts = task_status.split(":")
        percent = int(parts[1]) if len(parts) > 1 else 0
        return {"status": "processing", "progress": percent}
        
    return {"status": task_status}


@app.get("/api/test-frames", dependencies=[Depends(require_roles("tech"))])
def test_frames():
    import backend_runner
    return {
        "active_camera_ids": list(backend_runner.latest_frames.keys()),
        "has_frame": {cid: (frame is not None) for cid, frame in backend_runner.latest_frames.items()}
    }


# ---------------------------------------------------------------------------
# Search / stats (all authenticated roles)
# ---------------------------------------------------------------------------

@app.get("/api/stats", dependencies=[Depends(current_user)])
def get_stats():
    """
    Returns the aggregated security statistics and zone metrics report.
    """
    _require_feature("reports")
    report_text = query_engine.generate_security_report()
    return {
        "report": report_text
    }


@app.get("/api/reports/summary", dependencies=[Depends(current_user)])
def get_summary_report(period: str = "all"):
    """
    Headcount / vehicle / prolonged-stay summary for day / week / month / all.
    Headcount counts distinct people (unique global IDs), not raw events.
    """
    _require_feature("reports")
    if period not in PERIOD_LABELS:
        raise HTTPException(status_code=400, detail=f"period must be one of {', '.join(PERIOD_LABELS)}")
    return mode_manager.get_summary(time_frame=period)


@app.post("/api/query")
def run_natural_language_query(request: NLQueryRequest, http_request: Request = None,
                              user: dict = Depends(current_user)):
    """
    Translates natural language questions into filtered event/alert results,
    each enriched with one-click jump-to-clip metadata (why logged, timestamp,
    clip start/end/duration, details).
    """
    _require_feature("nl_search")
    q = (request.query or "").strip()
    if not q:
        # Empty query = show every logged event, in the order it was logged.
        results = query_engine.run_query(filters={}, session_mode=request.session_mode)
        results.sort(key=lambda r: (r.get("entry_time") or r.get("timestamp") or ""))
        intent = {"all_events": True}
    else:
        results = search_service.search(request.query, session_mode=request.session_mode)
        intent = search_service.last_parsed_intent()
    results = clip_service.enrich_results(results)
    record_audit(user["username"], user["role"], "nl_search",
                 target=(q or "(all events)"),
                 details=f"{len(results)} results", ip=_client_ip(http_request))
    return {
        "query": request.query,
        "session_mode": request.session_mode,
        "intent": intent,
        "results_count": len(results),
        "results": results
    }


# ---------------------------------------------------------------------------
# One-click clip playback (byte-range seekable source video)
# ---------------------------------------------------------------------------

def _is_allowed_video(path: str) -> bool:
    """Only serve files that are actually referenced by the system: an upload,
    a registered camera source, or a video_path already in the events DB."""
    if not path or not os.path.exists(path):
        return False
    norm = os.path.normcase(os.path.abspath(path))
    if norm.startswith(os.path.normcase(os.path.abspath(UPLOAD_DIR))):
        return True
    if norm.startswith(os.path.normcase(os.path.abspath(OUTPUT_DIR))):
        return True
    for cam in camera_registry.list_cameras():
        src = camera_registry.get_camera(cam["id"]).get("source")
        if src and os.path.normcase(os.path.abspath(src)) == norm:
            return True
    try:
        import sqlite3
        from db_schema import get_db_path
        conn = sqlite3.connect(get_db_path())
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM events WHERE video_path = ? LIMIT 1", (path,))
        hit = cur.fetchone() is not None
        conn.close()
        if hit:
            return True
    except Exception:
        pass
    return False


@app.get("/api/clip/video")
def clip_video(path: str, token: str = "", http_request: Request = None):
    """Serve a source video (with HTTP range support so the browser <video>
    can seek straight to the clip). Auth is via ?token= because media elements
    can't send Authorization headers."""
    user = _user_from_query_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or missing token.")
    if not _is_allowed_video(path):
        raise HTTPException(status_code=404, detail="Video not found or not permitted.")
    record_audit(user["username"], user["role"], "view_clip", target=os.path.basename(path),
                 ip=_client_ip(http_request))
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/clip/track", dependencies=[Depends(current_user)])
def clip_track(video_path: str, global_id: int = None, track_id: int = None,
               camera_id: int = None, start_frame: int = 0, end_frame: int = 0):
    """Per-frame bounding boxes (in source pixels) of the exact subject whose
    event was logged, so the clip player can highlight only that person. Boxes
    come from the tracking_data the pipeline already stored per global id."""
    if not _is_allowed_video(video_path):
        raise HTTPException(status_code=404, detail="Video not found or not permitted.")
    if global_id is None and track_id is None:
        raise HTTPException(status_code=400, detail="global_id or track_id required.")
    from event import get_tracking_data
    if end_frame <= start_frame:
        end_frame = start_frame + 1
    try:
        rows = get_tracking_data(video_path, track_id, start_frame, end_frame,
                                 camera_id=camera_id, global_id=global_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    fps = clip_service._fps_for(video_path)
    boxes = [
        {"frame": r["frame_number"], "t": round(r["frame_number"] / max(1.0, fps), 3),
         "bbox": [int(v) for v in r["bbox"]]}
        for r in rows
    ]
    return {"video_path": video_path, "global_id": global_id, "fps": round(fps, 3),
            "count": len(boxes), "boxes": boxes}


@app.get("/api/clip/running-tracks", dependencies=[Depends(current_user)])
def clip_running_tracks(video_path: str, start_frame: int = 0, end_frame: int = 0,
                        camera_id: int = None):
    """Per-frame bounding boxes for EVERY person flagged as running within the
    clip window — so the player can box all runners, not just the logged subject.

    Running events store their moment in `video_time` (their frame_* columns are
    0), and one event marks a person running for a few seconds, so we select the
    running global_ids by time (widened by the display window) and then pull each
    runner's tracked boxes across the requested frame range."""
    if not _is_allowed_video(video_path):
        raise HTTPException(status_code=404, detail="Video not found or not permitted.")
    from event import get_tracking_data
    from db_schema import connect_db

    fps = clip_service._fps_for(video_path)
    if end_frame <= start_frame:
        end_frame = start_frame + 1
    start_t = start_frame / max(1.0, fps)
    end_t = end_frame / max(1.0, fps)

    conn = connect_db(validate_schema=False)
    cursor = conn.cursor()
    sql = ("SELECT DISTINCT global_id, camera_id FROM events "
           "WHERE event_type = 'running_detected' AND video_path = ? "
           "AND video_time >= ? AND video_time <= ?")
    params = [video_path, start_t - 3.0, end_t + 0.5]
    if camera_id is not None:
        sql += " AND camera_id = ?"
        params.append(camera_id)
    runner_rows = cursor.execute(sql, params).fetchall()
    conn.close()

    subjects = []
    for gid, cam in runner_rows:
        if gid is None or gid == -1:
            continue
        try:
            rows = get_tracking_data(video_path, None, start_frame, end_frame,
                                     camera_id=cam, global_id=gid)
        except ValueError:
            continue
        boxes = [
            {"frame": r["frame_number"],
             "t": round(r["frame_number"] / max(1.0, fps), 3),
             "bbox": [int(v) for v in r["bbox"]]}
            for r in rows
        ]
        if boxes:
            subjects.append({"global_id": gid, "camera_id": cam, "boxes": boxes})

    return {"video_path": video_path, "fps": round(fps, 3),
            "subject_count": len(subjects), "subjects": subjects}


@app.get("/api/events", dependencies=[Depends(current_user)])
def get_events(global_id: int = None, camera_id: int = None, zone_id: int = None, video_path: str = None, session_mode: str = "single"):
    """
    Queries tracking events logs from the database using direct filters.
    """
    filters = {}
    if global_id is not None:
        filters["global_id"] = global_id
    if camera_id is not None:
        filters["camera_id"] = camera_id
    if zone_id is not None:
        filters["zone_id"] = zone_id
    if video_path is not None:
        filters["video_path"] = video_path

    results = query_engine.run_query(filters=filters, session_mode=session_mode)
    return {
        "filters": filters,
        "session_mode": session_mode,
        "results_count": len(results),
        "results": results
    }


# ---------------------------------------------------------------------------
# Alerts + principal notifications
# ---------------------------------------------------------------------------

@app.get("/api/alerts", dependencies=[Depends(require_roles("principal"))])
def recent_alerts(limit: int = 50):
    _require_feature("zone_alerts")
    return {"alerts": alerts.get_recent_alerts(limit=limit)}


@app.get("/api/alerts/{alert_id}/snapshot")
def alert_snapshot(alert_id: int, http_request: Request = None,
                   user: dict = Depends(require_roles("principal"))):
    alert = alerts.get_alert_by_id(alert_id)
    if alert is not None:
        snapshot_path = alert.get("snapshot_path")
        if snapshot_path and os.path.exists(snapshot_path):
            record_audit(user["username"], user["role"], "view_snapshot",
                         target=f"alert {alert_id}", ip=_client_ip(http_request))
            return FileResponse(snapshot_path, media_type="image/jpeg")
        raise HTTPException(status_code=404, detail="No snapshot stored for this alert.")
    raise HTTPException(status_code=404, detail="Alert not found.")


@app.get("/api/notifications", dependencies=[Depends(require_roles("principal"))])
def notifications(unread_only: bool = False, limit: int = 50):
    _require_feature("notifications")
    return {"notifications": alerts.get_notifications(unread_only=unread_only, limit=limit)}


@app.post("/api/notifications/read", dependencies=[Depends(require_roles("principal"))])
def mark_read(request: MarkReadRequest):
    alerts.mark_notifications_read(notification_ids=request.notification_ids)
    return {"status": "success"}


# ---------------------------------------------------------------------------
# Live alert stream
# ---------------------------------------------------------------------------

@app.websocket("/ws/alerts")
async def websocket_alerts_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for streaming real-time security events and alerts.
    Pass the login token as ?token=... (browsers can't set headers on WS).
    """
    from backend.auth import _resolve_token
    token = websocket.query_params.get("token", "")
    if _resolve_token(token) is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    active_websockets.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        active_websockets.discard(websocket)
    except Exception:
        active_websockets.discard(websocket)


# ---------------------------------------------------------------------------
# Zones + alert rules (tech team)
# ---------------------------------------------------------------------------

@app.get("/api/zones", dependencies=[Depends(current_user)])
def list_zones():
    """
    Lists all configured tracking zones.
    """
    return {"zones": get_all_zones()}


@app.get("/api/zones/camera/{camera_id}", dependencies=[Depends(current_user)])
def list_camera_zones(camera_id: int):
    """
    Lists configured tracking zones for a specific camera.
    """
    return {"zones": get_camera_zones(camera_id)}


@app.post("/api/zones", dependencies=[Depends(require_roles("tech"))])
def save_zone(zone: ZoneModel):
    """
    Saves (adds or updates) a tracking zone configuration, including alert rules.
    """
    all_zones = get_all_zones()

    # 🔑 License Validation & Camera Governance Check
    license_key = load_license_key()
    existing_cameras = set(z.get("camera_id") for z in all_zones if z.get("id") != zone.id)
    existing_cameras.add(zone.camera_id)

    is_valid, msg = verify_license(license_key, len(existing_cameras))
    if not is_valid:
        raise HTTPException(status_code=403, detail=f"License Check Failed: {msg}")

    if zone.id is None:
        ids = [z.get("id", 0) for z in all_zones]
        zone_id = max(ids, default=0) + 1
    else:
        zone_id = zone.id

    new_zone_def = {
        "id": zone_id,
        "camera_id": zone.camera_id,
        "name": zone.name,
        "points": zone.points,
        "max_occupancy": zone.max_occupancy
    }
    if zone.restricted is not None:
        new_zone_def["restricted"] = zone.restricted
    if zone.active_hours is not None:
        new_zone_def["active_hours"] = zone.active_hours
    if zone.school_days is not None:
        new_zone_def["school_days"] = zone.school_days

    updated_zones = [z for z in all_zones if z.get("id") != zone_id]
    updated_zones.append(new_zone_def)

    overwrite_zones(updated_zones)
    return {
        "status": "success",
        "message": f"Zone {zone_id} saved successfully.",
        "zone": new_zone_def
    }


@app.post("/api/zones/rules", dependencies=[Depends(require_roles("tech"))])
def update_zone_rules(rules: ZoneRulesModel):
    """
    Updates only the alert rules (restricted / active hours / school days) of an existing zone.
    """
    _require_feature("zone_alerts")
    updated = set_zone_alert_rules(
        camera_id=rules.camera_id,
        zone_id=rules.zone_id,
        restricted=rules.restricted,
        active_hours=rules.active_hours,
        school_days=rules.school_days,
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"Zone {rules.zone_id} on camera {rules.camera_id} not found.")
    return {"status": "success"}


@app.delete("/api/zones/{zone_id}", dependencies=[Depends(require_roles("tech"))])
def delete_zone(zone_id: int):
    """
    Deletes a specific tracking zone configuration.
    """
    all_zones = get_all_zones()
    updated_zones = [z for z in all_zones if z.get("id") != zone_id]

    if len(updated_zones) == len(all_zones):
        return {
            "status": "error",
            "message": f"Zone {zone_id} not found."
        }

    overwrite_zones(updated_zones)
    return {
        "status": "success",
        "message": f"Zone {zone_id} deleted successfully."
    }


# ---------------------------------------------------------------------------
# School holiday calendar (tech team)
# ---------------------------------------------------------------------------

@app.get("/api/holidays", dependencies=[Depends(current_user)])
def list_holidays():
    return {"holidays": school_calendar.list_holidays()}


@app.post("/api/holidays", dependencies=[Depends(require_roles("tech"))])
def add_holiday(request: HolidayRequest):
    if not school_calendar.add_holiday(request.date):
        raise HTTPException(status_code=400, detail="Invalid date format, expected YYYY-MM-DD.")
    return {"status": "success", "holidays": school_calendar.list_holidays()}


@app.delete("/api/holidays/{date}", dependencies=[Depends(require_roles("tech"))])
def remove_holiday(date: str):
    if not school_calendar.remove_holiday(date):
        raise HTTPException(status_code=404, detail="Date not found in the holiday list.")
    return {"status": "success", "holidays": school_calendar.list_holidays()}


# ---------------------------------------------------------------------------
# Trajectory + zone flow analytics
# ---------------------------------------------------------------------------

@app.get("/api/trajectory/{global_id}", dependencies=[Depends(current_user)])
def get_trajectory_timeline(global_id: int):
    """
    Returns the chronological timeline of zone transitions for a specific Global ID (GID).
    """
    timeline = query_engine.get_trajectory(global_id)
    return {
        "global_id": global_id,
        "timeline_count": len(timeline),
        "timeline": timeline
    }


@app.get("/api/zones/flow", dependencies=[Depends(current_user)])
def get_zones_flow():
    """
    Returns the cumulative zone flow metrics (total entries, exits, active counts, average dwell times).
    """
    report = query_engine.get_zone_flow_report()
    return {
        "zones_flow": report
    }


# ---------------------------------------------------------------------------
# Live multi-camera grid (location-based)
# ---------------------------------------------------------------------------

@app.get("/api/cameras", dependencies=[Depends(current_user)])
def list_cameras():
    """All cameras + floors for building the grid selector."""
    return {"floors": camera_registry.list_floors(), "cameras": camera_registry.list_cameras()}


@app.get("/api/cameras/by-location")
def cameras_by_location(q: str = None, floor: str = None, http_request: Request = None,
                        user: dict = Depends(current_user)):
    """Cameras for a location/floor query (e.g. q='first floor') plus a
    suggested grid layout (1 / 4 / 8 / more tiles)."""
    cams = camera_registry.cameras_for_query(query=q, floor=floor)
    record_audit(user["username"], user["role"], "view_grid",
                 target=(floor or q or "all"), details=f"{len(cams)} cameras",
                 ip=_client_ip(http_request))
    return {
        "query": q,
        "floor": floor or camera_registry.resolve_floor(q or ""),
        "count": len(cams),
        "layout": camera_registry.suggested_layout(len(cams)),
        "cameras": cams,
    }


@app.get("/api/cross-camera", dependencies=[Depends(current_user)])
def cross_camera():
    """Camera topology graph + identities (GIDs) seen on more than one camera —
    i.e. people the system handed off across cameras."""
    import sqlite3
    from db_schema import get_db_path
    conn = sqlite3.connect(get_db_path())
    cur = conn.cursor()
    cur.execute(
        "SELECT global_id, COUNT(DISTINCT camera_id) nc, GROUP_CONCAT(DISTINCT camera_id) "
        "FROM events WHERE camera_id IS NOT NULL GROUP BY global_id HAVING nc > 1 ORDER BY nc DESC LIMIT 200"
    )
    rows = cur.fetchall()
    conn.close()
    identities = [{"global_id": r[0], "camera_count": r[1],
                   "cameras": [c for c in str(r[2]).split(",")]} for r in rows]
    return {
        "topology": camera_registry.get_topology(),
        "cross_camera_identities": identities,
        "count": len(identities),
    }


@app.get("/api/cameras/{camera_id}/stream")
def camera_stream_endpoint(camera_id: int, token: str = "", http_request: Request = None):
    """Looping MJPEG live stream for one camera. Auth via ?token= (an <img>
    tag can't send an Authorization header)."""
    user = _user_from_query_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or missing token.")
    cam = camera_registry.get_camera(camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail="Camera not found.")
    
    source_str = str(cam.get("source", ""))
    is_rtsp = source_str.startswith("rtsp://") or source_str.startswith("http://")
    if not source_str or (not is_rtsp and not os.path.exists(source_str)):
        raise HTTPException(status_code=404, detail="Camera source video not available.")
        
    record_audit(user["username"], user["role"], "view_camera",
                 target=cam.get("name", f"cam {camera_id}"), ip=_client_ip(http_request))
    return StreamingResponse(
        camera_stream.mjpeg_generator(cam, fps=15),
        media_type=f"multipart/x-mixed-replace; boundary={camera_stream.BOUNDARY}",
    )


@app.get("/api/cameras/{camera_id}/snapshot")
def camera_snapshot_endpoint(camera_id: int, token: str = "", http_request: Request = None):
    """Single JPEG frame for a camera (grid thumbnail / preview)."""
    user = _user_from_query_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or missing token.")
    cam = camera_registry.get_camera(camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail="Camera not found.")
        
    source_str = str(cam.get("source", ""))
    is_rtsp = source_str.startswith("rtsp://") or source_str.startswith("http://")
    if not source_str or (not is_rtsp and not os.path.exists(source_str)):
        raise HTTPException(status_code=404, detail="Camera source not available.")
        
    jpeg = camera_stream.grab_snapshot(cam)
    if jpeg is None:
        raise HTTPException(status_code=500, detail="Could not grab frame.")
    from fastapi.responses import Response
    return Response(content=jpeg, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# Live analytics — smooth raw feed up front + full pipeline in the background.
# Reader thread streams the live tile at camera fps; a separate analytics thread
# runs detection/tracking/ReID/zones/fall/running/violence + event & alert logging
# without ever making the feed choppy. Events flow to Notifications/Search/Reports.
# ---------------------------------------------------------------------------

@app.post("/api/cameras/{camera_id}/analytics/start", dependencies=[Depends(require_roles("tech"))])
def start_camera_analytics(camera_id: int, fps: int = 3, analytics_fps: int = 0,
                           http_request: Request = None, user: dict = Depends(current_user)):
    _require_feature("core_tracking")
    import live_analytics
    rate = analytics_fps or fps  # accept either query param
    result = live_analytics.start(camera_id, analytics_fps=max(1, min(10, rate)))
    if not result.get("started") and result.get("reason") == "unknown camera_id":
        raise HTTPException(status_code=404, detail="Camera not found.")
    record_audit(user["username"], user["role"], "start_analytics",
                 target=f"camera {camera_id}", ip=_client_ip(http_request))
    return result


@app.post("/api/cameras/{camera_id}/analytics/stop", dependencies=[Depends(require_roles("tech"))])
def stop_camera_analytics(camera_id: int, http_request: Request = None,
                          user: dict = Depends(current_user)):
    import live_analytics
    result = live_analytics.stop(camera_id)
    record_audit(user["username"], user["role"], "stop_analytics",
                 target=f"camera {camera_id}", ip=_client_ip(http_request))
    return result


@app.get("/api/cameras/analytics/status", dependencies=[Depends(current_user)])
def camera_analytics_status():
    import live_analytics
    return {"workers": live_analytics.status()}


@app.get("/api/cameras/{camera_id}/analytics/summary", dependencies=[Depends(current_user)])
def get_camera_analytics_summary(camera_id: int):
    import summary_manager
    return {"summary": summary_manager.get_summary(camera_id)}



# ---------------------------------------------------------------------------
# Automated periodic summary reports (daily / monthly / yearly)
# ---------------------------------------------------------------------------

@app.get("/api/reports/periodic")
def periodic_summary(period: str = "daily", http_request: Request = None,
                     user: dict = Depends(current_user)):
    """Daily / monthly / yearly summary: headcount, crowd density, congestion,
    dwell time, and a safety-alert breakdown."""
    _require_feature("reports")
    if period not in periodic_report.PERIODS:
        raise HTTPException(status_code=400, detail=f"period must be one of {', '.join(periodic_report.PERIODS)}")
    report = periodic_report.generate(period)
    report["report_text"] = periodic_report.render_text(report)
    record_audit(user["username"], user["role"], "generate_report", target=period,
                 ip=_client_ip(http_request))
    return report


# ---------------------------------------------------------------------------
# Spatial heatmap (where people dwell) + supporting chart data
# ---------------------------------------------------------------------------

def _latest_event_video() -> Optional[str]:
    import sqlite3
    from db_schema import get_db_path
    conn = sqlite3.connect(get_db_path())
    cur = conn.cursor()
    cur.execute("SELECT video_path FROM events WHERE video_path IS NOT NULL ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


@app.get("/api/heatmap", dependencies=[Depends(current_user)])
def heatmap(video_path: str = None, grid_w: int = 48, grid_h: int = 27,
            start_frame: int = None, end_frame: int = None):
    """Density grid of person positions (bbox centroids) from tracking_data,
    binned into a grid_w x grid_h heatmap. Optional start/end frame windows it."""
    import sqlite3
    import cv2 as _cv2
    from db_schema import get_db_path

    grid_w = max(8, min(96, int(grid_w)))
    grid_h = max(6, min(54, int(grid_h)))
    if not video_path:
        video_path = _latest_event_video()
    if not video_path:
        return {"grid_w": grid_w, "grid_h": grid_h, "cells": [], "max": 0, "samples": 0}

    frame_w = frame_h = None
    try:
        cap = _cv2.VideoCapture(video_path)
        if cap.isOpened():
            frame_w = cap.get(_cv2.CAP_PROP_FRAME_WIDTH)
            frame_h = cap.get(_cv2.CAP_PROP_FRAME_HEIGHT)
        cap.release()
    except Exception:
        pass

    conn = sqlite3.connect(get_db_path())
    cur = conn.cursor()
    sql = "SELECT bbox_x1, bbox_y1, bbox_x2, bbox_y2 FROM tracking_data WHERE video_path = ?"
    params: list = [video_path]
    if start_frame is not None:
        sql += " AND frame_number >= ?"; params.append(int(start_frame))
    if end_frame is not None:
        sql += " AND frame_number <= ?"; params.append(int(end_frame))
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.execute("SELECT MAX(frame_number) FROM tracking_data WHERE video_path = ?", (video_path,))
    db_max_frame = cur.fetchone()[0] or 0
    conn.close()

    if not frame_w or not frame_h:
        frame_w = max((r[2] for r in rows), default=1) or 1
        frame_h = max((r[3] for r in rows), default=1) or 1

    grid = [[0] * grid_w for _ in range(grid_h)]
    for x1, y1, x2, y2 in rows:
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        gx = min(grid_w - 1, max(0, int(cx / frame_w * grid_w)))
        gy = min(grid_h - 1, max(0, int(cy / frame_h * grid_h)))
        grid[gy][gx] += 1

    mx = max((max(r) for r in grid), default=0)
    cells = [{"x": x, "y": y, "v": grid[y][x]}
             for y in range(grid_h) for x in range(grid_w) if grid[y][x] > 0]
    return {
        "grid_w": grid_w, "grid_h": grid_h, "cells": cells, "max": mx,
        "samples": len(rows), "video_path": video_path,
        "frame_w": int(frame_w), "frame_h": int(frame_h),
        "max_frame": int(db_max_frame),
    }


@app.get("/api/heatmap/frame", dependencies=[Depends(current_user)])
def heatmap_frame(video_path: str = None):
    """A representative (mid) frame of the video, as a JPEG, to sit behind the heatmap."""
    import cv2 as _cv2
    from fastapi.responses import Response
    if not video_path:
        video_path = _latest_event_video()
    if not video_path or not _is_allowed_video(video_path):
        raise HTTPException(status_code=404, detail="Video not available.")
    cap = _cv2.VideoCapture(video_path)
    total = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.set(_cv2.CAP_PROP_POS_FRAMES, max(0, total // 2))
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        raise HTTPException(status_code=500, detail="Could not read a frame.")
    ok, buf = _cv2.imencode(".jpg", frame)
    if not ok:
        raise HTTPException(status_code=500, detail="Could not encode frame.")
    return Response(content=buf.tobytes(), media_type="image/jpeg")


@app.get("/api/analytics/headcount-series", dependencies=[Depends(current_user)])
def headcount_series():
    """Unique people per hour-of-day (00–23) from logged sessions — feeds the
    headcount-over-time chart."""
    import sqlite3
    from db_schema import get_db_path
    conn = sqlite3.connect(get_db_path())
    cur = conn.cursor()
    cur.execute(
        "SELECT substr(entry_time, 12, 2) AS hr, COUNT(DISTINCT global_id) "
        "FROM events WHERE entry_time IS NOT NULL AND object_type='person' GROUP BY hr ORDER BY hr"
    )
    buckets = {f"{h:02d}": 0 for h in range(24)}
    for hr, cnt in cur.fetchall():
        if hr and hr.isdigit():
            buckets[f"{int(hr):02d}"] = cnt
    conn.close()
    return {"series": [{"hour": h, "count": c} for h, c in buckets.items()]}


# ---------------------------------------------------------------------------
# Directional line-crossing counters (turnstile-style entry/exit counting)
# ---------------------------------------------------------------------------

class LineModel(BaseModel):
    id: Optional[int] = None
    camera_id: int
    name: Optional[str] = None
    p1: List[float]
    p2: List[float]
    in_label: Optional[str] = "in"
    out_label: Optional[str] = "out"


@app.get("/api/lines", dependencies=[Depends(current_user)])
def list_lines(camera_id: int = None):
    lines = line_counter.get_camera_lines(camera_id) if camera_id is not None else line_counter.get_all_lines()
    return {"lines": lines}


@app.get("/api/lines/counts", dependencies=[Depends(current_user)])
def line_counts(video_path: str = None):
    """Aggregated in/out crossing counts per tripwire line."""
    return {"counts": line_counter.get_counts(video_path)}


@app.post("/api/lines", dependencies=[Depends(require_roles("tech"))])
def save_line_endpoint(line: LineModel):
    saved = line_counter.save_line({
        "id": line.id, "camera_id": line.camera_id, "name": line.name,
        "p1": line.p1, "p2": line.p2,
        "in_label": line.in_label or "in", "out_label": line.out_label or "out",
    })
    return {"status": "success", "line": saved}


@app.delete("/api/lines/{line_id}", dependencies=[Depends(require_roles("tech"))])
def delete_line_endpoint(line_id: int):
    if not line_counter.delete_line(line_id):
        raise HTTPException(status_code=404, detail="Line not found.")
    return {"status": "success", "message": f"Line {line_id} deleted."}


# ---------------------------------------------------------------------------
# Audit trail (who viewed/did what, when)
# ---------------------------------------------------------------------------

@app.get("/api/audit")
def audit_trail(username: str = None, action: str = None, limit: int = 200,
                http_request: Request = None,
                user: dict = Depends(require_roles("principal", "tech"))):
    """Trail of user actions. Viewable by developer / principal / tech (admin)."""
    record_audit(user["username"], user["role"], "view_audit", target="audit_log",
                 ip=_client_ip(http_request))
    return {"entries": get_audit_log(username=username, action=action, limit=limit)}


@app.delete("/api/audit", dependencies=[Depends(require_roles())])
def clear_audit(http_request: Request = None, user: dict = Depends(current_user)):
    """Purge the audit trail. Developer only — principal and tech (admin) can
    view the log but cannot delete it (require_roles() with no args = developer)."""
    removed = clear_audit_log()
    # Record the purge itself so the now-empty trail still shows who cleared it.
    record_audit(user["username"], user["role"], "clear_audit", target="audit_log",
                 details=f"purged {removed} entries", ip=_client_ip(http_request))
    return {"status": "cleared", "removed": removed}
