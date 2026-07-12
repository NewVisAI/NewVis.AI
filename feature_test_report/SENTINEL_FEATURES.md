# Sentinel AI — Feature Overview & Future Roadmap

**Project:** Sentinel AI — School Campus Video Analytics Platform
**Prepared for:** Project Manager
**Date:** 2026-07-11
**Environment:** CPU-only (no GPU required), Python 3.13, FastAPI web dashboard, SQLite/PostgreSQL

---

## Introduction

Sentinel is an AI video-analytics platform for school campuses. It watches camera feeds and
turns them into searchable, alertable insight: who is where, what safety or security events
occur, and how spaces are used — all through a secure, licensed web dashboard. This document
describes **every feature currently in the product**, what it does and how it helps, followed
by a **Future Works** section covering features we plan to add based on user requirements.

---

## Part A — Current Features

### 1. Core Vision Pipeline

**Object Detection**
*What it does:* Detects people (and vehicles) in every camera frame using a YOLO-based model.
*How it helps:* This is the foundation — nothing can be counted, tracked, or alerted on until
objects are reliably found in the video.

**Multi-Object Tracking**
*What it does:* Assigns each detected person a stable ID and follows them frame-to-frame within
a camera.
*How it helps:* Turns isolated detections into continuous journeys, so the system can measure
dwell time, speed, and movement instead of just counting blobs.

**Cross-Camera Re-Identification (ReID)**
*What it does:* Recognises the same person as they move between cameras and gives them one
persistent **Global ID**, using a purpose-built person-ReID model (OSNet). Camera-adjacency
rules prevent impossible "teleport" matches.
*How it helps:* Lets staff follow a person across the whole campus as a single identity rather
than losing them at every camera boundary — essential for investigations and accurate counts.

---

### 2. Safety & Behaviour Analytics

**Fall Detection**
*What it does:* Flags when a person goes from upright to horizontal with a sudden downward drop,
using body-geometry over a short time window (with smoothing to avoid false alarms).
*How it helps:* Surfaces medical emergencies or accidents in real time so staff can respond
quickly, even in areas no one is actively watching.

**Running Detection**
*What it does:* Measures each person's speed (normalised by their size so it works near or far
from the camera) and flags running.
*How it helps:* Running in corridors or classrooms often signals a fight, a chase, panic, or a
hazard — an early behavioural cue worth a look.

**Fight / Violence Detection**
*What it does:* Flags a possible altercation when two people are very close together **and** both
moving with high, agitated motion for several consecutive moments.
*How it helps:* Gives staff an early warning of physical conflict so they can intervene before
it escalates, with a saved snapshot as evidence.

**Loitering Detection**
*What it does:* Tracks how long each person stays in a zone and flags prolonged dwell beyond a
configurable threshold.
*How it helps:* Highlights unusual lingering (e.g. someone waiting near an exit or a restricted
area) that may need attention.

**Dress-Code / Uniform Compliance**
*What it does:* Checks each person's clothing colour against the school's allowed uniform colours
and flags likely violations, labelling them on the video.
*How it helps:* Automates a routine, time-consuming manual check and gives staff a searchable
record of non-compliance.

**Per-Person Risk Scoring**
*What it does:* Combines a person's behaviours (loitering, zone activity, etc.) into a running
risk level shown on the annotated feed.
*How it helps:* Lets staff triage at a glance — who needs attention first — instead of reading
every individual event.

---

### 3. Security & Access Control

**Intrusion Detection (Restricted Zones)**
*What it does:* Raises an alert the moment anyone enters a zone marked as restricted.
*How it helps:* Protects sensitive areas (labs, offices, storage) without a guard watching the
monitor, and captures a snapshot and footage link for every breach.

**After-Hours / Non-School-Day Entry**
*What it does:* Alerts when someone enters a monitored zone outside its allowed hours, on
weekends, or on calendar holidays (driven by a campus holiday calendar).
*How it helps:* Catches out-of-hours activity — a strong signal of trespassing or unauthorised
access — automatically, based on the school's real schedule.

**Zone Management**
*What it does:* Lets operators draw polygon zones on each camera and set properties like
occupancy limits, active hours, and restricted flags.
*How it helps:* Makes the whole system configurable to each campus's layout and rules without
code changes.

**Directional Line-Crossing Counters**
*What it does:* Counts people crossing a virtual tripwire line in each direction (turnstile-style
entry/exit counting).
*How it helps:* Gives accurate in/out counts at doorways and corridors for flow analysis and
capacity monitoring.

**Crowd-Density / Occupancy Monitoring**
*What it does:* Measures peak simultaneous people and per-zone occupancy against configured
limits.
*How it helps:* Identifies congestion and capacity pressure points (stairwells, entrances) to
support safety planning and crowd management.

---

### 4. Alerting & Notifications

**Alert Engine with Snapshot Capture**
*What it does:* For every safety/security event it records an alert and saves a snapshot image
with a red box around the exact subject and moment.
*How it helps:* Gives staff immediate visual proof and a permanent record — no scrubbing through
hours of footage to find the moment.

**Principal Notification Inbox**
*What it does:* Delivers every alert to an in-app inbox with unread/read status.
*How it helps:* Ensures serious events reach a decision-maker and can be tracked to
acknowledgement.

**Jump-to-Footage Deep Links**
*What it does:* Every alert stores the exact camera, video, frame, and identity needed to open
the recorded moment directly.
*How it helps:* One click takes staff to the precise clip, turning review from minutes into
seconds.

---

### 5. Search, Playback & Investigation

**Natural-Language Search**
*What it does:* Lets users search events in plain English — "show falls", "intrusions on camera
1", "loitering", "dress code violations" — and handles plurals, objects, cameras, zones, and
time windows.
*How it helps:* Non-technical staff can investigate incidents without learning a query language
or filtering tables by hand.

