# Testing Footage — Beta Safety Trio

**Purpose:** turn the "wired live, accuracy unknown" Beta features
(fall, running, altercation) into measured numbers so they can be promoted
Beta → Production under the [FEATURE_STATUS.md](FEATURE_STATUS.md#promotion-gate-beta--production)
gate. Unit tests already lock in the logic; this doc is the missing
"and now score it on real footage" step.

Nothing in this file changes code. It tells you what to collect, how to run
it, and what number qualifies as a pass.

---

## What to collect

For each feature we need **≥ 30 positive-event clips** and **≥ 2 hours of
normal (nothing-happened) footage** — the Promotion Gate minimum. Positives
prove recall; the long "quiet" tail proves the false-alarm rate.

Everything below should be recorded at the real mounting angle you plan to
deploy (hallway, courtyard, cafeteria). A ceiling clip and a wall-mount clip
of the same event are two different clips — the aspect-ratio heuristic reads
them differently.

### Fall detection (target: recall ≥ 90 %, ≤ 1 FP/camera/day)

**Positives (30 clips minimum):**

| Sub-case | Why it matters | Target count |
|---|---|---|
| Sideways fall in view | Baseline — the bbox flips aspect ratio; this is the "easy" case | 10 |
| Fall toward / away from camera | Bbox never flips; POSE_VERIFY should catch it | 8 |
| Trip + immediate roll | Tests the stillness-refuted path | 4 |
| Fall then person gets up in < 2s | Stillness refuted; confidence should be "low" | 4 |
| Fall then person stays down > 5s | Stillness confirmed; confidence should be "high" | 4 |

**Negatives (2 h minimum):** normal hallway/classroom footage plus:
- 5+ clips of a person sitting down abruptly (aspect ratio dips toward horizontal)
- 5+ clips of a person crouching / tying shoelaces
- 5+ clips of a group hugging / mock-wrestling that is NOT a fall

Sources to try: [UP-Fall Detection Dataset](http://sites.google.com/up.edu.mx/har-up/),
[Le2i Fall Detection Dataset](https://search.dataone.org/view/doi%3A10.5061%2Fdryad.pt7cb),
your own phone-camera re-enactments.

### Running detection (target: recall ≥ 80 %, ≤ 5 FP/camera/day)

**Positives:**

| Sub-case | Target count |
|---|---|
| Steady sprint across the frame (5+ steps visible) | 10 |
| Short burst run (2-3 steps then walk) | 8 |
| Run toward / away from camera (foreshortened) | 6 |
| Group running together | 6 |

**Negatives:**
- 5+ clips of brisk walking (should NOT fire)
- 5+ clips of cyclists / skateboarders passing (bbox motion, not human running)
- Clips at frame edges where bbox distorts — verify edge suppression works

### Altercation / violence (target: recall ≥ 70 %, ≤ 2 FP/camera/day)

**Positives:**

| Sub-case | Target count |
|---|---|
| Two-person shoving (clear direction reversals) | 12 |
| Grappling / clinch | 6 |
| One-sided attack (aggressor moves; victim mostly still) | 6 |
| Fight with 3-4 bystanders in frame | 6 |

**Negatives:**
- 5+ clips of two friends walking together in step (co-directional)
- 5+ clips of one person fast-passing another (no direction reversal)
- 5+ clips of a hug / greeting (proximity, low relative motion)
- 5+ clips of a sports game viewed at a distance
- 5+ clips of a person weaving through a crowd

Sources to try: [RWF-2000](https://github.com/mchengny/RWF2000-Video-Database-for-Violence-Detection)
(500 fight / 500 non-fight clips, YouTube-sourced), [Hockey Fight Dataset](http://academictorrents.com/details/38d9ed996a5a75a039b84cf8a137be794e7cee89).

---

## How to run the scoring pass

There is no dedicated scoring script yet — for this pass, drive the
existing headless entrypoint over each clip and read the events it wrote to
the DB. This is deliberately the smallest possible harness.

```powershell
# 1. Set the run-time knobs consistently for the whole pass, so every clip
#    is evaluated under the same policy (no accidental drift from a stale
#    deployment.json).
$env:DISABLE_AI_ENGINE = "1"
$env:POSE_VERIFY = "1"                 # already default-on, but be explicit
$env:ANOMALY_MIN_DURATION_S = "2.0"
$env:ANOMALY_OSCILLATION_MIN_FLIPS = "2"
$env:RUNNING_SUSTAINED_SAMPLES = "3"
$env:RUNNING_DIRECTION_CHECK = "1"
$env:RUNNING_EDGE_MARGIN_PCT = "0.05"

# 2. For each clip, clear the events DB then run headless.
foreach ($clip in Get-ChildItem tests/footage/falls/*.mp4) {
    .\.venv-win\Scripts\python.exe -c "from event import clear_event_logs; clear_event_logs()"
    .\.venv-win\Scripts\python.exe app.py --headless --video $clip.FullName
    # Read fall events for this clip; write one row to a CSV.
    .\.venv-win\Scripts\python.exe scripts/score_clip.py --clip $clip.Name --event-type fall_detected --expected 1
}
```

`scripts/score_clip.py` does not exist yet — writing it is a follow-up. For
now, query the DB manually:

```powershell
.\.venv-win\Scripts\python.exe -c "from db_schema import connect_db; c=connect_db(); print(c.execute(""SELECT event_type, video_time, video_path FROM events WHERE event_type IN ('fall_detected','running_detected','violence') ORDER BY video_time"").fetchall())"
```

For each clip, note:
- **TP** if the alert fired at roughly the right video_time (within ± 2 s of the ground-truth event)
- **FN** if the alert did not fire on a positive clip
- **FP** if the alert fired on a negative clip

Then compute per feature:
- Recall = TP / (TP + FN) — must clear the target
- False alarms per camera per day = FP / (hours of negative footage) × 24 — must be under the target

### Recording the numbers

Log every clip's outcome in [TEST_PLAN.md](TEST_PLAN.md) under a new
"Footage-scored accuracy" section, one row per clip. Once ≥ 30 positives
are labelled and both targets pass, the feature can be promoted in
[FEATURE_STATUS.md](FEATURE_STATUS.md).

---

## Tuning knobs to try if the numbers miss the target

Every knob below resolves via env → deployment.json → default. Change one
at a time, re-run the pass, record the delta.

### Fall
- `POSE_VERIFY=0` to isolate the bbox-only recall (if pose is hurting more than helping in your environment)
- `FALL_STILLNESS_SECONDS=2.0` to confirm faster (more "low" confidences, fewer "high")
- `FALL_STILLNESS_MOVEMENT_RATIO=0.5` to be stricter about what counts as still

### Running
- `RUNNING_SPEED_THRESHOLD` — lower to increase recall (more false positives on brisk walkers)
- `RUNNING_SUSTAINED_SAMPLES=2` to fire on shorter bursts
- `RUNNING_DIRECTION_CHECK=0` if the direction gate is killing legitimate runs at odd camera angles
- `RUNNING_EDGE_MARGIN_PCT=0` to disable edge suppression entirely (verify with edge-tracking positives)

### Altercation
- `ANOMALY_REL_MOTION` — lower for gentler jostles, higher to reject casual roughhousing
- `ANOMALY_MIN_DURATION_S` — the strongest false-alarm gate; raise to 3-4s in a busy hallway
- `ANOMALY_OSCILLATION_MIN_FLIPS` — 3 or 4 for very-clean shoving in a chaotic environment
- `RAISED_ARMS_CHECK=1` to enable the optional pose augment (doubles pose cost per event; measure)
