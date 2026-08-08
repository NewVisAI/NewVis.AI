# Sentinel — Test Plan v1 (pre-client-demo)

Companion to [FEATURE_STATUS.md](FEATURE_STATUS.md). That file says *what we claim*; this one says
*how we prove it*.

Goal: get the ① Production tier to "tested, evidenced, demo-ready" — and set up the measurement
harness so ② Beta features can be promoted with numbers instead of vibes.

**Environment for every phase** (from [CLAUDE.md](CLAUDE.md)):

```bash
wsl bash -lc 'cd /mnt/d/COLLEGE/Sentinel && DISABLE_AI_ENGINE=1 SENTINEL_DB_PATH=/root/sentinel_data/cctv_logs.db ./.venv/bin/python -m uvicorn backend.server:app --host 0.0.0.0 --port 8000'
```

Both env vars are load-bearing. `MOTION_GATING`, `USE_SUBSTREAM`, `DECODE_BACKEND`, and
`REID_DEFERRED` must all be **unset** during acceptance testing — we test the honest path first.

---

## Phase 0 — Unblock: get the suite green ⚠️ START HERE

The suite is **not green today**. Current state, measured 2026-07-29:

```
Ran 32 tests in 76s
FAILED (failures=2)
```

Both failures are cross-camera Global-ID reuse:

- [test_reid.py:172](test_reid.py:172) `test_same_object_across_cameras_reuses_global_id` — `1 != 2`
- [test_reid.py:226](test_reid.py:226) `test_match_updates_last_seen_time_and_camera` — `1 != 2`

Both expect the same person on camera 2 to reuse the global ID from camera 1, and get a fresh ID
instead. **Triage first — the fix depends on which of these it is:**

| Hypothesis | How to tell | If true |
|---|---|---|
| Test fixture problem — synthetic crops don't produce meaningful OSNet embeddings, so similarity legitimately falls below threshold | Log the actual cosine similarity in the failing test and compare to `REID_SIMILARITY_THRESHOLD` | Fix the fixture (use real crops from `reid_crops/`); the product is fine |
| Real regression — matching logic or threshold broke | Same similarity log shows a *high* score that still didn't match | Product bug. Cross-camera Re-ID cannot be demoed until fixed |

Either way: **do not demo cross-camera Re-ID until this is resolved.** It's the single most
impressive feature in the product and also the least trustworthy right now.

```bash
wsl bash -lc 'cd /mnt/d/COLLEGE/Sentinel && ./.venv/bin/python -m unittest test_reid -v 2>&1 | tail -40'
```

**Exit criteria:** 32/32 green, and the root cause written down in this file (not just "fixed").

---

## Phase 1 — Automated regression for the Production tier

The whole point of tiering something Production is that it stays working. Today the deterministic
core has **no test coverage** — the suite covers licensing, fall, re-id, and the event graph, and
nothing else. Every test below is cheap, because every feature is deterministic: synthesize a track,
assert the row that lands in the DB.

New files, in priority order:

| # | File | Covers | Key assertions |
|---|---|---|---|
| 1 | `test_zones.py` | zone entry/exit, dwell | Point inside/outside/on-edge polygon; entry then exit logs both; dwell accumulates; zone auto-scale at `PROCESS_WIDTH=1280` matches full-res coords |
| 2 | `test_alerts.py` | restricted, after-hours, holiday | Restricted zone fires; normal zone does not; after-hours boundary (one minute either side); holiday from [school_calendar.py](school_calendar.py) fires; snapshot file written; notification row created unread |
| 3 | `test_line_counter.py` | in/out counts | L→R increments in, R→L increments out; a track that stops on the line doesn't double-count; `flush()` persists |
| 4 | `test_calendar.py` | school-day logic | Weekend, holiday, term boundary, mid-term weekday |
| 5 | `test_reports.py` | aggregation | Headcount series buckets correctly; heatmap accumulates to expected cells; empty range returns empty, not an error |
| 6 | `test_rbac.py` | access control | Each role reaches its own endpoints and gets 401/403 on others — via FastAPI `TestClient`, no camera needed |

Conventions: `unittest` (not pytest), tests live in the repo root next to the existing ones.

```bash
wsl bash -lc 'cd /mnt/d/COLLEGE/Sentinel && ./.venv/bin/python -m unittest test_fall test_licensing test_reid test_event_graph test_zones test_alerts test_line_counter test_calendar test_reports test_rbac'
```

**Exit criteria:** all green, and each ① row in [FEATURE_STATUS.md](FEATURE_STATUS.md) names its
test file instead of saying "no automated test".

---

## Phase 2 — Manual acceptance walkthrough

Unit tests prove the logic; this proves the *product*. Run it end-to-end on a real camera and
capture evidence as you go — the evidence doubles as demo material.

Record results in the table at the bottom of this file. One row per case: Pass / Fail / Blocked,
plus the evidence file.

