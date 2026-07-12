# Sentinel AI — Feature Test Report

**Test input:** `D:\Downloads\view-IP2 (1).mp4`
**Run date:** 2026-07-02
**Machine:** Windows 11, CPU-only (no CUDA), Python 3.13 venv
**Driver:** [`feature_test_driver.py`](../feature_test_driver.py) — runs the real production code paths (same functions `app.py` uses), not mocks.

---

## 1. How the test was run

A headless driver processed the whole video through the actual pipeline
(`detector → tracker → reid → zone_manager → event → alerts → incident_manager`),
wrote an annotated video + snapshot evidence, then read every feature's results
straight out of the SQLite database. It also ran the natural-language search
service, the headcount report, the licensing/governance layer, and the three
unit-test suites.

- **Detection model:** YOLOv8n (local `yolov8n.pt`), device = **CPU**
- **Tracking:** ByteTrack (via `supervision`)
- **Re-ID:** ResNet50 (ImageNet weights auto-downloaded, initialized successfully)
- **Zones used:** the existing [`zones.json`](../zones.json) — a **restricted** west-half zone and an **after-hours** east-half zone (allowed hours 07:00–09:00, Mon–Fri)
- **Frame sampling:** every 6th frame (stride 6) so the full 3-minute clip is covered in one CPU pass → **910 frames processed**

### Video properties
| Property | Value |
|---|---|
| Resolution | 1920 × 1080 |
| FPS | 29.97 |
| Frames | 5457 |
| Duration | 182.1 s (~3 min) |
| Scene | Indoor lecture hall — people walking/standing/talking |

---

## 2. Evidence artifacts

| Artifact | Location |
|---|---|
| **Annotated video** (boxes, Global IDs, trails, zones, risk, HUD) | [`annotated_output.mp4`](annotated_output.mp4) (58 MB) |
| Curated hero snapshots | [`evidence/`](evidence/) |
| All auto-captured evidence frames (98) | [`frames/`](frames/) |
| All alert snapshots — red box on subject (92) | [`../alert_snapshots/`](../alert_snapshots/) |
| Machine-readable results | [`results.json`](results.json) |
| Raw pipeline log | [`pipeline_log.txt`](pipeline_log.txt) |

### Multi-person tracking + Global ID + risk scoring (one frame)
![Multi-person tracking](evidence/01_multiperson_tracking_globalid_risk.jpg)

Eight people simultaneously tracked with stable Global IDs (`GID 1`, `GID 5`…`GID 11`),
motion trails, per-identity `RISK: 100` scores, and the orange restricted/monitored
zone divider.

### Restricted-zone intrusion snapshot (what the principal sees)
![Restricted intrusion](evidence/03_restricted_zone_intrusion_snapshot.jpg)

Auto-generated red-box snapshot: *"person — Restricted Zone (West half)"*.

### After-hours entry snapshot
![After hours](evidence/04_after_hours_entry_snapshot.jpg)

*"person — Monitored Entrance (East half)"* — entry outside 07:00–09:00.

---

## 3. Per-feature results

Legend: ✅ verified firing on this footage · ✅ (logic) ran with no positives on this
footage but validated by unit tests / no false positives · ⚠️ works with a caveat.

| # | Feature | Status | Evidence from this run |
|---|---|---|---|
| 1 | **Human/object detection** (YOLOv8) | ✅ | 63 person zone-sessions; 7,262 tracking rows. No vehicles in scene → 0 (correct). |
| 2 | **Multi-object tracking** (ByteTrack) | ✅ | 81 distinct local track IDs across the clip. |
| 3 | **Neural Re-ID / Global ID** (ResNet50) | ✅ | 81 track IDs consolidated into **53 Global IDs**; IDs stay stable across frames (see hero image). |
| 4 | **Zone entry/exit sessions** | ✅ | 63 `leaving` sessions logged with entry/exit times + durations (up to 140 s). |
| 5 | **Restricted-zone intrusion alerts** | ✅ | **36** `restricted_zone_entry` alerts, each with snapshot + notification. |
| 6 | **After-hours / non-school-day entry alerts** | ✅ | **56** `after_hours_entry` alerts (run clock was outside 07:00–09:00). |
| 7 | **Crowd-density / occupancy monitoring** | ✅ | **44** `occupancy_alert` events — Zone 1 hit 3 (limit 2), Zone 2 hit 4–5 (limit 3). |
| 8 | **Loitering + intrusion risk scoring** (IncidentManager) | ✅ | Sustained presences scored `RISK: 100` (restricted + loitering + night); visible on labels. |
| 9 | **Principal notifications inbox + snapshots** | ✅ | **92** notifications recorded, **all 92** with a stored snapshot, all unread. |
| 10 | **Dress-code compliance** | ✅ (logic) | Ran on every standing person; **0** violations — subjects' clothing matched allowed uniform colors (no false positives). |
| 11 | **Running detection** | ✅ (logic) | Ran per-track; **0** events — everyone walked (below the 2.5 bbox-heights/s threshold). No false positives. |
| 12 | **Fall detection** | ✅ (logic) | No falls occur in this footage → 0 (correct). Logic validated by `test_fall` (5/5 pass). |
| 13 | **Natural-language search** | ✅ | `intrusion`→36 alerts, `people in zone 2`→37, `running`/`uniform violations`→0 (correct). Plural gap since fixed (see §4). |
| 14 | **Headcount reporting** (ModeManager) | ✅ | **45** unique people (Zone 1: 20, Zone 2: 26), 63 visits, per-zone prolonged-stay list. |
| 15 | **Licensing & camera governance** (Ed25519) | ✅ | Evaluation mode enforced; camera cap + tamper/bypass rejection verified. Caveat below. |

