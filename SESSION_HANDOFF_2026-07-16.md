# Sentinel AI — Session Handoff (2026-07-16)

Paste this into a new chat so it knows what happened. This session was long; below is
everything material.

## Project & environment
- **Sentinel AI** — school/campus CCTV video-analytics platform. FastAPI backend +
  single-file HTML dashboard (`backend/index.html`). Pipeline: YOLOv8n detect → ByteTrack
  → OSNet (torchreid) cross-camera Re-ID → zones/behaviours → events/alerts.
- **Repo:** `https://github.com/haronnk/SENTINEL-2.0` (owner haronnk, git identity
  `Haronnk <haronk0604@gmail.com>`). Path: `D:\COLLEGE\Sentinel` = `/mnt/d/COLLEGE/Sentinel`.
- **Env:** WSL Ubuntu, **CPU-only** Python 3.13 venv at `.venv` (torch CPU build). Run via
  `wsl ./.venv/bin/python ...`. 8-core box, no GPU. Bash tool = Git Bash (mangles /mnt/d
  paths — run python inside `wsl bash -lc '...'`). Windows host on WiFi 192.168.1.6.
- **License:** `ADIVA_TRIAL`, 20 cams, all features, `hardware_hash: ANY`, expires
  2026-08-13 — in `license.key` (gitignored). Friend rotated the signing key this session;
  the public key in `license_validator.py` is now `fc1b893e…` (not the old dev key).

## Git state at end of session
- **All work committed & pushed.** HEAD = **`571b872`** on `main` (base was friend's
  `96d78d4`). Nothing uncommitted except this handoff file.
- Secrets (`dev_keys/`, `license.key`, `.venv`) and artifacts (`reid_crops/`, `models/`,
  `*.db-wal/-shm`, `frames/`) are gitignored.

## What was built/fixed this session (all in `571b872`)

### GPU-saving optimizations (the main recent work)
1. **Adaptive-rate gate** (`live_analytics.py`, `app.py`): analytics runs at 3 fps when a
   person is present, **1 fps when the camera is empty**, 8 s hangover. Keyed off real YOLO
   detections (a motionless/fallen person is still detected → keeps processing; no
   motion-sensor blind spot). **Measured ~36% fewer pipeline runs on quiet cameras**, ~0%
   on busy ones. `app.py` exposes `camera_state.last_person_count`.
