# Sentinel AI — Feature Audit, Capability Report & Future Roadmap

**Prepared for:** Project Manager
**Project:** Sentinel AI — School Campus Video Analytics Platform
**Date:** 2026-07-11
**Environment:** CPU-only (no GPU), Python 3.13, FastAPI dashboard, SQLite/PostgreSQL back end

---

## 1. Executive Summary

Sentinel is a single-/multi-camera video analytics platform for school campuses. It runs
person **detection**, **tracking**, and **cross-camera re-identification** as its core, then
layers safety, security, and behavioural analytics on top, all served through a licensed
web dashboard with natural-language search, clip playback, notifications, audit logging and
periodic reports.

Against the **15 candidate features** you supplied, the current build already ships **8 fully**,
**3 partially** (a usable version exists but is heuristic or incomplete), and **4 are not yet
built**. No new hardware or GPU is required for anything shipped so far — every current
detector reuses motion/geometry from the existing pipeline rather than a heavy dedicated model.

### Coverage at a glance

| # | Requested Feature | Status | Where it lives |
|---|---|---|---|
| 1 | Dress Code Compliance | 🟢 **Implemented** | `event.py` → `check_dress_code` |
| 2 | Unauthorized Person Detection | 🟡 **Partial** | `alerts.py` (restricted + after-hours entry) |
| 3 | Attendance Automation | 🟡 **Partial** | headcount + `line_counter.py` |
| 4 | Fight and Violence Detection | 🟢 **Implemented** | `anomaly_detector.py` |
| 5 | Bullying Detection | 🔴 **Not built** | — |
| 6 | Fall Detection | 🟢 **Implemented** | `fall_detector.py` |
| 7 | Intrusion Detection | 🟢 **Implemented** | `alerts.py` + `zones.json` |
| 8 | Weapon Detection | 🔴 **Not built** | — |
| 9 | Crowd Density Monitoring | 🟢 **Implemented** | `periodic_report.py`, `event.py` occupancy |
| 10 | Loitering Detection | 🟢 **Implemented** | `incident_manager.py` |
| 11 | Running Detection | 🟢 **Implemented** | `running.py` |
| 12 | Smoke and Fire Detection | 🔴 **Not built** | — |
| 13 | Object Left Behind Detection | 🔴 **Not built** | — |
| 14 | Student Tracking (Privacy Controlled) | 🟢 **Implemented** | `reid.py` + licensing/audit/roles |
| 15 | Slip and Hazard Detection | 🟡 **Partial** | shares `fall_detector.py` logic |

**Score: 8 done · 3 partial · 4 to build.**

---

## 2. Feature-by-Feature Audit

### 🟢 Implemented

**1. Dress Code Compliance**
`check_dress_code()` samples the shirt-region colour of each tracked person and compares it
against a set of allowed uniform colours (`color_similarity`). Low similarity → a
`dress_code_violation` event is logged and the person is labelled **"UNIFORM VIOLATION"** on
the annotated video. Searchable via natural language ("dress code", "uniform", "violations").
*Caveat:* it is a **colour heuristic**, not a garment/logo classifier — good for a single
solid-colour uniform policy, weaker for patterned or multi-item dress codes.

**4. Fight and Violence Detection**
`anomaly_detector.check_anomaly()` flags a possible altercation when two tracked people are
**very close together AND both moving with high, agitated speed** for several consecutive
frames (proximity + motion-energy heuristic, with cooldown). Fires `violence_detected`,
raises an alert with snapshot and principal notification. *Caveat:* deliberately conservative
heuristic (explainable, CPU-cheap), not a trained action-recognition model.

**6. Fall Detection**
`fall_detector.check_fall()` detects the **standing → horizontal transition** plus a centroid
drop of ≥0.5 body-heights within a sliding window, with EMA smoothing and per-identity
cooldown. Fires `fall_detected`, snapshot + notification, and a "FALL DETECTED" on-screen
label. Covered by unit tests (`test_fall.py`, 5/5).

**7. Intrusion Detection**
Two mechanisms in `alerts.py`: **restricted-zone entry** (any person entering a zone marked
`restricted: true`) and **after-hours / non-school-day entry** (entry outside a zone's
`active_hours`, on weekends, or on calendar holidays via `school_calendar`). Each fires an
alert with a red-box snapshot, deep-link back to the exact footage, and a principal
notification.

**9. Crowd Density Monitoring**
`periodic_report.py` reports **peak simultaneous people** (density) and per-zone occupancy
breaches; `event.py` has a full occupancy-limit engine (`check_occupancy_alerts`, per-zone
`max_occupancy`). *Note:* live occupancy **alerting** was intentionally disabled (crowding is
normal in schools) — density and headcount are still measured and reported.

**10. Loitering Detection**
`incident_manager.py` tracks dwell time per person per zone; exceeding `LOITERING_THRESHOLD`
(15 s, tunable) marks the person as **Loitering** and records an incident. Surfaced in periodic
reports (dwell/loitering stats) and searchable.

