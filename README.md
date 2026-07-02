# Sentinel AI — School CCTV Intelligence Engine

Sentinel AI turns raw camera feeds into a searchable, metadata-indexed event
database and adds real-time detection for safety-critical incidents. This
codebase is the **hybrid merge** of two sibling projects (this engine-focused
fork + the school-safety-features fork at dhxvxn/SIngle-Camera-Intelligence):
the stronger implementation of each subsystem was kept.

See `docs/FEATURE_ROADMAP.md` for how the 15 target school-safety features map
onto this codebase, and what still needs to be built.

## School-safety layer (merged from the SCI fork)

- **Zone alert rules** — zones can be `restricted` (alert on any entry) or given
  `active_hours` + `school_days`; a campus-wide holiday calendar
  (`school_calendar.py`) overrides both.
- **Principal notifications** (`alerts.py`) — every restricted-zone,
  after-hours, or fall alert stores a **snapshot photo** of the moment (red box
  around the subject) plus footage-jump metadata, and lands in an in-app
  notification inbox.
- **Hybrid fall detection** (`fall_detector.py`) — standing→horizontal
  transition + vertical-drop over a sliding window (verified logic) with EMA
  bbox smoothing and FPS-independent cooldowns; routed through the alerts
  pipeline (snapshot + notification), no extra ML model.
- **Headcount reporting** (`mode_manager.py`) — distinct-person counts (unique
  Global IDs, not raw events), filterable by day/week/month/all, per-zone
  breakdown.
- **Unified NL search** (`search_service.py`) — fall/intrusion queries route to
  the alerts table; everything else to the zone-session events table.

## Licensing & roles (productization layer)

- **Ed25519-signed licenses** (`license_validator.py`) — only the public
  verification key ships; the private signing key stays with the developers
  (`generate_key.py`, `dev_keys/` — never distribute). Licenses carry client id,
  camera limit, expiry date, hardware lock, and a **feature-tier list**.
  Expired key ⇒ zero cameras. No demo-password bypasses exist.
- **Three user roles** (`backend/auth.py` + dashboard):
  - `developer` — license/camera/feature governance, user management, full access
  - `tech` — zones, alert rules, school hours, holiday calendar, video ingest
  - `principal` — notifications inbox with snapshots, live alert feed,
    headcount reports, footage playback
- Default dashboard logins (change via `SENTINEL_*_PASSWORD` env vars):
  `developer` / `techteam` / `principal` with `dev@sentinel` / `tech@sentinel` /
  `principal@sentinel`.

### IP protection notes

Python cannot be made uncopyable; the deployed protection stack is:
Ed25519 licensing (above) + Docker-only distribution (`Dockerfile`) +
obfuscation (run PyArmor over the source before building the client image).
Never ship `dev_keys/`, `generate_key.py`, or the git history to a client.

---

## Core Pipeline (already built)

```
[ Camera Feed / RTSP ]
        |
        v
[ Object Detection ]        detector.py      (YOLOv8 / RT-DETR, GPU-accelerated)
        |
        v
[ Local Tracking ]          tracker.py       (ByteTrack / DeepSORT)
        |
        v
[ Neural Re-Identification ] reid.py         (ResNet50 embeddings, lazy stride)
        |
        v
[ Zone & Event Logic ]      zone_manager.py, zone_logic.py, event.py
        |
        v
[ Metadata DB + API ]       db.py, db_schema.py, backend/server.py (FastAPI)
        |
        v
[ Natural Language Search ] intent_manager.py, llm_parser.py, query_engine.py
```

* **Detection**: YOLOv8 (edge/CPU) or RT-DETR (higher accuracy), CUDA-ready.
* **Tracking**: ByteTrack for fast local ID association.
* **Re-ID**: ResNet50 appearance embeddings, refreshed every 15 frames per
  track to cut inference cost, plus shirt-color matching as a soft signal.
* **Zones**: Polygon-based zone definitions (`zones.json`) for
  entry/exit/intrusion logic — this is the same primitive that intrusion
  detection, loitering detection, and restricted-area alerts will build on.
* **Storage & retrieval**: SQLite metadata index, queried through a natural
  language console (`search_console.py`) or REST/WebSocket API
  (`backend/server.py`).

## What's Not Built Yet

The features on the school requirements list (dress code compliance, weapon
detection, fight/violence detection, fall detection, fire/smoke detection,
etc.) each need their own detection model or classifier layered on top of
this pipeline — see `docs/FEATURE_ROADMAP.md` for a per-feature plan.

The pipeline also currently assumes one video source per process. Scaling to
a real ~10,000-camera school deployment (distributed ingestion, edge
inference, message queues, horizontal scaling) is a separate architecture
phase, deliberately out of scope for now per `.agents/AGENTS.md`.

---

## Quick Start

### 1. Prerequisites
Python 3.10+.

### 2. Installation

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Mac/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Running

**Live surveillance mode** (process a video file or RTSP stream):
```bash
python app.py
```
Controls: `SPACE` pause/play, `LEFT/RIGHT` seek, `Q`/`ESC` quit.

**Natural language search console**:
```bash
python search_console.py
```

**Pipeline benchmark** (measure decode/detect/track/ReID latency and cache
hit ratio — useful for sizing hardware before scaling to many cameras):
```bash
python benchmark_pipeline.py
```

**FastAPI backend** (REST + WebSocket alerts):
```bash
python -m uvicorn backend.server:app --host 127.0.0.1 --port 8000
```
Docs at `http://127.0.0.1:8000/docs`; alert stream at `ws://127.0.0.1:8000/ws/alerts`.

### 4. Testing

```bash
python test_reid.py
```

---

## Project Docs

Technical notes on individual subsystems live under `docs/`:
`SYSTEM_DESIGN.md`, `GLOBAL_ID.md`, `ZONE_MANAGER.md`, `ZONE_FORMAT.md`,
`EVENT_DETECTOR.md`, `EVENTS_SCHEMA.md`, `QUERY_SYSTEM.md`, `COLOR_MATCHING.md`,
`VIDEO_PLAYER.md`, `APP_FLOW.md`, `BUG_FIXES.md`, and the new
`FEATURE_ROADMAP.md`.