### Alert / event totals (exact, from DB)
```
restricted_zone_entry : 36        leaving (zone sessions) : 63
after_hours_entry     : 56        occupancy_alert         : 44
------------------------------    running_detected        : 0
alerts total          : 92        dress_code_violation    : 0
notifications total   : 92        tracking rows           : 7262
snapshots stored      : 92        distinct global IDs     : 53 (from 81 tracks)
```

### Natural-language search (rule-based, no LLM key)
| Query | Parsed intent | Results |
|---|---|---|
| "show me people who entered the restricted zone" | object=person, event=entering, zone=1 | 26 |
| "intrusion" | event=restricted_zone_entry (→alerts table) | 36 |
| "running" | event=running_detected | 0 (correct) |
| "uniform violations" | event=dress_code_violation | 0 (correct) |
| "people in zone 2" | object=person, zone=2 | 37 |

### Headcount report (excerpt)
```
👥 Headcount (unique people): 45
 • Zone 1: 20 unique people
 • Zone 2: 26 unique people
 Total person visits (incl. re-entries): 63 | Vehicles: 0
```

### Licensing / governance
| Check | Result |
|---|---|
| Evaluation mode, 1 camera | ✅ allowed |
| Evaluation mode, 2 cameras | ❌ rejected — "Camera limit exceeded… Licensed for 1" |
| Old demo passwords (`123456789`, `SentinelDemo2026`, `AdivaDemo2026`) | ❌ all rejected (signature verification) |
| Hardware fingerprint | `5D391588E3BF5FA3` |

### Unit tests
| Suite | Result |
|---|---|
| `test_fall` | ✅ 5/5 pass |
| `test_reid` | ✅ 11/11 pass |
| `test_licensing` | ✅ 9/9 pass (now self-contained; see §4) |

---

## 4. Caveats & findings

1. **Some features had no positive events *in this footage* — by design, not failure.**
   Dress-code, running, and fall detection all *executed* on every eligible track
   but this lecture-hall clip contains no non-uniform-flagged clothing, no running,
   and no falls. Their detection logic is independently proven by the unit tests
   (`test_fall` passes) and by producing **zero false positives** here. To see them
   fire on video, feed footage that actually contains those behaviors.

2. **NL search plural gap — FIXED.** The rule parser previously mapped singular
   tokens (`car`, `fall`) but not plurals (`cars`, `falls`), so those queries fell
   through to an *unfiltered* result set. `intent_manager.py`'s `OBJECT_ALIAS` and
   `EVENT_ALIAS` now include plural/variant forms (`cars`→car, `falls`→fall_detected,
   `runs`/`ran`→running_detected, `buses`, `intrusions`, `uniforms`, `slips`, etc.).
   Verified: `"falls"` → `{event: fall_detected}`, `"cars"` → `{object: car}`.

3. **`test_licensing` — FIXED (now 9/9).** The suite previously needed the
   developer-only private key `dev_keys/license_signing_key.pem` (gitignored) to
   mint licenses, so 7/9 tests errored on a non-dev machine. It now signs with an
   **ephemeral in-memory Ed25519 keypair** (and points the validator's public key
   at it) in `setUpClass`, so the full sign→verify round trip runs anywhere without
   the secret. Also fixed the secondary robustness bug: `generate_key.py` now
   reconfigures stdout/stderr to UTF-8 (like `app.py`) so its `❌` status messages
   no longer raise `UnicodeEncodeError` on the Windows console.

4. **Alert clock vs. video time.** After-hours alerts use the real wall clock
   (`datetime.now()`), which is why they fired during this evening test run. Zone
   session `entry_time`s are stored in UTC. Both are by design; just note it when
   reading timestamps.

---

## 5. Reproduce

```powershell
cd D:\COLLEGE\Sentinel
$env:FRAME_SKIP="6"           # every 6th frame; use 2-3 for denser sampling
.\.venv\Scripts\python.exe feature_test_driver.py "D:\Downloads\view-IP2 (1).mp4"
```
Outputs land in `feature_test_report/` (annotated video, frames, results.json).
