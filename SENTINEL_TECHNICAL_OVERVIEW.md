# Sentinel AI — Technical Overview

**Purpose:** a complete technical description of the Sentinel AI campus surveillance
platform — how it works, what it's built on, how it deploys, what it costs to run,
and its current limitations — for review by a technical lead.

**Prepared:** 2026-07-15 · **Base commit:** `96d78d4` (+ local uncommitted fixes, noted inline)
**Reviewer note:** Sections 11–12 (Known Issues / Tech Debt) are deliberately candid — read them.

---

## 1. What it is

Sentinel is a real-time video-analytics platform for school/campus CCTV. It ingests
camera streams, runs a computer-vision pipeline (person detection → tracking →
cross-camera re-identification → behavioural analytics), logs events and raises
alerts, and serves everything through a role-based web dashboard with natural-language
search, clip playback, notifications, audit logging, and licensing.

It runs **CPU-only today** (no GPU on the dev box); the same code paths support GPU,
NPU (ONNX/OpenVINO), and Edge-TPU (Coral) via a pluggable inference layer.

---

## 2. Technology stack (what it uses)

| Layer | Technology |
|---|---|
| Language / runtime | Python 3.13 (WSL Ubuntu on dev; Linux in prod) |
| Web framework | **FastAPI** + Uvicorn (ASGI), WebSockets for live alerts |
| Object detection | **YOLOv8n** (Ultralytics); RT-DETR optionally supported |
| Multi-object tracking | **ByteTrack** via Ultralytics / `supervision` |
| Person Re-ID | **OSNet (`osnet_x1_0`)** via `torchreid` — 512-D embeddings |
| Behavioural detectors | Custom heuristics (fall, running, violence, loitering, dress-code) |
| Zones / lines | `supervision` PolygonZone + custom line-crossing counters |
| DL runtime | **PyTorch 2.x (CPU build)**; ONNX Runtime / TFLite for edge |
| Database | **SQLite** (default) or **PostgreSQL** (via `DATABASE_URL`) |
| Frontend | Single-file HTML/CSS/JS dashboard (no framework), MJPEG `<img>` streams |
| Licensing crypto | **Ed25519** signatures (`cryptography`) |
| Video I/O | OpenCV (FFmpeg backend) for RTSP/file decode |
| Packaging | `requirements.txt`; container-ready |

**Key models & sizes:** YOLOv8n ≈ 3.2M params; OSNet_x1_0 ≈ 2.2M params / ~0.98 GFLOPs.
Both are deliberately small for CPU/edge budgets.

---

## 3. Architecture — how it works

### 3.1 The core per-frame pipeline (`app.py::_process_camera_frame`)

For each processed frame of a camera:

```
frame ─► YOLO detect (person/vehicle) ─► ByteTrack (per-camera track IDs)
      ─► OSNet Re-ID embedding per person ─► GlobalIdentityManager
                                              (assigns persistent Global ID across cameras)
      ─► zone tests (point-in-polygon)  ─► line-crossing counters
      ─► behaviour checks: fall, running, violence, loitering, dress-code
      ─► event/session logging (events + tracking_data tables)
      ─► alerts (restricted zone / after-hours / fall / violence …) + snapshot
      ─► annotated display_frame
```

- **Detection** (`detector.py`): YOLO returns boxes for target COCO classes
  (person, bicycle, car, motorcycle, bus, truck), confidence-filtered.
- **Tracking** (`tracker.py`): ByteTrack gives stable per-camera track IDs.
- **Re-ID** (`reid.py`): OSNet produces a 512-D appearance embedding per person crop;
  `GlobalIdentityManager` matches it (cosine similarity + colour + spatial/time gating)
  to assign a **persistent Global ID (GID)** that follows a person across cameras.
- **Events/sessions** (`event.py`): entries/exits per zone become "sessions" (dwell
  time, entry/exit). Written to SQLite/Postgres.
