# Sentinel — Session Update: Live Analytics + Stability Fixes

**Date:** 2026-07-15
**Author:** working session (Haron + assistant)
**Status:** ✅ All fixes applied **locally, NOT committed**. Base = your friend's `96d78d4`.
**Scope:** fixed the recurring "failed to fetch across all tabs" crashes, built live-stream
analytics, and traced a big performance problem to the database's location.

---

## TL;DR

- The live camera view kept crashing the whole server. Found and fixed **five** distinct
  causes (three of them bugs in the shared codebase, two in the new live-analytics path).
- Built a **single-stream live-analytics runner** (`live_analytics.py`) — full pipeline
  (detection/tracking/ReID/zones/fall/running/violence + event & alert logging) on one live
  camera, thread-based so it's safe on a modest box.
- Found the real cause of the UI being sluggish / timing out: **the SQLite DB lives on
  `/mnt/d` (WSL drvfs), where every DB connection takes ~3 seconds.** Made the DB path
  configurable and moved it to native storage → **~1000× faster** (3.6s → 0.001–0.14s).
- After all fixes: with the camera **off** and analytics retrying, every tab responds in
  **< 0.8s** and nothing crashes.

---

## Problems found & fixed

### 1. 🔴 `cv2.VideoCapture.read` monkey-patch crash — *in `backend_runner.py` (friend's code)*
The pool workers did `camera_state.cap.read = fake_read`, but `read` is a read-only C
attribute → **every worker crash-loops** (`'cv2.VideoCapture' object attribute 'read' is
read-only`). This is why the background engine never delivered frames.
**Fix:** added a `FrameInjector` stand-in and swap the whole `cap` object instead of patching
the method. *(This bug is still in your upstream `backend_runner.py` — worth landing there.)*

### 2. 🔴 `UnboundLocalError: local variable 'time'` — *in `app.py` (friend's recent code)*
`_process_camera_frame` had a redundant `import time` **inside** the function (reconnect
branch), which made `time` a function-local, so `video_time = time.time()` a few lines later
crashed on **every successful frame**. This silently broke all live processing.
**Fix:** removed the redundant local `import time` (module already imports it at the top).
*(Also still in upstream `app.py`.)*

### 3. 🔴 RTSP decoded inside the web process → server crash
The live MJPEG/snapshot path opened and decoded the RTSP camera **in the main web process**,
with a loop written for files. A real camera hiccup could block a worker or segfault OpenCV
and take the **whole server down** → "failed to fetch" on every tab.
**Fix:** live cameras are **never** decoded in the web process now. The web paths serve the
frame cache or a placeholder; `live_analytics` is the *sole* RTSP decoder (isolated).

### 4. 🟠 Model loading blocked the event loop
Starting analytics loaded YOLO + OSNet inside the request thread (~30s, GIL-heavy) → the
server was unresponsive during the load ("failed to fetch").
**Fix:** models are loaded **once, shared**, and **pre-warmed at startup** so a start request
never reloads them.

### 5. 🟠 Opening an *offline* camera starved the server
When the camera was off, `cv2.VideoCapture(rtsp)` blocked ~8s holding the GIL, and the retry
loop did it repeatedly.
**Fix:** a fast **0.5s socket probe** before any real open — we only attempt the slow open
when the camera is actually reachable, with exponential backoff otherwise.

### 6. 🟠 Database on `/mnt/d` (drvfs) — the real cause of the sluggish UI
Measured: a single SQLite `connect + query + close` takes **2.2–3.6s on `/mnt/d`** vs
**0.001s on native ext4**. Your friend's "DB-persistent tokens" change makes **every
authenticated request** open the DB, so every `/api/*` call paid ~3–7s → browser fetch
timeouts.
**Fix:** made the DB path configurable via `SENTINEL_DB_PATH` and moved the DB to native
WSL storage. Result: `/api/*` went from **3–12s → < 0.8s**.

---

## Files changed (local, uncommitted)