**One-Click "Jump to Clip" Playback**
*What it does:* Opens any result in a video modal that seeks straight to the moment, shows a
plain-English **"why it was logged"**, and the exact clip window.
*How it helps:* Makes every logged event instantly reviewable with full context, so decisions are
made on the footage, not a text row.

**Subject-Highlight Overlay**
*What it does:* During clip playback, draws a box that follows **only the logged person**, not
everyone in frame.
*How it helps:* Removes ambiguity about who the event refers to in a crowded scene.

**Spatial Heatmap**
*What it does:* Visualises where people spend the most time across a camera view.
*How it helps:* Reveals traffic patterns and hotspots to inform staffing, signage, and layout
decisions.

---

### 6. Reporting & Governance

**Automated Daily / Monthly / Yearly Reports**
*What it does:* Generates period summaries — headcount, crowd density, congestion, dwell/
loitering, and a safety-alert breakdown — with a background scheduler that emits a daily summary
automatically.
*How it helps:* Gives administrators regular, digestible insight without anyone running reports
by hand.

**Append-Only Audit Log**
*What it does:* Records who did what and when — logins, searches, and every time footage,
snapshots, or reports are viewed — including role and IP, in a tamper-evident append-only trail.
*How it helps:* Provides accountability and a compliance trail for handling sensitive footage,
which is critical in a school setting.

**Cross-Camera Identity & Zone-Flow Reports**
*What it does:* Reports people handed off between cameras, plus zone entry/exit totals, active
counts, and average dwell times.
*How it helps:* Turns raw tracking into operational metrics about how the campus is actually used.

---

### 7. Platform & Deployment

**Web Dashboard**
*What it does:* A FastAPI-based web application (~42 API routes) presenting live feeds, search,
alerts, reports, and governance in one place.
*How it helps:* One accessible interface for all users — no specialist software to install.

**Live Multi-Camera Grid**
*What it does:* Shows cameras grouped by floor/location with 1 / 4 / 8 / all layouts; click a tile
to drill into a single feed.
*How it helps:* Lets operators monitor the whole campus at once and zoom into any area on demand.

**Licensing & Feature Gating**
*What it does:* Cryptographically signed licenses (Ed25519) enforce a camera-count cap, per-
feature access, expiry, and hardware lock.
*How it helps:* Enables controlled, tiered deployment and protects the product commercially.

**Role-Based Access Control**
*What it does:* Separate logins and permission levels for developer, tech team, and principal.
*How it helps:* Ensures each user sees only what their role should — important for privacy and
security.

**Database Flexibility**
*What it does:* The same code runs on lightweight SQLite or production-grade PostgreSQL.
*How it helps:* Easy to demo on a laptop and scale to a real deployment without rewrites.

**Privacy-Controlled Tracking**
*What it does:* People are tracked as anonymised **Global IDs** — no names or personal data —
combined with license gating, role-based access, and the audit log.
*How it helps:* Delivers the safety benefits of tracking while respecting student privacy, which
is the posture schools require.

---

## Part B — Future Works

Features we will or may add, based on user requirements. Grouped by how soon they can be
delivered.

### Near-Term (extends the current pipeline — no new hardware)

- **Object Left Behind Detection** — flag an unattended bag or object that stays put with no
  owner nearby, using the existing tracker. *Benefit: catches suspicious or forgotten items.*
- **Slip & Hazard Detection** — extend fall logic to distinguish a slip (brief loss of footing)
  from a full fall, and add a dedicated hazard event type. *Benefit: broader accident coverage
  and early hazard warning.*
- **Attendance Automation (count-based)** — turn line-crossing and zone headcount into per-period
  attendance sheets with export. *Benefit: automated occupancy/attendance records.*
- **Real Alert Delivery** — send alerts by email, SMS, or push notification, not just the in-app
  inbox. *Benefit: staff are reached instantly, even off the dashboard.*

### Mid-Term (new detection models, well-scoped)

- **Weapon Detection** — add a gun/knife detection model with high-confidence, staff-confirmed
  alerts. *Benefit: early warning of the most serious threats.*
- **Smoke & Fire Detection** — a fire/smoke vision model (or sensor integration) routed through
  the existing alert pipeline. *Benefit: rapid response to fire emergencies.*
- **Unauthorized Person Detection (roster-based)** — optional, privacy-reviewed enrolment of
  authorised staff/students (badge/RFID preferred) so an unknown person in a staff-only area is
  flagged. *Benefit: true access control beyond zone/time rules.*

### Longer-Term / Research (higher effort, needs validation)

- **Bullying Detection** — combine violence cues, grouping/encirclement, repeated targeting of the
  same person, and loitering. *Benefit: addresses a key school-safety concern; requires careful
  tuning to avoid false alarms.*
- **Trained Action Recognition** — upgrade the heuristic fall/running/fight detectors to a learned
  temporal model for higher accuracy (GPU deployment). *Benefit: fewer false positives, better
  detection.*
- **Face-Based Identity** — only if school policy and consent allow; enables named attendance and
  strict access control. *Benefit: highest-fidelity identity, at the cost of significant privacy
  considerations.*

### Platform Improvements

- **GPU Deployment Path** — lift processing speed and enable heavier trained models.
- **Scalable Multi-Stream Grid** — a server-composited mosaic to reliably show many live cameras
  at once.
- **Instant Clip Seeking** — re-mux source video for faster mid-file playback.
- **Per-Site Configuration** — expose detection thresholds and rules in the UI for each deployment.

---

*This document reflects the current state of the Sentinel AI platform and the planned direction of
development. Future Works items are candidates driven by user requirements and may be reprioritised.*