**11. Running Detection**
`running.py` measures centroid displacement normalised by bounding-box height
("bbox-heights per second"), so it scales with distance from camera without calibration. EMA
smoothed, edge-triggered with cooldown. Fires `running_detected` and a "RUNNING" label.

**14. Student Tracking (Privacy Controlled)**
The strongest match. Cross-camera tracking via `reid.py` (OSNet / torchreid person-ReID) with
**anonymised Global IDs (GID N)** — no names, faces-as-identity, or PII stored. Privacy
controls layered on top: **license gating**, **role-based dashboard access**
(developer / tech / principal), and an **append-only audit log** (`audit_log.py`) recording
who viewed which footage and when. This is tracking by design-anonymous identity, which is the
privacy-controlled posture schools ask for.

### 🟡 Partial

**2. Unauthorized Person Detection**
*What exists:* restricted-zone and after-hours entry alerts effectively catch "someone where
they shouldn't be, when they shouldn't be there." *Gap:* there is **no allow-list of known/
authorised people** — the system cannot say "this specific person is not a registered
student/staff." True unauthorised-person detection needs a face/badge recognition or an
enrolled-identity roster, which is not built (and carries privacy trade-offs).

**3. Attendance Automation**
*What exists:* unique-people **headcount**, per-zone headcount, and directional **line-crossing
entry/exit counters** (`line_counter.py`, turnstile-style). *Gap:* counts are **anonymous** —
there is no mapping from a detection to a **named student on a roster**, so it produces
"how many" not "who is present." Real attendance needs identity enrolment (face recognition or
RFID/badge integration).

**15. Slip and Hazard Detection**
*What exists:* the fall detector's motion/geometry logic already captures sudden drops, and the
NL search maps "slip" and "hazard" to the fall pipeline. *Gap:* no **dedicated hazard
detection** — e.g. wet-floor/spill recognition, or a person's slip distinguished from a fall.
Currently a slip is reported as a fall.

### 🔴 Not Yet Built

**5. Bullying Detection** — No dedicated module. Would build on the violence heuristic plus
loitering/grouping and repeated-targeting patterns; genuinely hard (contextual, easily
false-positive) and best treated as a research-grade feature.

**8. Weapon Detection** — No weapon classifier. Requires a dedicated object-detection model
(gun/knife) — a well-scoped addition to the existing YOLO detector, but needs a trained model
and validation.

**12. Smoke and Fire Detection** — Not built. Requires a fire/smoke vision model or sensor
integration.

**13. Object Left Behind Detection** — Not built. Requires stationary-object persistence logic
(a bag/box that stays put with no owner nearby for N seconds) — a well-defined addition on top
of the existing tracker.

---

## 3. Complete List of Features Currently in the Project

This is the full inventory of what Sentinel does **today**, grouped by area.

### 3.1 Core Vision Pipeline
- **Object detection** (YOLO-based, `detector.py`) — people and vehicles.
- **Multi-object tracking** (`tracker.py`) with stable per-camera track IDs.
- **Cross-camera re-identification** (`reid.py`, OSNet / torchreid) — assigns a persistent
  **Global ID** to the same person across cameras; camera-adjacency gating stops "teleporting."
- **Single-camera and multi-camera modes** (`mode_manager.py`, `event.py` session keying).

### 3.2 Safety & Behaviour Analytics
- **Fall detection** (`fall_detector.py`).
- **Running detection** (`running.py`).
- **Fight / violence (anomaly) detection** (`anomaly_detector.py`).
- **Loitering / prolonged-dwell detection** (`incident_manager.py`).
- **Dress-code / uniform compliance** (`event.py`).
- **Per-person risk scoring** (`incident_manager.py` — aggregates behaviours into a risk level).

### 3.3 Security & Access Control
- **Intrusion — restricted-zone entry** alerts (`alerts.py`, `zones.json`).
- **After-hours / non-school-day entry** alerts, driven by a **campus holiday calendar**
  (`school_calendar.py`) and per-zone active hours.
- **Zone management** — draw/define polygon zones, occupancy limits, restricted flags
  (`zone_manager.py`, `zone_logic.py`).
- **Directional line-crossing counters** (turnstile entry/exit counts, `line_counter.py`).
- **Occupancy / crowd-density monitoring** (`event.py`, `periodic_report.py`).

### 3.4 Alerting & Notifications
- **Alert engine** with red-box **snapshot capture** of the exact moment (`alerts.py`).
- **Principal notification inbox** (unread/read state) for every alert.
- **Alert callbacks** (e.g. WebSocket broadcast hook for the dashboard).
- Deep-link metadata (video path, frame, track/global ID) to **jump straight to the footage**.

### 3.5 Search, Playback & Investigation
- **Natural-language search** (`intent_manager.py`, `search_service.py`, `query_engine.py`) —
  e.g. "show falls", "intrusions on camera 1", "loitering", "dress code violations". Handles
  singular/plural, objects, cameras, zones, time windows; empty query returns all events.
