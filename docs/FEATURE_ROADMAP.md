# School Safety Feature Roadmap

This maps the 15 requested school-safety features onto the existing pipeline
(`detector.py`, `tracker.py`, `reid.py`, `zone_manager.py`/`zone_logic.py`,
`event.py`, `incident_manager.py`, `db.py`/`db_schema.py`, `query_engine.py`).

**Correction (2026-07-01):** an earlier version of this doc said none of
the 15 features were implemented yet. That was wrong — it was written
before a full read of `app.py`/`event.py`/`incident_manager.py`. Several
already exist and are wired into the running app. Status below is accurate
as of this date; keep it updated as work progresses.

## Already implemented and wired into `app.py`

- **Intrusion Detection** — `incident_manager.py`'s `IncidentManager`
  scores entry into zones classified as "restricted" (zone name contains
  restricted/vault/server) and logs an incident.
- **Loitering Detection** — `IncidentManager.LOITERING_THRESHOLD = 15.0`
  seconds; any tracked person staying in a zone past that is flagged.
- **Crowd Density Monitoring** — `event.py: check_occupancy_alerts()`
  compares live per-zone person counts against `max_occupancy` (set per
  zone in `zones.json`) and logs `occupancy_alert` events.
- **Dress Code Compliance** — `event.py: check_dress_code()` extracts
  shirt color (`reid.py: extract_shirt_color`) and compares it to a
  hardcoded list of 3 allowed uniform RGB colors, logging
  `dress_code_violation` events. Needs the school's real uniform colors
  and ideally a garment-shape check, not just color similarity.

## Drafted, not yet wired in

- **Running Detection** — `running.py` (in project root) computes
  frame-to-frame centroid displacement normalized by bbox height
  ("bbox-heights per second"), avoiding the need for camera calibration.
  No new ML model required — pure logic on existing tracker output. Still
  needs to be imported and called from `app.py`'s per-track loop (see
  `HANDOFF.md` for exact wiring steps) and its speed threshold tuned
  against real footage.

## Not started, cheap (reuse existing zone/tracking primitives)

- **Object Left Behind Detection** — needs bag/backpack/suitcase added to
  `detector.py`'s `TARGET_CLASSES` (COCO ids: backpack=24, handbag=26,
  suitcase=28 — not currently tracked), then logic to flag an object track
  that separates from any person track and stays stationary past a time
  threshold.
- **Student Tracking (Privacy-Controlled)** — the ReID/Global ID engine
  already exists (`reid.py`, `docs/GLOBAL_ID.md`) but has no privacy
  controls: no retention limits, no consent/anonymization, no access
  control. This needs a privacy/policy conversation with the school (it
  involves identifiable minors) before building further — not just an
  engineering task.

## Not started, need a new lightweight model or heuristic

- **Fall Detection** / **Slip and Hazard Detection** — need pose
  estimation or an aspect-ratio/velocity heuristic on existing bounding
  boxes. Closely related to each other and to the running-detection motion
  analysis above (a sudden bbox aspect-ratio collapse can indicate a fall).
- **Unauthorized Person Detection** — needs an enrolled-roster face/badge
  match layered on top of the existing zone/time rules.

## Not started, need a dedicated newly-trained model (highest effort)

- **Weapon Detection** — rare-class object detector; needs a curated
  dataset, can't just reuse the general YOLO model.
- **Fight and Violence Detection** — temporal/action-recognition model
  (not single-frame); typically a clip classifier over tracked person
  pairs.
- **Bullying Detection** — hardest of the set; likely proximity/action
  heuristics rather than a single model. Should launch as an
  advisory/human-review flag, not an automated response — false-positive
  risk and student-privacy sensitivity are both high.
- **Smoke and Fire Detection** — frame-level classifier, doesn't depend on
  the person-tracking pipeline at all; one of the easier *new-model*
  features to add despite being in this "highest effort" tier.
- **Attendance Automation** — needs face recognition or badge/ID scanning
  integrated with the school's student information system; a separate
  system from the anonymous tracking this pipeline currently does.

## Suggested build order

1. Finish wiring `running.py` — it's already written, just needs
   integration (fastest remaining task).
2. Object Left Behind Detection — cheap, reuses existing primitives, just
   needs new object classes tracked.
3. Fire/smoke and weapon detection — highest safety severity; fire/smoke
   is the simpler of the two new models to add.
4. Fall and slip/hazard detection — moderate effort, extends the same
   bbox-motion-analysis approach as running detection.
5. Unauthorized person detection and attendance automation — need an
   enrollment/roster system (new component) before they're useful.
6. Fight/violence and bullying detection last — hardest ML problems,
   should launch as human-review alerts rather than automated responses.
7. Student tracking privacy controls — should happen in parallel with the
   above as a policy/legal workstream, not purely technical.

## Scale note

All of the above assumes the current single-camera-per-process
architecture. Running this across ~10,000 cameras needs a separate
architecture pass (distributed ingestion, edge pre-filtering, message
queue, horizontal scaling of detection workers) that is intentionally
deferred per the user's 2026-07-01 decision.
