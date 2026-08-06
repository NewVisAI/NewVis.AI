# Report — Compute/Cost Levers Added 2026-08-05

Two accuracy/speed changes landed today under the constraint **"improve quality, accuracy
and fastness without increasing computational cost"**, plus supporting repo hygiene. Both
levers are **flag-gated, off by default, Beta** until on-site footage validates them — the
same discipline as the existing cost-optimization levers.

Grounded in delta-change research: **DeltaCNN** (CVPR 2022) — sparse frame-difference CNN
inference, up to 7× on GPU but CUDA-only — and CPU-friendly **motion-region gating**.

---

## Lever #1 — Motion-delta skip  (compute REDUCTION)

**Flag:** `MOTION_DELTA=1` · **Files:** `inference_config.py`, `live_analytics.py`

### What it does
On an **idle** camera (person-gate has seen nobody for the hangover window), the analytics
loop skips the detector pass on a frame that barely changed from the last processed one —
decided by a tiny grayscale frame-difference. It exploits the temporal redundancy that
DeltaCNN uses, at the frame level and **CPU-only**.

### How it saves compute
- On a **static empty scene** the detector goes near-silent instead of running at the idle
  rate. The steady-state cost per idle frame drops from *one full YOLO pass* to *one tiny
  frame-diff*.
- **Measured on the dev CPU box:** the motion-delta check costs **~0.16 ms/frame**. A full
  YOLO detector pass is orders of magnitude larger, so **every skipped idle frame is ~pure
  saving** (the check pays for itself thousands of times over on each skip).
- The saving scales with **how empty the cameras are**. On a campus, hallways at night,
  empty classrooms between periods, and after-hours cameras are idle for the majority of
  camera-hours — exactly where inference was previously being spent on nothing.

### Cost translation
Less inference per idle-camera-hour → fewer GPU-hours for the same camera count → either
**fewer/cheaper GPU nodes** or **more cameras per node**. For the 300-camera school in the
existing cost model, idle-heavy cameras are a large fraction of total camera-hours, so this
compounds with levers ①–③. **Dollar figures are deferred to on-site measurement** (per
convention — no fabricated numbers).

### Why it can't cause a missed fall (safety)
1. **Idle-gate only.** The skip runs only while the person-gate is idle. YOLO detects a
   motionless/fallen person, which holds the gate *active*, so a populated scene is never
   skipped.
2. **Full-scan heartbeat** (`MOTION_DELTA_FULLSCAN_S`, default 4 s) forces a real detector
   pass periodically, bounding how long a low-motion newcomer waits. Locked by a unit test.

---

## Lever #2 — Event-gated pose verification for falls  (accuracy at ~ZERO added compute)

**Flag:** `POSE_VERIFY=1` · **Files:** `inference_config.py`, `pose_verify.py`,
`fall_detector.py`, `app.py`

### What it does
When the cheap bbox fall heuristic newly fires, a pose model runs **once, on that person's
crop only**, and attaches a keypoint-based confidence (`pose_verified`, `pose_confidence`)
that the torso is really horizontal. It catches falls **toward/away from the camera** that
the bbox aspect-ratio test misses.

### How it stays compute-neutral
- Pose runs **only on the rare fall event**, not every frame → **average added compute ≈ 0**.
- This is a deliberate **cost avoidance**: the naive alternative — running a second pose model
  on *every* frame — would have raised steady-state detection cost substantially (a whole
  extra network per frame per camera). Event-gating buys the accuracy without that bill.
- Uses the **ultralytics stack already loaded** (`yolov8n-pose.pt`, same 17 COCO keypoints as
  the Coral PoseNet repo) — **no new runtime, no Coral hardware.** Real model verified to load
  and return the expected `(n,17,3)` keypoints.

### Safety
Pose **never suppresses** a fall (that would risk a missed fall). It only annotates; on
disagreement or error the alert still fires. Two unit tests lock this.

### Rejected alternative
`google-coral/project-posenet` — archived Nov 2023, **requires Coral Edge TPU hardware**,
foreign tflite/pycoral runtime, dated MobileNet-V1, and running pose every frame would have
*increased* compute. Capability adopted via YOLOv8-pose instead.

---

## Combined impact

| Lever | Steady-state compute | Accuracy | Cost direction |
|---|---|---|---|
| #1 Motion-delta | **Down** (skips idle-scene inference; 0.16 ms check) | Neutral (lossless on populated scenes) | **Saves** GPU-hours ∝ camera idleness |
| #2 Pose-verify | **~Flat** (fires only on falls) | **Up** (catches axial falls, confidence signal) | **Avoids** cost of always-on pose |

Net: quality/accuracy up, steady-state compute down — meeting the no-added-compute constraint.
Hard FPS/TCO numbers are a **GPU-box / on-site** measurement (deferred by convention).

---

## Verification
- **23/23 unit tests pass**: `test_motion_delta` (8), `test_pose_verify` (10), `test_fall`
  regression (5). All touched files compile; real pose model smoke-tested end-to-end.
- Validation gates documented in `docs/MOTION_DELTA.md` and `docs/POSE_VERIFY.md`
  (parity / safety / benefit steps to run before raising either out of Beta).

## Files
- **Modified:** `inference_config.py`, `live_analytics.py`, `fall_detector.py`, `app.py`
- **New:** `pose_verify.py`, `test_motion_delta.py`, `test_pose_verify.py`,
  `docs/MOTION_DELTA.md`, `docs/POSE_VERIFY.md`, this report.

## Also done today (non-code)
- Fixed and pushed READMEs/descriptions across the public `dhxvxn` repos (broken links,
  boilerplate, duplicate READMEs, profile bio typos).
- Ran the `SentinelAI-CCTV` backend and the main Sentinel dashboard locally to verify.
- Repo hygiene: removed session scratch scripts; confirmed `yolov8n-pose.pt` is gitignored
  (`*.pt`).

## Not yet done (planned)
- **#3 Quantization** (FP16/INT8 via existing `quantize.py` tooling) — free speed at parity.
- **#4 DeltaCNN / MotionDeltaCNN** — up to 7× but CUDA-only; GPU-box experiment.
