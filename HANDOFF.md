# Handoff Notes — School CCTV Project (read this first)

## UPDATE 2026-07-02 — hybrid merge with the SCI fork is DONE

This repo was merged with the sibling fork
(https://github.com/dhxvxn/SIngle-Camera-Intelligence.git). Decisions made
with the user: ByteTrack kept (over DeepSORT), hybrid fall detector (SCI
transition logic + EMA smoothing, routed through alerts), offline
Ed25519-signed license keys (no license server), role-based web dashboard.
New/rewritten since the notes below: `alerts.py`, `school_calendar.py`,
`search_service.py`, `mode_manager.py`, `fall_detector.py`,
`license_validator.py`, `generate_key.py`, `backend/auth.py`,
`backend/server.py`, `backend/index.html`. Zone rules + holiday calendar +
principal notifications + headcount reports are wired into `app.py`
(menu options 4/5/6) and the events pipeline (`event.py` raises zone alerts
on entry). All tests pass (test_fall, test_licensing, test_reid); backend
role gates verified live. The private license-signing key lives in
`dev_keys/` (gitignored) — losing it means reissuing every license.
The sections below predate the merge; feature-status details still apply.

This project was being worked on with Claude via a remote device bridge
(no shell/delete access on this machine — only read/write files). We're
switching to Claude Code running locally in this folder so it has full
shell access (delete, move, install packages, run the app, git). This file
summarizes everything decided and done so far so nothing gets re-derived
or re-decided from scratch.

## Goal

Repurpose this hackathon prototype into the foundation for a school safety
CCTV system, eventually targeting ~10,000 cameras, implementing 15
features (listed below). The 10,000-camera distributed-architecture
redesign is explicitly **deferred** — current work stays scoped to
single/few-camera, per the user's decision on 2026-07-01.

## What this codebase actually is

A working single-camera surveillance pipeline:
detection (`detector.py`, YOLOv8/RT-DETR) → tracking (`tracker.py`,
ByteTrack) → Re-ID (`reid.py`, ResNet50 embeddings + shirt-color) → zone
logic (`zone_manager.py`, `zone_logic.py`, `zones.json`) → session/event
tracking (`event.py`) → risk scoring (`incident_manager.py`) → SQLite
storage (`db.py`, `db_schema.py`) → NL search (`intent_manager.py`,
`llm_parser.py`, `query_engine.py`) → playback (`video_player.py`) →
FastAPI backend (`backend/server.py`).

## IMPORTANT correction

Earlier in this session, before reading `app.py`, `event.py`, and
`incident_manager.py` in full, I told the user none of the 15 requested
features existed yet. **That was wrong** — several are already
implemented and wired into `app.py`. Don't repeat that mistake; the
accurate status is in `docs/FEATURE_ROADMAP.md` (already corrected) and
summarized below.

## Feature status (accurate, as of 2026-07-01)

**Already implemented and wired into `app.py`:**
- Intrusion Detection — `incident_manager.py` (`IncidentManager`), scores
  entry into zones classified as "restricted" (name contains
  restricted/vault/server) via `_get_zone_type`.
- Loitering Detection — `IncidentManager.LOITERING_THRESHOLD = 15.0`
  seconds, checked in `update_risk()`.
- Crowd Density Monitoring — `event.py: check_occupancy_alerts()`, compares
  live per-zone counts to `max_occupancy` from `zones.json`, logs
  `occupancy_alert` events.
- Dress Code Compliance — `event.py: check_dress_code()`, compares
  extracted shirt RGB (`reid.py: extract_shirt_color`) against a hardcoded
  list of allowed uniform colors, logs `dress_code_violation` events. Only
  3 hardcoded RGB colors currently — needs the school's actual uniform
  colors and probably garment-shape checks, not just color.

**Drafted but NOT yet wired in** (this is the in-progress task):
- Running Detection — `/tmp/running.py` was written this session (should
  be copied to the project root, i.e. `running.py` alongside the other
  modules) but has NOT been imported or called from `app.py` yet. It's a
  pure-logic feature (no new ML model): computes centroid displacement
  per frame normalized by bbox height ("bbox-heights per second") to avoid
  needing camera calibration. Still needs:
  1. Copy `running.py` into the project root.
  2. In `app.py`'s `_process_camera_frame`, import
     `check_running, is_currently_running, reset_running_state` from
     `running`, call `check_running(track_key=(camera_state.camera_id,
     track_id), global_id=global_id, camera_id=camera_state.camera_id,
     bbox=(x1,y1,x2,y2), video_time=video_time,
     video_path=camera_state.source)` in the same per-track loop as
     `check_dress_code`, and append `" | RUNNING"` to the label when
     `is_currently_running(global_id, video_time)` is true (same pattern
     used for `active_uniform_violations`).
  3. Call `reset_running_state()` everywhere `reset_runtime_state()` is
     already called (in `run_surveillance_mode` and `_seek_all_cameras`)
     so a seek doesn't cause false running triggers from stale history.
  4. The speed threshold (`RUNNING_SPEED_THRESHOLD = 2.2` bbox-heights/sec)
     is a guess — needs tuning against real footage.

**Not started, but cheap (reuse existing zone/tracking primitives):**
- Object Left Behind Detection — needs bag/backpack/suitcase added to
  `detector.py`'s `TARGET_CLASSES` (COCO ids: backpack=24, handbag=26,
  suitcase=28 — not currently tracked, only person/vehicle classes are),
  then logic to detect an object track that separates from any person
  track and stays stationary past a threshold.
