# Sentinel AI — Product Editions & Feature Catalog

**Purpose:** marketing-facing feature list and edition breakdown. Features are ordered by
computational cost (ascending) so that each edition maps cleanly to a hardware/compute footprint.

**Editions:** Basic · Premium · Pro. Every feature carries a status:

| Status | Meaning |
|---|---|
| ★ **Production** | Deterministic implementation or measured behaviour. Safe to demo and to sell on. |
| 🔶 **Beta** | Runs live in the pipeline today. Accuracy is being measured against real footage before it is promised as a number. |

Sentinel AI is a **generic multi-camera video-analytics platform** — the same product runs across
campuses, retail, offices, transit, warehouses, and industrial sites. Nothing on this page is
domain-specific.

---

## The feature ladder — ordered by computational cost

### Rung 0 — Zero inference (pure logic on already-detected data)

These features cost effectively nothing per frame; they run on tracker output, timestamps, and DB
rows. They are the safest floor of the product.

| Feature | Status | Edition |
|---|---|---|
| Zone drawing, entry/exit logging | ★ Production | Basic |
| Restricted-zone alerts | ★ Production | Basic |
| After-hours / schedule-based alerts | ★ Production | Basic |
| Notification inbox with snapshot evidence | ★ Production | Basic |
| Role-based access (multi-role user accounts) | ★ Production | Basic |
| Audit log (accountability trail) | ★ Production | Basic |
| Headcount charts + periodic reports | ★ Production | Basic |
| Dwell heatmap (aggregation) | ★ Production | Basic |
| Licensing + per-feature gating | ★ Production | Basic |

### Rung 1 — Lightweight overhead (decode + timers + geometry)

Adds video decode and simple geometric operators on top of the tracker.

| Feature | Status | Edition |
|---|---|---|
| Live multi-camera grid (RTSP) | ★ Production | Basic |
| Jump-to-footage clip playback | ★ Production | Basic |
| Dwell time / loitering | ★ Production | Premium |
| Line-crossing in/out counts | ★ Production | Premium |
| Occupancy alerts (per-zone density limits) | ★ Production | Premium |

### Rung 2 — Baseline inference (per-frame detector)

The core CV pass — YOLOv8n + ByteTrack. Everything above this line rides on the same one detection
per frame; adding heuristics on top is close to free.

| Feature | Status | Edition |
|---|---|---|
| **Person detection + multi-object tracking** | ★ Production | Basic |
| Running detection | 🔶 Beta | Premium |
| Dress-code / uniform compliance | 🔶 Beta | Premium |
| Fall detection (bbox heuristic) | 🔶 Beta | Premium |

### Rung 3 — Conditional or per-event inference

Runs only when triggered by an event or when the user asks — average steady-state cost is close to
Rung 2.

| Feature | Status | Edition |
|---|---|---|
| Pose verification for falls (event-gated) | 🔶 Beta | Pro |
| Violence / altercation detection | 🔶 Beta | Pro |
| AI activity summary (natural-language) | 🔶 Beta | Pro |
| AI natural-language search over events | 🔶 Beta | Pro |

### Rung 4 — Cross-camera inference (highest cost)

Runs an appearance model on every person on every camera and correlates across sites.

| Feature | Status | Edition |
|---|---|---|
| Cross-camera Re-ID / Global Person ID | 🔶 Beta | Pro |
| Investigation graph (cascades, recurring actors) | 🔶 Beta | Pro |

---

## Edition summary

### Basic — Monitoring & Alerts
Deterministic monitoring floor. Everything in this tier is Production.

**Includes:** Live multi-camera grid · person detection + tracking · zones with entry/exit logs ·
restricted-zone and after-hours alerts · notification inbox with snapshot evidence · jump-to-footage
playback · dwell heatmap · headcount charts and periodic reports · role-based access · audit log.

**Positioning:** "Reliable eyes on every camera, alerts you can trust, and a searchable trail of what
happened."

### Premium — Behavioural Analytics
Adds behavioural rules and safety detections on top of Basic.

**Adds:** Dwell / loitering · line-crossing in-out counts · occupancy alerts · running detection
(Beta) · fall detection (Beta) · dress-code / uniform compliance (Beta).

**Positioning:** "Turn presence into behaviour: know when someone lingers, crosses, runs, falls, or
breaks dress code."

### Pro — Investigation & Intelligence
Adds cross-camera intelligence and AI-assisted investigation.

**Adds:** Violence / altercation detection (Beta) · AI activity summary (Beta) · AI natural-language
search (Beta) · pose-verified fall confidence (Beta) · cross-camera Re-ID / Global ID (Beta) ·
investigation graph (Beta).

**Positioning:** "Follow a person across cameras, ask questions in plain English, and reconstruct
what happened without scrubbing hours of footage."

---

## Selling with the Beta label

The Beta label is a feature, not a caveat to hide. It signals that a capability is in the product
today but its numeric accuracy is being measured on the customer's own footage before being
committed to. Beta features graduate to Production once labelled footage produces a measured recall
and false-alarm number and a named human signs off — see `FEATURE_STATUS.md` for the promotion
gate.

Safety-adjacent Beta features (fall, violence) are **advisory + human review**, never an
automated response. Say this in every demo.

---

## What isn't a feature edition

Deployment-cost optimizations — sub-stream analytics, hardware decode offload, camera-side motion
gating, frame-delta skipping, FP16 quantization — are **flag-gated infrastructure**. They reduce
the compute needed to deliver a given edition; they are not a customer-facing capability. Discuss
them in the ops / infrastructure conversation, not on the pricing page.

Roadmap capabilities that are named but not built (weapon detection, fire/smoke, object-left-behind,
attendance automation, unauthorized-person alerts) belong on a separate roadmap slide, not on the
edition matrix.

---

## Reference: license feature codes ↔ editions

Editions are enforced by the existing per-feature license system (`license_validator.py`). The tier
bundles are:

- **Basic** → `core_tracking`, `zone_alerts`, `notifications`, `reports`, `api_access`
- **Premium** → Basic + `running_detection`, `fall_detection`, `dress_code`
- **Pro** → Premium + `nl_search`, plus the cross-camera and AI-summary codes

See `license_validator.py` — `EDITION_BUNDLES` — for the machine-readable map used at issue time.
