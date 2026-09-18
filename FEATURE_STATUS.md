# Sentinel — Feature Status & Release Tiers

**Owner:** Haron · **Last updated:** 2026-07-29 · **Decision source:** project meeting 2026-07-29

The meeting decision: **ship the basics as production, everything accuracy-uncertain as Beta.**
Test the production set now, demo it to the client, then promote Beta → Production one feature at
a time as real footage gives us real numbers.

This file is the single source of truth for what we claim to the client. Nothing gets called
"working" here without either (a) a deterministic implementation we can prove on this box, or
(b) a measured accuracy number from labelled footage. If a feature has neither, it's Beta.

**Reading the tiers:**

| Tier | What we tell the client | Bar to be in this tier |
|---|---|---|
| **① Production** | "This works, here's the demo." | Deterministic logic, or presence/count-level CV. Automated test + passed manual acceptance. |
| **② Beta** | "This runs today; we're measuring accuracy against your footage before we promise a number." | Runs end-to-end in the live pipeline, but accuracy is unquantified or a known-crude heuristic. |
| **③ Not in demo** | Not mentioned, or named as roadmap only. | Not wired into the live path, infra-only, or not built. |

---

## ① Production candidates — deterministic, demo these

These are geometry, timers, database aggregation, and access control. Their correctness does not
depend on model accuracy, so we can prove them on this box today. That's what makes them safe to
put in front of the client.

| Feature | Implementation | Test status | Notes / honest caveat |
|---|---|---|---|
| Live multi-camera grid (RTSP) | [camera_stream.py](camera_stream.py), `/api/cameras/{id}/stream` | manual only | Depends on the site's network, not on us. |
| Person detection + tracking | [detector.py](detector.py), [tracker.py](tracker.py) — YOLOv8n + ByteTrack | **no automated test** | Solid for *is a person here / how many*. Degrades under heavy occlusion — say so. |
| Zone definition + entry/exit logging | [zone_manager.py](zone_manager.py), [zone_logic.py](zone_logic.py), [event.py](event.py) | **no automated test** | Point-in-polygon. Accuracy = whether the zone was drawn right. |
| Restricted-zone alerts | [alerts.py:26](alerts.py:26) `restricted_zone_entry` | **no automated test** | Rule-based on zone class. |
| After-hours / non-school-day alerts | [alerts.py:27](alerts.py:27) + [school_calendar.py](school_calendar.py) | **no automated test** | Calendar + clock. Fully deterministic. |
| Notification inbox + snapshot evidence | `/api/notifications`, `alert_snapshots/` | manual only | Red-box snapshot at the exact frame — this demos very well. |
| Jump-to-footage clip playback | [clip_service.py](clip_service.py), `/api/clip/*` | **no automated test** | Alert → the video moment. Strong client-facing feature. |
| Dwell time / loitering | `IncidentManager` (threshold 15 s) | **no automated test** | Timer on a track. The *threshold* is a tuning choice, not an accuracy claim. |
| Line-crossing in/out counts | [line_counter.py](line_counter.py), wired at [app.py:519](app.py:519) | **no automated test** | Deterministic crossing test — but inherits tracker ID stability. |
| Dwell heatmap | [heatmap.py](heatmap.py), `/api/heatmap` | **no automated test** | Pure aggregation. |
| Headcount charts + periodic reports | `/api/analytics/headcount-series`, [periodic_report.py](periodic_report.py) | **no automated test** | Aggregation over logged events. |
| Role-based access (developer/tech/principal) | [backend/index.html:843](backend/index.html:843) `NAV`, `require_roles` | **no automated test** | Nav and every endpoint are role-gated. |
| Audit log | [audit_log.py](audit_log.py), `/api/audit` | **no automated test** | Accountability trail — schools care about this more than we expect. |
| Licensing + per-feature gating | [license_validator.py:45](license_validator.py:45) | ✅ `test_licensing.py` green | Feature codes already exist; see the gating note below. |