2. **Deferred OSNet Re-ID** (`reid.py`, `inference_config.py`, `reid_search.py`): env
   `REID_DEFERRED=1` → OSNet NOT run live (cheap colour/edge fallback instead); saves
   representative crops to `reid_crops/`; full OSNet cross-camera matching runs **on-demand**
   via `reid_search.search(gid)`. **Measured: one OSNet Re-ID = 39 ms → 0.2 ms deferred**
   (~200×). System-level was only ~14% on CPU because (a) an embedding cache already limits
   OSNet calls, (b) CPU total is dominated by non-GPU work (drawing/tracking) — **on a GPU
   the OSNet share is larger**. Trade-off: live cross-camera Global-IDs become unreliable
   (that's why we defer); per-camera events (fall/loiter/intrusion/running) stay live.

### Live analytics (decoupled feed)
- `live_analytics.py` runs **two threads per camera**: reader/display pushes the **raw feed
  at ~20 fps** (smooth, no AI) to `backend_runner.latest_frames`; analytics thread runs the
  pipeline at ~3 fps (events → DB → Notifications). **Display never gated by analysis.**
- New endpoints: `POST /api/cameras/{id}/analytics/start|stop`, `GET
  /api/cameras/analytics/status`. New **"Run live analytics"** button in the dashboard's
  **Video Analytics** tab (developer/tech only), with a live status bar.

### Stability fixes (were causing "failed to fetch on all tabs" crashes)
- **`backend_runner.py`**: `FrameInjector` replaces the illegal `camera_state.cap.read =
  fake_read` monkey-patch (read-only C attr → workers crash-looped). **The pool engine is
  still fork-unsafe upstream** — run with `DISABLE_AI_ENGINE=1` and use per-camera
  live_analytics instead.
- **`app.py`**: removed a redundant local `import time` in `_process_camera_frame` that
  caused `UnboundLocalError: time` on every frame.
- **`camera_stream.py`**: web process no longer decodes RTSP (it crashed the server);
  live cameras serve cache-or-placeholder; FFmpeg TCP+timeout hardening. MJPEG at 15 fps.
- **`backend/server.py`**: `DISABLE_AI_ENGINE` env toggle; models pre-warmed at startup
  (loading them in a request thread froze the event loop).
- **`db_schema.py`**: `SENTINEL_DB_PATH` env. **SQLite on `/mnt/d` (WSL drvfs) is ~3 s PER
  DB CONNECTION → every /api call was 3–7 s.** Fixed by moving DB to native ext4:
  `/root/sentinel_data/cctv_logs.db` (a WAL-checkpointed copy of the original). ~1000× faster.

### Docs/tooling created
- `gpu_benchmark.py` (measures fps / cameras-per-GPU, honours `REID_DEFERRED`).
- `REPORT_for-friend_optimizations-and-GPU.md` (changes + GPU-laptop setup + how to measure).
- `SENTINEL_TECHNICAL_OVERVIEW.md`, `SENTINEL_DEPLOYMENT_GUIDE.md`,
  `UPDATE_2026-07-15_stability-and-live-analytics.md`. Earlier: `SENTINEL_FEATURES.md`,
  `Sentinel_Features.docx`, `Sentinel_Hosting_Proposal.docx` (in feature_test_report/).

## How to run (this WSL box)
```bash
wsl bash -lc 'cd /mnt/d/COLLEGE/Sentinel && DISABLE_AI_ENGINE=1 \
  SENTINEL_DB_PATH=/root/sentinel_data/cctv_logs.db \
  ./.venv/bin/python -m uvicorn backend.server:app --host 0.0.0.0 --port 8000'
# http://localhost:8000  · login developer / dev@sentinel
# Video Analytics → pick camera → Run live analytics
```
**Server is currently STOPPED.** Uvicorn without --reload needs a full restart to pick up
code changes; kill with `pkill -9 -f 'uvicorn backend.server'`. Background runs via the
Bash tool's `run_in_background` (foreground uvicorn) survive; `wsl "... &"` does NOT.

## Verified this session
- Licensing enforcement (camera cap 403, feature gating 403, immediate effect) — live.
- OSNet→ONNX export parity 0.9997 cosine (from earlier commit `f7c645d`).
- Live analytics dry run: display ~21 fps + analytics ~3 fps, events logged, all tabs <0.8 s
  after DB fix, no crash. Cam 1 (Adiva RTSP) showed real annotated + raw feed.

## Open items / next steps
- **Camera hardware (unresolved):** a 2nd Adiva camera won't join the LAN. Ruled OUT
  same-IP conflict (nothing at .20 when cam 1 unplugged) and cable/port (cam 1 works in both
  GE1/GE2). Likely **power or router-port** issue. Next: check router DHCP list at
  `http://192.168.1.1` for the new camera (MAC starts `00-12-34…` like cam 1 = `00-12-34-CE-2C-07`);
  try cam 1's power adapter on it. Cam 1 works at `rtsp://admin:@192.168.1.20:554/h264/ch1/main/av_stream`.
- Friend (GPU laptop) to run `gpu_benchmark.py` (`REID_DEFERRED=0` vs `1`, watch
  `nvidia-smi`) for real GPU numbers. Needs `license.key` sent separately (ANY-hardware key
  works anywhere). Install CUDA torch in a fresh venv (repo .venv is CPU-only).
- Optional easy wins: turn OFF annotation drawing in the analytics thread (it draws onto a
  frame that's never displayed — wasted CPU); wire crop-saving into the pipeline + a
  `/api/reid/search` endpoint for the on-demand cross-camera search.
- Upstream: make the pool engine spawn-safe (or retire it for live_analytics); move to
  PostgreSQL for prod; harden licensing (production keypair off-machine + online activation)
  before any sale.
- Deferred Re-ID is a **product decision** (cross-camera identity becomes search-time, not
  live) — confirm with the technical lead. Deployment target discussed: **Edge + Cloud**,
  ~10 GPUs for 300 cameras, ~₹27–52 lakh one-time + ~₹5–9 lakh/yr for a school.

## Memory notes (already saved)
`drvfs-db-slowness` (the /mnt/d SQLite trap + SENTINEL_DB_PATH fix), `wsl-venv-and-run`,
`sentinel-local-license`, `reid-osnet-and-features`.