- **One-click "jump to clip" playback** (`clip_service.py`) — modal with byte-range seekable
  video, a **"why logged"** explanation, timestamps, and clip window.
- **Subject-highlight overlay** — canvas bounding box that highlights **only the logged
  subject** during clip playback.
- **Spatial heatmap** of where people dwell (`heatmap.py`).

### 3.6 Reporting & Governance
- **Automated daily / monthly / yearly summary reports** (`periodic_report.py`) — headcount,
  crowd density (peak simultaneous), congestion, dwell/loitering, and a safety-alert breakdown,
  with a background daily-report scheduler.
- **Append-only audit log** (`audit_log.py`) — who / what / when / role / IP for every
  sensitive action (login, search, viewing clips, snapshots, cameras, reports).
- **Cross-camera identity report** — people handed off between cameras.
- **Zone-flow metrics** — total entries/exits, active counts, average dwell times.

### 3.7 Platform & Deployment
- **FastAPI web dashboard** (`backend/server.py`, `backend/index.html`) — ~42 API routes.
- **Live multi-camera grid** — location/floor-based, looping MJPEG, 1/4/8/all layouts
  (`camera_registry.py`, `camera_stream.py`, `cameras.json`).
- **Licensing** (`license_validator.py`, `generate_key.py`) — Ed25519-signed licenses,
  camera-count cap, per-feature gating, expiry, hardware lock.
- **Role-based access** — developer / tech team / principal logins.
- **Database abstraction** — same code path on **SQLite and PostgreSQL** (`db_schema.py`).
- **Containerisation** support and a benchmark/profiling harness (`benchmark_pipeline.py`).
- **Test suites** — `test_fall.py` (5/5), `test_reid.py` (11/11), `test_licensing.py` (9/9).

---

## 4. Future Works — Proposed Roadmap

Ordered by effort-to-value. Items in **Tier 1** reuse the existing pipeline and could ship
quickly; **Tier 3** needs new models and validation.

### Tier 1 — Complete the partials & quick wins (reuse existing pipeline)
1. **Object Left Behind Detection** — flag a stationary object (bag/box) with no owner within
   N metres for N seconds. Builds directly on the current tracker; no new model.
2. **Slip vs. Fall separation** — extend `fall_detector.py` to distinguish a slip (brief drop +
   recovery) from a sustained fall, and add a dedicated **"hazard"** event type.
3. **Attendance Automation (count-based → zone-based)** — turn line-crossing + zone headcount
   into per-period, per-zone attendance sheets and CSV export. (Named attendance is Tier 3.)
4. **Real alert delivery** — replace the console/inbox sink with **email / SMS / push**
   (the code already has a clean `register_alert_callback` hook).
5. **Alert/notification data hygiene** — a "clear alerts" maintenance action (tables are
   currently cumulative across runs).

### Tier 2 — New detectors (dedicated model, well-scoped)
6. **Weapon Detection** — add a gun/knife detection model to the YOLO stage, with a high
   confidence threshold and human-in-the-loop confirmation.
7. **Smoke & Fire Detection** — fire/smoke vision model (or integrate existing smoke sensors),
   routed through the same alert + snapshot + notification pipeline.
8. **Unauthorized Person Detection (roster-based)** — optional, privacy-gated enrolment of
   authorised staff/students (badge/RFID preferred over face for privacy), so "unknown person
   in a staff-only zone" becomes actionable. Requires a data-privacy policy sign-off.

### Tier 3 — Research-grade / higher risk
9. **Bullying Detection** — combine violence heuristics, grouping/encirclement patterns,
   repeated targeting of the same GID, and loitering. Contextual and false-positive-prone;
   treat as an R&D track with careful evaluation.
10. **Trained action-recognition upgrade** — replace the heuristic fight/running/fall detectors
    with a learned temporal model (e.g. pose-based) for accuracy, once GPU is available.
11. **Face-based identity (attendance & unauthorised person)** — only if policy allows;
    highest privacy sensitivity, needs consent framework and secure storage.

### Cross-cutting platform improvements
- **GPU deployment path** — current build is CPU-only; a GPU tier would lift FPS and enable
  trained models.
- **Scalable multi-stream** — server-composited mosaic stream to beat the browser's
  ~6-connection MJPEG cap for reliable 8-up grids.
- **Faststart MP4 re-muxing** for instant mid-file clip seeking.
- **Model/feature configurability per site** — per-deployment thresholds surfaced in the UI.

---

## 5. Recommendation

The platform already covers the **safety and security core** a school needs: falls, running,
violence, loitering, intrusion, after-hours entry, crowd density, dress code, and privacy-
controlled cross-camera tracking — all with alerting, notifications, searchable clips, audit
logging and reporting. The highest-value, lowest-risk next steps are **Tier 1** (object-left-
behind, slip/hazard split, attendance export, real alert delivery). **Weapon** and **smoke/fire**
detection are the most impactful new detectors and are well-scoped for Tier 2. **Bullying** and
**face-based identity** should be scheduled as deliberate R&D with a privacy review, not quick
adds.