> **The gap that matters:** every row above is *deterministic*, but almost none of it has an
> automated test. Right now `test_licensing`, `test_fall`, `test_reid`, `test_event_graph` are the
> whole suite — 32 tests, and the core zone/alert/counting logic isn't in it. Calling this tier
> "production" without regression coverage means the next refactor silently breaks it. Closing
> that gap is Phase 1 of [TEST_PLAN.md](TEST_PLAN.md).

---

## ② Beta — runs live, accuracy not yet earned

Each of these is wired into the live pipeline and will fire on real cameras. What we don't have is
a precision/recall number, because we have no labelled footage. **Label these Beta in the UI and
say the word "Beta" out loud in the demo.**

| Feature | Implementation | Why it's Beta, specifically |
|---|---|---|
| **Fall detection** | [fall_detector.py](fall_detector.py), wired at [app.py:590](app.py:590) | Bbox aspect-ratio + velocity heuristic, not pose estimation. `test_fall.py` passes on *synthetic* boxes — that proves the logic, not the accuracy. Zero real-world false-positive data. |
| **Running detection** | [running.py](running.py), wired at [app.py:578](app.py:578) | Speed threshold (bbox-heights/sec) has never been tuned against real footage. Threshold is a guess. |
| **Violence / altercation** | [anomaly_detector.py](anomaly_detector.py), wired at [app.py:631](app.py:631) | The module docstring says it plainly: *"not a trained fight classifier"* — it's a proximity + agitated-motion heuristic. Deliberately conservative, so expect misses. Do not call this "violence detection" to a client without "Beta". |
| **Dress-code compliance** | `check_dress_code` in [event.py](event.py), wired at [app.py:568](app.py:568) | Compares shirt colour to **3 hardcoded RGB values**. Useless until we have the school's actual uniform colours, and colour alone can't tell a shirt from a jacket. |
| **Cross-camera Re-ID / Global ID** | [reid.py](reid.py) — OSNet | ⚠️ **2 unit tests are failing right now**: [test_reid.py:172](test_reid.py:172) and [test_reid.py:226](test_reid.py:226) both expect a global ID to be reused across cameras and get a new ID instead. Separately, `REID_DEFERRED=1` intentionally makes live Global IDs unreliable. **Needs triage before it's shown at all.** |
| **AI natural-language search** | [query_engine.py](query_engine.py), [llm_parser.py](llm_parser.py) | Works on the phrasings we've tried. We have no coverage list, so an off-script client question may return nothing. Demo with rehearsed queries, and be upfront that it's Beta. |
| **AI activity summary** | [summary_manager.py](summary_manager.py) | Just fixed a real bug — it was replaying the last 50 alerts *ever* as current activity (`SUMMARY_WINDOW_MINUTES`, default 15). Unvalidated since the fix, and still uncommitted. |
| **Investigation graph** (trace / cascades / recurring actors) | [event_graph.py](event_graph.py), `/api/investigate/*` | Brand new and **uncommitted**. `test_event_graph.py` is green, but it has never run against real multi-camera data. |

---

## ③ Not in the demo

**Now wired (was Not-in-demo, promoted to Production):**

- **Crowd-density / occupancy alerts.** `check_occupancy_alerts` is now called from the live frame
  path in [app.py](app.py). It is **opt-in per zone** — a zone with no `max_occupancy` field
  configured is skipped entirely, so schools and other domains where zones are routinely crowded do
  not get spurious alerts. Deployers turn it on by setting `max_occupancy` on the zones they care
  about.

**Infra / cost work — keep off, don't demo:**

Sub-stream analytics, hardware decode (NVDEC/QSV/VAAPI), camera-side motion gating, frame dedup,
FP16 quantization. All flag-gated and off by default (see
[REPORT_2026-07-19_cost-optimization-levers.md](REPORT_2026-07-19_cost-optimization-levers.md)).
These are a *cost* story for a later conversation, not a *capability* story.