- **Alerts** (`alerts.py`): rule hits → alert row + red-box **snapshot** + a
  principal **notification** + optional WebSocket broadcast.

### 3.2 Two ways the pipeline is driven on live cameras

There are **two** live-processing designs in the codebase (important for the reviewer):

**(A) Pool engine — `backend_runner.py`** (original, by the team)
Multiprocessing worker pools; each pool owns several cameras, runs reader threads +
the pipeline, writes JPEG frames to a shared `multiprocessing.Manager` dict.
Intended for scale (N cameras across a few processes).
⚠️ **Currently broken on the dev box** — see §11.

**(B) Per-camera live analytics — `live_analytics.py`** (added this session, thread-based)
One RTSP decode per camera, **fanned out into two decoupled threads**:
- **Reader/display thread** — publishes the **raw** frame (LIVE banner only, no
  inference) to the frame cache at the camera's full rate (~20 fps). This is what the
  live tile shows → **smooth, real-time, nothing processed on it.**
- **Analytics thread** — pulls the *latest* frame (drops stale ones), runs the full
  pipeline at ~3 fps, logs events, raises alerts. **Never gates the display.**

This is the design now wired to the dashboard's **Video Analytics → "Run live
analytics"** control. Measured on the dev box: display ~21 fps, analytics ~3 fps, both
running together, all API endpoints < 0.8 s, one camera ≈ 1.5 CPU cores.

### 3.3 Inference backend abstraction (`inference_config.py`)

A single source of truth resolves which backend runs, from `deployment.json` or env
vars (env > json > default):
- Detector weights: `.pt` (GPU/CPU) / `.onnx` (NPU) / `_edgetpu.tflite` (Coral).
- ReID backend: OSNet (Torch), or its **ONNX/TFLite export** for NPU/Edge-TPU.
- Backend-aware Re-ID match thresholds (quantized models separate identities less
  cleanly, so they use a lower cosine bar).

`export_models.py` exports YOLO + **OSNet** (not a generic ResNet — verified the
ONNX export matches the Torch embedding at **0.9997 cosine**) to ONNX/Edge-TPU.

### 3.4 Web/serving layer (`backend/server.py`)

FastAPI app (~40+ routes): auth/login (token-based, DB-persisted), camera list/stream/
snapshot, live-analytics start/stop/status, zones/lines CRUD, NL query, clip playback
(byte-range seekable), notifications, reports, audit log, licensing, cross-camera report.
MJPEG live tiles are served as `multipart/x-mixed-replace` to plain `<img>` tags.

---

## 4. Feature list (with how each works)

### Detection, tracking, identity
- **Object detection** — YOLOv8n per frame (person + vehicles).
- **Multi-object tracking** — ByteTrack, stable per-camera track IDs.
- **Cross-camera Re-ID** — OSNet 512-D embeddings + `GlobalIdentityManager`
  (cosine + colour + time/space gating) → persistent Global IDs across cameras.

### Behavioural analytics (heuristics on the tracked boxes — no extra ML model)
- **Fall detection** — upright→horizontal aspect-ratio transition + centroid drop,
  EMA-smoothed, per-identity cooldown.
- **Running detection** — centroid speed normalised by bbox height (calibration-free).
- **Fight/violence** — two people close + both high motion-energy for N frames.
- **Loitering** — dwell-time threshold per zone.
- **Dress-code / uniform** — shirt-region colour vs allowed-uniform colours.
- **Per-person risk score** — aggregates behaviours into a running risk level.

### Zones, lines, occupancy
- **Restricted-zone intrusion** — point-in-polygon on a zone flagged `restricted`.
- **After-hours / non-school-day entry** — zone `active_hours` + holiday calendar.
- **Line-crossing counters** — directional tripwire entry/exit counts.
- **Crowd density / occupancy** — peak simultaneous people, per-zone counts.

### Search, playback, reporting
- **Natural-language search** — "show falls", "intrusions on camera 1", GID lookups;
  handles plurals, objects, cameras, zones, time windows.