- Student Tracking (Privacy-Controlled) — the ReID/Global ID engine
  already exists (`reid.py`, `docs/GLOBAL_ID.md`) but has zero privacy
  controls (no retention limits, no consent/anonymization, no access
  control). This needs a policy conversation with the school (it involves
  identifiable minors) before building anything further here — flag this
  to the user, don't just build it.

**Not started, need a new lightweight model or heuristic:**
- Fall Detection, Slip/Hazard Detection — need pose estimation or an
  aspect-ratio/velocity heuristic on existing bounding boxes; closely
  related to each other and to the running-detection motion analysis
  above (same bbox-history approach could extend to detect sudden
  aspect-ratio collapse = fall).
- Unauthorized Person Detection — needs an enrolled-roster face/badge
  match, combined with existing zone/time rules.

**Not started, need a dedicated newly-trained model (highest effort):**
- Weapon Detection (rare-class object detector, needs curated dataset)
- Fight and Violence Detection (temporal/action-recognition model, not
  single-frame)
- Bullying Detection (hardest; likely proximity/action heuristics rather
  than one model; should be advisory/human-review only, high false-positive
  risk and student-privacy sensitivity)
- Smoke and Fire Detection (frame-level classifier, doesn't depend on the
  tracking pipeline at all — one of the easier *new-model* features)
- Attendance Automation (face recognition or badge scan integrated with
  the school's student information system — separate system, not an
  extension of anonymous tracking)

## Cleanup status

`cleanup_hackathonpro.ps1` was delivered to the project root but **has not
been run yet** (confirmed by directory listing — `SUBMISSION.md`,
`DEMO_SCRIPT.md`, both demo `.mp4`s, `cctv_logs.db`, `snapshots/`,
`__pycache__`, `.venv`, `.codex`, and `docs/FEATURES_CHECKLIST.md` are all
still present). It moves them into `_to_delete/` rather than deleting
outright. Since Claude Code has real shell access, it can just delete
these directly if the user confirms — the PowerShell script is now
redundant/optional.

## Files already updated in the project (delivered and committed)

- `README.md` — rewritten for the school-CCTV framing.
- `docs/FEATURE_ROADMAP.md` — feature-to-code mapping (see corrected
  status above; make sure the doc matches this handoff, not the earlier
  wrong version).
- `.gitignore` — added `.venv/` and `_to_delete/`.
- `cleanup_hackathonpro.ps1` — optional now given real shell access.

## Suggested next steps for Claude Code

1. Confirm with the user whether to just delete the hackathon-fluff files
   directly (rm) instead of using the PowerShell move-script.
2. Finish wiring `running.py` into `app.py` per the steps above.
3. Move to the next feature per the roadmap's suggested build order,
   confirming priorities with the user rather than assuming.
