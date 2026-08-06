# Motion-delta skip (temporal-redundancy compute lever)

**Flag:** `MOTION_DELTA=1` (off by default) · **Tier:** Beta until validated on site footage
**Tuning:** `MOTION_DELTA_THRESH` (default `0.002`), `MOTION_DELTA_FULLSCAN_S` (default `4.0`)

## What it does
On an **idle** camera (the adaptive person-gate has seen nobody for the hangover window),
the analytics loop skips the detector pass on a frame whose content is essentially unchanged
from the last processed frame. It reuses the temporal redundancy that DeltaCNN-style methods
exploit, but at the **frame level and CPU-only** — no model change, no new dependency.

On a static empty scene the detector goes near-silent instead of running at the idle rate; the
per-frame cost drops to a tiny grayscale frame-diff (~0.16 ms on the dev box) instead of a full
YOLO pass. This is the compute-*reducing* half of the accuracy/speed work.

## Where it lives
- `inference_config.py`: `motion_delta()`, `motion_delta_thresh()`, `motion_delta_fullscan_s()`
  (env var > `deployment.json` > default, same pattern as every other knob).
- `live_analytics.py`: helpers `_delta_downscale()` / `_frame_change_ratio()`, and the skip block
  in `_analytics_loop` (right after the `#13` frame-dedup skip).

## Why it can't cause a missed fall (safety)
Two guards, both enforced in `_analytics_loop`:
1. **Idle-gate only.** The skip runs *only* while `gate == "idle"`. YOLO detects a motionless or
   fallen person, which keeps `last_person_count > 0` and holds the gate **active**, so a
   populated scene is never delta-skipped — the fall pipeline runs on it normally.
2. **Full-scan heartbeat.** Even on a static idle scene, a full detector pass is forced at least
   every `MOTION_DELTA_FULLSCAN_S`, bounding how long a low-motion newcomer can go undetected
   (mirrors motion gating's idle heartbeat). Verified by
   `test_motion_delta.MotionDeltaLoopSimulation`.

## Validation gate (do before claiming any number / raising tier)
1. **Correctness — parity:** on representative site footage, run the pipeline with `MOTION_DELTA=0`
   vs `MOTION_DELTA=1` and confirm the **same events/tracks** are produced (skips only ever fall on
   genuinely empty frames). Any divergence means `MOTION_DELTA_THRESH` is too high — lower it.
2. **Safety:** include a clip where a person enters with minimal motion (walks in slowly / partially
   occluded) and confirm detection latency ≤ `MOTION_DELTA_FULLSCAN_S`.
3. **Benefit — compute:** record avg ms/frame and `delta_skipped` (exposed in `live_analytics`
   `snapshot()`) on a mostly-idle camera; the win scales with how empty the scene is.
4. Real throughput/FPS numbers are a **GPU-box / on-site** measurement, per project convention.

## Status counters
`live_analytics` `snapshot()` now reports `motion_delta` (on/off) and `delta_skipped` (detector
passes avoided) alongside the existing `dup_frames`.
