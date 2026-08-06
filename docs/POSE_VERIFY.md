# Event-gated pose verification for falls (accuracy lever)

**Flag:** `POSE_VERIFY=1` (off by default) · **Tier:** Beta until validated on site footage
**Tuning:** `POSE_VERIFY_WEIGHTS` (default `yolov8n-pose.pt`), `POSE_VERIFY_THRESH` (default `0.5`)

## What it does
When the cheap bbox fall heuristic (`fall_detector.check_fall`) newly detects a fall, a pose
model runs **once, on that person's crop only**, and attaches a keypoint-based confidence that
the person is genuinely in a fallen (torso-horizontal) posture. It emits three extra fields on
the fall details: `pose_verified` (bool | None), `pose_confidence` (0..1), `pose_reason`.

Because pose fires **only on the rare fall event** (not every frame), average added compute is
~zero — this satisfies the "improve accuracy without adding compute" constraint. It also catches
falls **toward/away from the camera**, where the bbox aspect-ratio never flips horizontal but the
keypoint torso vector still does.

## Safety: it never suppresses a fall
A safety system must not drop a real alert because pose disagreed. So pose only **augments** the
details — it never turns a detected fall into a non-event. On pose disagreement the alert still
fires (tagged `pose_verified=False`); on any pose error or no-detection it returns
`pose_verified=None` and the alert is unaffected. Verified by
`test_pose_verify.CheckFallPoseIntegration` (`..._never_suppresses`, `..._error_never_breaks_fall`).

## Where it lives
- `inference_config.py`: `pose_verify()`, `pose_verify_weights()`, `pose_verify_thresh()`.
- `pose_verify.py`: `fallen_confidence_from_keypoints()` (pure, unit-tested geometry) +
  `verify_fall(frame, bbox)` (lazy model, never raises).
- `fall_detector.py`: `check_fall(..., frame=None)` — augments the returned details when
  `POSE_VERIFY=1` and a frame is passed. Default `frame=None` keeps every existing caller unchanged.
- `app.py`: the fall call site now passes `frame=frame`.

## The keypoint signal
Torso vector = shoulder-centre → hip-centre. `horizontal_ratio = |dx| / (|dx| + |dy|)`:
`~0` upright, `~1` lying flat. `pose_verified = horizontal_ratio >= POSE_VERIFY_THRESH`. Needs one
shoulder + one hip above `KP_CONF_MIN` (0.3); otherwise returns unknown.

## Validation gate (before claiming a number / raising tier)
1. **Recall unchanged:** on footage with real falls, confirm every fall raised with `POSE_VERIFY=0`
   is still raised with `POSE_VERIFY=1` (pose must never reduce recall — it is not allowed to).
2. **Precision signal:** label true vs false bbox-falls; check `pose_confidence` separates them
   (true falls high, false alarms low). Tune `POSE_VERIFY_THRESH` to that separation.
3. **Toward/away-camera falls:** include clips where a person falls along the camera axis; confirm
   `pose_verified=True` on them even though the bbox aspect ratio barely moved.
4. **Compute:** confirm the pose model is only invoked on fall events (log/count invocations),
   so steady-state ms/frame is unchanged. Real throughput = GPU-box / on-site measurement.

## Notes
- First fall lazily loads the pose model (one-time latency). Prewarm on a GPU node if desired.
- On a GPU node, export the pose net to ONNX/TensorRT and point `POSE_VERIFY_WEIGHTS` at it.
