# Sentinel — What Changed + How to Run It on Your GPU Laptop

**For:** the teammate with the GPU laptop
**Goal of these changes:** cut GPU usage so the platform can run **300 cameras for a
school** affordably — and let you **measure the difference on real GPU hardware.**

---

## 1. What we changed (plain English)

We added **two GPU-saving optimizations** and finished the **live-analytics** design,
on top of some stability fixes. Nothing here changes what the product *does* — it makes
it run cheaper.

### A. Adaptive-rate gate  (`live_analytics.py`, `app.py`)
The analytics pipeline used to run at a fixed rate on every camera. Now it's **adaptive**:
- Runs at **3 fps when a person is around**, drops to **1 fps when the camera is empty**,
  with an **8-second hangover** (stays fast for 8 s after the last person).
- It keys off **real YOLO person detections** — so a person who **falls and lies still is
  still detected**, and the pipeline keeps running on them (no motion-sensor blind spot).
- **Measured:** ~**36% fewer pipeline runs** on quiet/intermittent cameras (schools have
  lots of these — hallways during class, night perimeter). ~0% on always-busy cameras
  (correctly — you don't skip when people are present).

### B. Deferred OSNet Re-ID  (`reid.py`, `inference_config.py`, `reid_search.py`)
OSNet (the cross-camera "who is this person" model) is the **heaviest continuous GPU
cost** — it runs a whole CNN per person. New toggle **`REID_DEFERRED=1`**:
- **Live:** OSNet is **not run**. A cheap colour/edge fingerprint keeps per-camera
  tracking; representative person crops are saved to `reid_crops/`.
- **Search time only:** full OSNet cross-camera matching runs **on demand** via
  `reid_search.search(gid)` — a rare, bounded operation instead of 24/7.
- **Measured:** one OSNet Re-ID = **39 ms**, deferred = **0.2 ms** (~200× cheaper per op).
  System-level on our CPU box it was ~14% (limited because there's already an embedding
  cache), **but on a GPU it's a bigger share of GPU work** — which is exactly what your
  laptop will let us measure.
- **Trade-off:** live cross-camera Global-IDs become unreliable in this mode (which is why
  we defer to search time). **Per-camera events — fall, loitering, intrusion, running —
  stay fully live.** Cross-camera "where did they go?" becomes a fast forensic search.
  Good fit for a school.

### C. Live-analytics UI + decoupled feed  (`backend/index.html`, `camera_stream.py`)
- **Video Analytics → "Run live analytics"** button: starts backend analytics on a live
  camera. **The live feed stays raw & smooth (~20 fps)**; the heavy AI runs separately
  (~3 fps) and logs events → **alerts appear in the Notifications tab.**
- Status bar shows connecting / running + frame counts.

### D. Stability fixes (needed to run at all)
- `backend_runner.py`: fixed a crash — it patched `cv2.VideoCapture.read` (read-only) →
  workers crash-looped. Replaced with a `FrameInjector`.
- `app.py`: fixed an `UnboundLocalError: time` that broke live processing every frame.
- `camera_stream.py`: the web process no longer decodes RTSP itself (it crashed the
  server); live cameras stream cache-or-placeholder.
- `backend/server.py`: `DISABLE_AI_ENGINE` toggle, model pre-warm at startup, analytics
  endpoints.
- `db_schema.py`: `SENTINEL_DB_PATH` — SQLite on WSL `/mnt/d` (drvfs) is ~3 s **per DB
  connection**; point it at native storage → ~1000× faster.

### Measured summary
| Optimization | Result |
|---|---|
| Adaptive gate | ~36% fewer pipeline runs on quiet cameras (hardware-independent) |
| Deferred Re-ID | 39 ms → 0.2 ms per Re-ID; bigger GPU share on a real GPU |
| Combined (estimate, school camera) | ~30–50% less continuous GPU → ~30–50% more cameras per GPU |

---

## 2. Run it on your GPU laptop

The repo's `.venv` is a **CPU-only** PyTorch. To use your GPU, make a fresh venv with a
**CUDA** PyTorch. The code auto-detects the GPU (`detector.py` picks `cuda` if available).

### Step 1 — clone + Python
```bash
git clone https://github.com/haronnk/SENTINEL-2.0.git
cd SENTINEL-2.0
python -m venv .venv-gpu
# Windows:  .venv-gpu\Scripts\activate      Linux/WSL:  source .venv-gpu/bin/activate
python -m pip install -U pip
```

### Step 2 — install CUDA PyTorch (pick your CUDA version)
```bash
# check your CUDA with:  nvidia-smi   (top-right shows CUDA version)
# CUDA 12.1 example:
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
# then the rest (skip the CPU torch pin in requirements.txt if it conflicts):
pip install ultralytics supervision opencv-python fastapi uvicorn[standard] \
            cryptography torchreid onnxruntime numpy
```
Confirm the GPU is visible:
```bash
python -c "import torch; print('CUDA:', torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

### Step 3 — license + run
Get `license.key` from Haron (it's gitignored — not in the repo) and drop it in the folder.
```bash
# DISABLE_AI_ENGINE=1 skips the old pool engine (use per-camera live_analytics instead).
# On a native (non-/mnt/d) machine you don't need SENTINEL_DB_PATH.
DISABLE_AI_ENGINE=1 python -m uvicorn backend.server:app --host 0.0.0.0 --port 8000
# open http://localhost:8000   (login developer / dev@sentinel)
```
Startup log should say `Using PyTorch on CUDA` — that confirms the GPU is in use.
Then: **Video Analytics → pick a camera → Run live analytics.**

---

## 3. Measure the resource difference (the whole point)

Use the included **`gpu_benchmark.py`**. It runs the full pipeline over a video and prints
**fps + cameras-per-machine**, honouring `REID_DEFERRED`. Run it twice and compare, while
watching the GPU.

### Terminal 1 — watch the GPU
```bash
nvidia-smi -l 1      # updates every second: GPU %, memory, power
```

### Terminal 2 — run both modes
```bash
# a) full OSNet Re-ID live (heavier)
REID_DEFERRED=0 python gpu_benchmark.py path/to/test_video.mp4 200

# b) deferred Re-ID (OSNet skipped live)
REID_DEFERRED=1 python gpu_benchmark.py path/to/test_video.mp4 200
```
Compare the two: **ms/frame, fps, and "cameras per machine"**, and note the GPU %
difference in `nvidia-smi`.

### What to look for
- **fps / cameras-per-machine goes up** with `REID_DEFERRED=1` → that's the Re-ID saving.
- **GPU utilization %** in `nvidia-smi` is lower with deferred Re-ID.
- The **adaptive gate** saving is separate (it reduces *how many frames* are processed on
  quiet cameras) — you'll see it live: run analytics on a camera, watch the status bar's
  `active_frames` vs `idle_frames` climb, and GPU load drop when the scene is empty.

### For a real GPU-vs-CPU comparison
Run `gpu_benchmark.py` once in the **CPU venv** and once in the **GPU venv** on the same
video — the fps jump shows how many more cameras the GPU handles.

> Note: a GPU **laptop** (RTX 3050/4060-class) is great for proving the numbers and a
> small pilot (~5–15 cameras). A 300-camera school still needs proper GPU servers — but
> the laptop gives us real, honest numbers to show the technical lead.

---

## 4. Config flags cheat-sheet

| Env var | Effect |
|---|---|
| `REID_DEFERRED=1` | skip OSNet live; cross-camera Re-ID becomes on-demand search |
| `DISABLE_AI_ENGINE=1` | skip the old multiprocessing pool engine (use per-camera live_analytics) |
| `SENTINEL_DB_PATH=/path/db` | move SQLite off slow storage (WSL `/mnt/d`); native/Postgres in prod |
| `DETECTOR_WEIGHTS`, `REID_MODEL_ONNX`, `REID_MODEL_TFLITE` | edge/NPU/Coral model paths |

Adaptive-gate tuning lives in `live_analytics.py`: `DEFAULT_ANALYTICS_FPS` (fast rate),
`IDLE_ANALYTICS_FPS` (empty rate), `GATE_HANGOVER_S` (how long to stay fast after a person).

*Companion docs:* `SENTINEL_TECHNICAL_OVERVIEW.md`, `SENTINEL_DEPLOYMENT_GUIDE.md`,
`UPDATE_2026-07-15_stability-and-live-analytics.md`.