| ID | Feature | Steps | Pass criteria | Evidence to capture |
|---|---|---|---|---|
| A1 | Login + RBAC | Log in as each of developer / tech / principal | Nav shows only that role's items; a direct `#/admin` URL as principal redirects to overview | Screenshot per role |
| A2 | Live camera | Cameras tab | Stream renders < 5 s, no "stream unavailable" | Screenshot |
| A3 | Analytics start | Analytics tab → camera → Run live analytics | Boxes + track IDs drawn; FPS visible; no crash over 10 min | Screen recording, 30 s |
| A4 | Person count | Walk 1, then 3 people through view | Count matches ground truth | Screenshot + noted actual |
| A5 | Zone entry | Draw a zone, walk into it | Entry event logged with correct zone + timestamp | Screenshot + DB row |
| A6 | Restricted alert | Mark zone restricted, walk in | Alert fires; notification appears unread with snapshot | Screenshot of inbox |
| A7 | After-hours | Set hours so now is "after hours", walk in | After-hours alert fires | Screenshot |
| A8 | Holiday | Add today as holiday, walk in | Holiday alert fires | Screenshot |
| A9 | Snapshot evidence | Open any alert's snapshot | Red box on the right person at the right moment | The snapshot itself |
| A10 | Jump to footage | Alert → view clip | Clip opens at the alert moment ± 2 s | Screen recording |
| A11 | Loitering | Stand still in a zone 20 s (threshold 15 s) | Loiter flagged once, not repeatedly | Screenshot |
| A12 | Line crossing | Cross a line 3× each direction | Counts read 3 in / 3 out exactly | Screenshot |
| A13 | Heatmap | After ~10 min of activity | Hot region matches where people actually walked | Screenshot |
| A14 | Headcount chart | Reports tab | Curve matches observed occupancy over the session | Screenshot |
| A15 | Audit log | Audit tab after all of the above | Every action above appears with correct user + IP | Screenshot |
| A16 | Restart survival | Restart uvicorn, reload | All events/alerts/zones still present | Screenshot |
| A17 | 60-min soak | Leave one camera running 60 min | No crash, no memory climb, no FPS decay | Log tail + before/after `free -m` |

Beta features get walked through too, but under a different bar — **"does it run and produce a
plausible result"**, not "is it accurate". Log what fires and how often; that becomes the baseline
false-alarm rate for Phase 3.

| ID | Feature | Note what happened |
|---|---|---|
| B1 | Fall | Controlled lie-down (safely, on a mat). Does it fire? Latency? |
| B2 | Running | Jog through view. Fires? Does brisk walking also fire (false positive)? |
| B3 | Violence | Two people mock-scuffle. Fires? How many normal-interaction false alarms in the hour? |
| B4 | Dress code | Note that it's comparing against 3 hardcoded colours — record what it flags |
| B5 | AI search | 10 prepared queries + 5 deliberately off-script | Which returned nothing → the coverage list |
| B6 | AI summary | Check it describes the **last 15 min**, not stale alerts (the bug just fixed) |
| B7 | Cross-camera | **Only if Phase 0 resolved.** Walk camera 1 → camera 2, check Global ID holds |

**Exit criteria:** every A-case Pass (or a filed issue for each Fail), and a baseline false-alarm
count per Beta feature over ≥ 1 hour of normal footage.

---

## Phase 3 — Accuracy measurement (unlocks Beta promotion)

This phase is **blocked on real footage** and is what turns Beta into Production. Set it up now so
that footage arriving is the only missing input.

**Footage needed, per the promotion gate:** ≥ 30 true events per feature, plus ≥ 2 h of normal
footage, from this site's cameras at real mounting angles. Staged events are acceptable for falls
and running (safety and practicality) as long as they're staged *at the real camera angles* —
angle, not authenticity, is what breaks these heuristics.

**Labelling format** — one CSV per clip set, no tooling needed:

```csv
clip_path,event_type,start_sec,end_sec,camera_id,notes
```

**Harness:** `accuracy_eval.py` (to build) — replays labelled clips through the live pipeline,
matches fired alerts against labels with a ± 2 s tolerance window, emits per-feature
precision / recall / false-alarms-per-hour. Same pattern as
[validate_quantization.py](validate_quantization.py): prove parity on this box, defer throughput to
the GPU box.

**Also here (needs the GPU laptop, per [GPU_LAPTOP_RUNBOOK.md](GPU_LAPTOP_RUNBOOK.md)):** run
`gpu_benchmark.py` to settle decode-bound vs inference-bound. That decides whether cross-camera
batching (lever ⑤) is worth building at all — a separate question from accuracy, same trip.

---

## Phase 4 — Client presentation

- **One-pager**: Production features (with evidence screenshots from Phase 2), Beta features marked
  Beta, roadmap as roadmap. Derive it from [FEATURE_STATUS.md](FEATURE_STATUS.md) so there's one
  source of truth.
- **Demo script**: fixed order, rehearsed. A2 → A3 → A5 → A6 → A9 → A10 → A12 → A13 → A14, then
  Beta features explicitly introduced as Beta.
- **The footage ask**: the demo is also how we ask for what Phase 3 needs. Come with the specific
  list — cameras, hours, event types, and why staged events at real angles are fine.
- **Don't** demo: cross-camera Re-ID (until Phase 0 lands), motion gating, anything from ③.

---

## Results log

Fill in as Phase 2 runs. Keep it in the repo — it's the evidence trail.

| ID | Date | Result | Evidence | Notes |
|---|---|---|---|---|
| | | | | |