- **One-click clip playback** — byte-range-seekable video with "why logged" metadata
  and a subject-highlight overlay (boxes only the logged person).
- **Spatial heatmap** — where people dwell.
- **Automated reports** — daily/monthly/yearly: headcount, density, dwell, alert
  breakdown, busiest hour.

### Live view + live analytics (this session)
- **Smooth live feed** — raw MJPEG at full camera rate, no processing on it.
- **"Run live analytics" toggle** (Video Analytics tab) — starts backend processing on
  the live stream; events log to DB, **alerts flow to the Notifications column**; live
  status (connecting / running + frame counts). Display and analysis are fully decoupled.

### Governance & platform
- **Role-based access** — developer / tech / principal (developer is superuser).
- **Notifications inbox** — unread/read, snapshot + jump-to-footage, WebSocket push.
- **Append-only audit log** — who viewed what, when, role, IP.
- **Licensing** — see §5.
- **Dual database** — SQLite or PostgreSQL, same code.

---

## 5. Licensing & security model

- **Ed25519-signed license keys.** Only the public key ships in the binary; the private
  signing key stays with the vendor. A client can *verify* but cannot *forge* a license.
- License payload: `client_id`, `max_cameras`, `features[]`, `hardware_hash` (single or
  comma-separated MACs, or `ANY`), `expiration`.
- **Enforced (verified live this session):**
  - **Camera cap** — registering more cameras than licensed → HTTP 403.
  - **Per-feature gating** — endpoints call `_require_feature(...)`; features outside the
    tier → 403.
  - **Immediate effect** — the license file is re-read per request (no restart needed).
  - **Multi-machine hardware lock** — comma-separated fingerprints; expiry; `ANY` opt-out.

**Security caveats the reviewer should know (see §11 for detail):**
- Enforcement is **client-side / honor-based** — it runs inside the shipped software.
  Ed25519 stops *forging* a license, not *removing the check* from readable Python.
  Production needs compiled/obfuscated builds and/or **online activation + heartbeat**.
- Expiry trusts the local clock (clock-rollback bypass) → online time check fixes it.
- Auth tokens are DB-persisted; every authenticated request hits the DB (perf note §11).

---

## 6. Data model & storage

- **Tables:** `events`, `tracking_data`, `alerts`, `notifications`, `users`,
  `active_tokens`, `audit_log`, `line_crossings`, zones/lines in JSON config files.
- **SQLite** default (single file `cctv_logs.db`, WAL mode) or **PostgreSQL** via
  `DATABASE_URL`. Adapter (`db_schema.py`) rewrites `?`↔`%s` and types transparently.
- **Config as files:** `cameras.json` (camera registry), `zones.json`, `lines.json`,
  `deployment.json` (optional, edge tier), `license.key` (gitignored).
- **Media:** alert snapshots on disk; raw video on-site NVRs (not centralised).
- **Retention:** background loop prunes events/tracking + raw MP4s older than 30 days,
  plus a disk-space watchdog (emergency purge < 10% free).

---

## 7. API surface (selected)

```
POST /api/login                              → token
GET  /api/cameras                            → registry
GET  /api/cameras/{id}/stream                → MJPEG live tile (smooth raw)
GET  /api/cameras/{id}/snapshot              → single JPEG
POST /api/cameras/{id}/analytics/start|stop  → live analytics (this session)
GET  /api/cameras/analytics/status           → per-camera worker status
POST /api/query                              → NL search
GET  /api/clip/video (Range)                 → seekable clip
GET  /api/notifications  ·  POST mark-read
GET  /api/reports/periodic?period=…
GET  /api/audit  ·  /api/cross-camera  ·  /api/zones/flow
GET/POST/DELETE /api/zones  ·  /api/lines
GET  /api/license  ·  POST /api/license/generate|apply
```

---

## 8. Deployment plans