⚠️ **`MOTION_GATING=1` must stay off for the demo and for acceptance testing.** It skips decode
work, which is exactly the kind of thing that could cause a missed fall. Its safeguard (heartbeat
decode) is untested on real footage.

**Not built** — from [docs/FEATURE_ROADMAP.md](docs/FEATURE_ROADMAP.md), useful as a roadmap slide:
weapon detection, fire/smoke, bullying, object-left-behind, unauthorized-person, attendance
automation. Say "roadmap", never "coming soon with a date".

---

## Promotion gate: Beta → Production

A Beta feature is promoted only when **all four** hold:

1. **Labelled footage exists** — ≥ 30 true positive events and ≥ 2 hours of normal ("nothing
   happened") footage from *this site's* cameras, at the real mounting angles.
2. **Numbers are measured and recorded**, in [TEST_PLAN.md](TEST_PLAN.md), against these targets:

   | Feature | Recall target | Max false alarms | Rationale |
   |---|---|---|---|
   | Fall detection | ≥ 90 % | ≤ 1 / camera / day | Safety-critical: a miss is the worst outcome, so recall wins over precision. |
   | Running detection | ≥ 80 % | ≤ 5 / camera / day | Low severity; a false alarm costs almost nothing. |
   | Violence | ≥ 70 % | ≤ 2 / camera / day | Advisory + human review only. Never an automated response. |
   | Dress code | ≥ 85 % precision | — | A false accusation against a student is the expensive error, so precision wins. |
   | Cross-camera Re-ID | ≥ 75 % correct re-association | ≤ 10 % ID swaps | ID swaps mislead an investigation worse than a missing match does. |

3. **An automated regression test** locks in the tuned thresholds.
4. **A named human signs off** on the number — the metric is not self-approving.

Anything safety-adjacent (fall, violence, bullying) stays **advisory with human review** even after
promotion. We do not ship an automated response to a heuristic.

---

## Two mechanisms worth building

1. **A real `BETA` badge in the dashboard.** Right now the tiering lives only in this file, which
   means in a live demo it depends on us remembering to say it. Put the badge next to Beta features
   in the nav and on their result cards.
   → **Done:** [backend/index.html](backend/index.html) now renders a `Beta` badge next to the AI
   Security Summary, AI Intent Search, and the Falls / Running / Dress-code search chips, with a
   tooltip explaining that accuracy is under validation.
2. **Reuse license feature codes as the tier switch.** [license_validator.py:45](license_validator.py:45)
   already gates `fall_detection`, `running_detection`, `dress_code`, etc. per client. That's
   exactly the right hook to ship a client "Production-only" license and enable Beta features
   per-site once they're validated — no code fork.
   → **Done:** [license_validator.py](license_validator.py) now exposes `EDITION_BUNDLES`
   (`basic` / `premium` / `pro`) and a `get_edition_features(edition)` helper. Licenses issued for
   an edition include exactly that bundle's feature codes.

---

## Marketing editions ↔ features

The customer-facing edition matrix and Basic/Premium/Pro breakdown live in
[PRICING_TIERS.md](PRICING_TIERS.md). The bundles it references are machine-defined in
`license_validator.EDITION_BUNDLES`:

| Edition | Feature codes | Status of contents |
|---|---|---|
| **Basic** | `core_tracking`, `zone_alerts`, `notifications`, `reports`, `api_access` | All Production. |
| **Premium** | Basic + `loitering`, `line_crossing`, `occupancy_alerts`, `running_detection`, `fall_detection`, `dress_code` | Loitering, line-crossing, occupancy are Production; running / fall / dress-code are Beta. |
| **Pro** | Premium + `pose_verification`, `violence_detection`, `ai_summary`, `nl_search`, `cross_camera_reid`, `investigation_graph` | All Pro-tier additions are Beta today. |

`occupancy_alerts` was promoted from Not-in-demo → Production on the same commit that wired it
into the live frame path; its safety property is that a zone without an explicit `max_occupancy`
is silent.