| File | Change |
|---|---|
| `live_analytics.py` **(new)** | Single-stream live-analytics runner (thread-based, shared/prewarmed models, socket-probe + backoff, sole RTSP decoder, publishes annotated frames + logs events). |
| `backend_runner.py` | `FrameInjector` fix for the read-only `cap.read` crash (#1). |
| `app.py` | Removed redundant local `import time` (#2). |
| `camera_stream.py` | Live sources serve cache-or-placeholder only — no RTSP decode in the web process (#3). FFMPEG TCP+timeout hardening kept. |
| `backend/server.py` | `DISABLE_AI_ENGINE` env toggle; `live_analytics.prewarm()` at startup; new endpoints: `POST /api/cameras/{id}/analytics/start|stop`, `GET /api/cameras/analytics/status`. |
| `db_schema.py` | `DB_PATH` now honours `SENTINEL_DB_PATH` env var (#6). |

Nothing is committed. `git status` shows the six files above.

---

## How to run (this box / WSL)

```bash
wsl
cd /mnt/d/COLLEGE/Sentinel
# DISABLE_AI_ENGINE: skip the heavy pool engine (it saturates this 8-core box and,
#   until the fork issue is fixed upstream, delivers no frames here).
# SENTINEL_DB_PATH:  use fast native ext4 storage instead of slow /mnt/d.
DISABLE_AI_ENGINE=1 SENTINEL_DB_PATH=/root/sentinel_data/cctv_logs.db \
  ./.venv/bin/python -m uvicorn backend.server:app --host 0.0.0.0 --port 8000
# open http://localhost:8000  (login developer / dev@sentinel)
```

**Turn on live analytics for a camera** (once the camera is back online):
```bash
# start (fps optional, default 3)
curl -X POST "http://localhost:8000/api/cameras/1/analytics/start?fps=3" -H "Authorization: Bearer <token>"
# status / stop
curl     "http://localhost:8000/api/cameras/analytics/status" -H "Authorization: Bearer <token>"
curl -X POST "http://localhost:8000/api/cameras/1/analytics/stop"  -H "Authorization: Bearer <token>"
```
Annotated frames appear on the camera's live tile; events/alerts flow into Notifications,
AI Search and Reports.

> **Note on the DB move:** the working DB is now `/root/sentinel_data/cctv_logs.db` (a copy of
> the original, WAL-checkpointed). The original `/mnt/d/COLLEGE/Sentinel/cctv_logs.db` is
> untouched. Unset `SENTINEL_DB_PATH` to go back to the slow drvfs one.

---

## Diagnosis summary (camera OFF)

| Check | Result |
|---|---|
| Server crash on live camera | **Fixed** — no crash; live cameras never decoded in web process |
| Analytics start with camera off | `waiting-for-camera`, retries with backoff, **no crash** |
| All tabs responsive (worker retrying) | ✅ every `/api/*` **< 0.8s** |
| Load average | ~1.0 (healthy) |
| mp4 cameras (2–10) | still render via the safe file path |

---

## Outstanding — recommendations for your friend (upstream)

1. **Land the `FrameInjector` fix** — `cap.read = fake_read` still crash-loops every pool
   worker in `backend_runner.py`.
2. **Land the `time` fix** — the redundant `import time` in `_process_camera_frame`
   (`app.py`) breaks live processing on every frame.
3. **Background engine fork issue** — even with #1 fixed, forking the pool workers from the
   heavy, multithreaded web process is fragile and saturates a small box. Consider
   `multiprocessing.set_start_method("spawn")` **and** switching `db_lock` to a
   `Manager().Lock()` (raw locks aren't picklable for spawn), and/or gate camera count.
4. **DB location / auth cost** — the per-request `active_tokens` DB lookup is fine on native
   storage but catastrophic on `/mnt/d`. For dev, keep the DB on native ext4
   (`SENTINEL_DB_PATH`); for production, use Postgres (`DATABASE_URL`) or at least cache token
   validation in memory so auth doesn't hit the DB every request.
5. **Edge/analytics on a small box** — this 8-core WSL box can't run the full pool engine on
   all cameras. The per-camera `live_analytics` thread is the pragmatic pattern here; the pool
   engine needs a beefier / GPU machine (ties back to the hosting sizing).

---

## Environment reminders

- `.venv` is WSL Python; run via `./.venv/bin/python …`, open `http://localhost:8000`.
- License is `ADIVA_TRIAL` (20 cams, all features, `hardware_hash: ANY`, expires 2026-08-13),
  applied to `license.key` (gitignored).
- Adiva camera under test: `rtsp://admin:@192.168.1.20:554/h264/ch1/main/av_stream`
  (2880×1616 main stream; was switched off at end of session).