### 8.1 The deciding constraint: bandwidth
10,000 × 1080p H.264 ≈ **~30 Gbps** continuous. You **cannot** ship that to a central
cloud economically. So the reference architecture is **edge inference + cloud control
plane**: analyse video where it's captured; only events/metadata/clips traverse the WAN.

### 8.2 Recommended architecture (hybrid edge + cloud)
- **At each site:** GPU/NPU edge servers run the pipeline locally. Raw video stays on
  on-site NVRs. The per-camera `live_analytics` pattern (or a fixed pool engine) runs here.
- **Central cloud:** dashboard, licensing/activation, metadata DB (Postgres), clip
  storage, cross-site search, reporting.

### 8.3 Edge inference options (per site)
| Tier | Hardware | Notes |
|---|---|---|
| GPU | NVIDIA T4/L4/A10 + TensorRT/DeepStream | ~15–30 cams/GPU (full stack, ~5–10 fps) |
| NPU | Intel (OpenVINO) via ONNX export | mid-range, cheaper |
| Edge-TPU | Google Coral (~₹6k) via `_edgetpu.tflite` | budget; detection offload; ReID needs INT8 TFLite |
| CPU | existing PCs | forensic/low-fps only; not for many live cams |

### 8.4 Sizing (throughput, full stack: YOLO + OSNet + heuristics)
- **~15–30 cameras per modern GPU** at surveillance fps (5–10), optimised.
- **10,000 cameras ≈ ~500 GPUs.** **300 cameras ≈ ~10 GPUs** (typical), 6–20 depending
  on fps / whether Re-ID is on / resolution.
- Levers that change the count: frame rate, turning Re-ID off, inference resolution,
  batching across cameras.

### 8.5 Deployment tiers (product packaging)
- **Budget / forensic** — mostly on-demand + motion-triggered + a few live cameras;
  reuse existing PCs / Coral sticks. (Trades continuous coverage for cost.)
- **Standard live** — all cameras live at low fps, full features, on-prem GPU boxes.
- **Enterprise** — 10k-scale edge + cloud, GPU fleet.

---

## 9. Cost of running (planning-grade, INR, FX ₹85/US$)

> Ranges are ±30–40%; they move with fps, resolution, retention, GPU pricing, and FX.

### For a **300-camera** site (representative)
| Item | Cost |
|---|---|
| AI compute (≈10 GPUs, edge) — capex | ₹25–40 lakh |
| Software licence (per site) | ₹7.5–19.5 lakh (₹2.5k–6.5k/camera) |
| Integration / install | ₹3–5 lakh |
| **One-time total (AI, existing cameras)** | **~₹27–52 lakh** |
| Recurring: power + cloud control plane + storage + AMC | **~₹5–9 lakh/yr** |
| Electricity (≈3–5 kW, 24×7) | ~₹2–3 lakh/yr |

### For **10,000 cameras** (edge + cloud, recommended)
| Item | Cost |
|---|---|
| Edge GPU fleet (~500 GPUs across sites) — capex | ₹21–43 crore |
| Central cloud control plane | ₹2–15 lakh/month |
| Steady-state maintenance (power/ops/storage/AMC) | ~₹40–90 lakh/month |
| 5-year TCO (edge+cloud) | ~₹55–95 crore |

Renting 500 cloud GPUs instead is ~₹1.3–3.5 crore/month (~₹110–200 crore over 5 yr) —
**owning edge hardware is far cheaper at scale.**

### One-time productisation (all tiers)
Hardening the prototype into a fleet product (multi-GPU optimised pipeline, DB-backed
camera registry, online licensing/activation): a 6–10-engineer team for 9–12 months,
**~₹1.5–3 crore**; a pilot site ~₹15–30 lakh.

---

## 10. Environment & how to run (dev)

```bash
# WSL, CPU-only dev box. DB on native ext4 (drvfs is ~1000x slower — see §11).
DISABLE_AI_ENGINE=1 SENTINEL_DB_PATH=/root/sentinel_data/cctv_logs.db \
  ./.venv/bin/python -m uvicorn backend.server:app --host 0.0.0.0 --port 8000
# http://localhost:8000  ·  logins: developer/dev@sentinel, tech, principal
```
Then Video Analytics → pick a camera → **Run live analytics** (smooth feed + background
events → Notifications).

---

## 11. Known issues / technical debt (read this)

These are the honest gaps a technical lead will care about. Several have **local,
uncommitted fixes** from this session (flagged); they should be reviewed and landed
upstream.

1. **Pool engine is unreliable (`backend_runner.py`).**
   - It monkey-patched `cv2.VideoCapture.read` (a read-only C attribute) → **every
     worker crash-loops.** *Local fix:* `FrameInjector` swaps the whole `cap` object.
   - Even fixed, it **forks worker pools from the heavy, multithreaded web process**,
     which is fork-unsafe → workers hang/deadlock and saturate CPU on a small box.
     *Recommendation:* `spawn` start method + a `Manager().Lock()` (raw locks aren't
     picklable for spawn), or run workers as independent processes started before torch
     loads. **Until fixed, use `DISABLE_AI_ENGINE=1` + the per-camera `live_analytics`.**
2. **`UnboundLocalError: 'time'` in `app.py::_process_camera_frame`** — a redundant
   local `import time` shadowed the module import, breaking live processing on every
   frame. *Local fix applied.* **Still present upstream.**
3. **RTSP decoded in the web process could crash the server.** *Fixed:* live cameras are
   never decoded in the web process; `live_analytics` is the sole decoder, and the web
   paths serve cache-or-placeholder. FFmpeg TCP + read-timeout hardening added.
4. **Database on `/mnt/d` (WSL drvfs) ≈ 3 s per connection** vs ~0.001 s on native
   ext4 (≈1000×). With per-request DB token lookups, every `/api/*` call paid ~3–7 s.
   *Fix:* `SENTINEL_DB_PATH` env override + DB moved to native storage → `/api/*` < 0.8 s.
   **Production: use PostgreSQL** (or at least cache token validation in memory).
5. **Model-load starvation** — loading YOLO+OSNet in a request thread froze the event
   loop (~30 s). *Fix:* shared models, **pre-warmed at startup**.
6. **Licensing is honor-based / client-side** (readable Python). Needs compiled/
   obfuscated builds + **online activation + heartbeat**; expiry trusts local clock.
   Also: the current signing key is a **locally-generated dev key** — a production
   keypair (private key offline/HSM) must replace it before any sale.
7. **Prototype-scale plumbing** — cameras/zones/lines are flat JSON; single-node;
   heuristic (not trained) behaviour detectors. Fine for pilots, not for 10k live.
8. **CPU-only dev box** — the full pool engine on all cameras saturates 8 cores; the
   per-camera `live_analytics` handles ~1 camera comfortably. Real scale needs GPUs.

---

## 12. Recommended next steps (for discussion)

1. **Land the local fixes** (FrameInjector, the `time` bug, live_analytics, DB path,
   web-process RTSP hardening) — they're not committed yet.
2. **Fix or retire the pool engine** — either make it spawn-safe, or standardise on the
   per-camera `live_analytics` thread pattern (which works today) and scale it out.
3. **Move to PostgreSQL** for production; keep SQLite for single-site/dev.
4. **Harden licensing** — production keypair + online activation before any sale.
5. **GPU/edge productisation** — TensorRT/DeepStream pipeline, batched inference,
   DB-backed camera registry; validate the OSNet ONNX/Edge-TPU exports on real silicon.
6. **Consider trained models** to replace the heuristic fall/run/violence detectors for
   accuracy, once GPUs are available.

---

*Companion docs in this repo:* `SENTINEL_FEATURES.md` (feature narrative),
`Sentinel_Hosting_Proposal.docx` (costed hosting proposal),
`EDGE_DEPLOYMENT.md` (edge/NPU details),
`UPDATE_2026-07-15_stability-and-live-analytics.md` (this session's fixes).
