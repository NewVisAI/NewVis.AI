"""
Event-gated pose verification for fall alerts (flag: POSE_VERIFY, off by default).

A pose model runs ONLY on the person crop of an already-detected fall, to attach a
keypoint-based confidence that the person is genuinely in a fallen (torso-horizontal)
posture. This is the compute-safe way to add pose: falls are rare, so the average added
cost is ~zero, while the alert gains a signal the bbox aspect-ratio test can't give
(it catches falls toward/away from the camera, where the bbox never flips horizontal).

SAFETY: this NEVER suppresses a fall the bbox heuristic caught. A safety system must not
drop a real alert because pose disagreed, so verify_fall() only *augments* the details
(pose_verified / pose_confidence / pose_reason). On any failure it returns
pose_verified=None ("unknown") and the alert is unaffected.

Usage (already wired into fall_detector.check_fall when POSE_VERIFY=1):

    details = pose_verify.verify_fall(frame, bbox)   # merged into the fall details dict
"""

from typing import Dict, List, Optional, Sequence, Tuple

import inference_config

# COCO-17 keypoint indices used for the torso-orientation test.
NOSE, L_SHOULDER, R_SHOULDER, L_HIP, R_HIP, L_ANKLE, R_ANKLE = 0, 5, 6, 11, 12, 15, 16
KP_CONF_MIN = 0.3   # ignore keypoints the pose model isn't confident about

_model = None


def _get_model():
    """Lazily load the pose model (only ever touched on a real fall)."""
    global _model
    if _model is None:
        from ultralytics import YOLO
        _model = YOLO(inference_config.pose_verify_weights())
    return _model


def _centre(points: Sequence[Optional[Tuple[float, float]]]) -> Optional[Tuple[float, float]]:
    valid = [p for p in points if p is not None]
    if not valid:
        return None
    return (sum(p[0] for p in valid) / len(valid), sum(p[1] for p in valid) / len(valid))


def fallen_confidence_from_keypoints(kpts: Sequence[Sequence[float]]) -> Tuple[float, str]:
    """Pure geometry: given 17 COCO keypoints as (x, y, conf), return
    (confidence 0..1, reason) that the torso is horizontal (person lying / fallen).

    Uses the shoulder-centre -> hip-centre vector: a standing torso is mostly vertical
    (dy dominates -> ratio ~0), a fallen torso is mostly horizontal (dx dominates ->
    ratio ~1). Robust to partial views and to falls toward/away from the camera, which
    the bbox aspect-ratio test misses. No model needed — unit-testable in isolation.
    """
    def pt(i: int) -> Optional[Tuple[float, float]]:
        x, y, c = kpts[i][0], kpts[i][1], kpts[i][2]
        return (float(x), float(y)) if float(c) >= KP_CONF_MIN else None

    shoulder = _centre([pt(L_SHOULDER), pt(R_SHOULDER)])
    hip = _centre([pt(L_HIP), pt(R_HIP)])
    if shoulder is None or hip is None:
        return 0.0, "insufficient torso keypoints"

    dx = abs(shoulder[0] - hip[0])
    dy = abs(shoulder[1] - hip[1])
    if dx + dy < 1e-6:
        return 0.0, "degenerate torso"

    horizontal_ratio = dx / (dx + dy)   # 1.0 = lying flat, 0.0 = fully upright
    return horizontal_ratio, f"torso_horizontal_ratio={horizontal_ratio:.2f}"


def verify_fall(frame, bbox, thresh: Optional[float] = None) -> Dict:
    """Run pose on the person crop and return fields to merge into the fall details.

    Never raises and never suppresses: on any problem it returns pose_verified=None so
    the fall alert fires exactly as it would without pose.
    """
    if thresh is None:
        thresh = inference_config.pose_verify_thresh()
    out: Dict = {"pose_verified": None, "pose_confidence": 0.0, "pose_reason": "not_run"}
    try:
        import numpy as np  # noqa: F401  (frame is a numpy array; kept local to stay import-light)
        x1, y1, x2, y2 = (int(v) for v in bbox)
        h, w = frame.shape[:2]
        pad_x = int((x2 - x1) * 0.15)
        pad_y = int((y2 - y1) * 0.15)
        cx1, cy1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
        cx2, cy2 = min(w, x2 + pad_x), min(h, y2 + pad_y)
        crop = frame[cy1:cy2, cx1:cx2]
        if crop.size == 0:
            out["pose_reason"] = "empty_crop"
            return out

        results = _get_model()(crop, verbose=False)
        best_score, best_kpts = -1.0, None
        for r in results:
            if getattr(r, "keypoints", None) is None or r.keypoints.data is None:
                continue
            data = r.keypoints.data          # (n, 17, 3)
            confs = r.boxes.conf if getattr(r, "boxes", None) is not None else None
            for i in range(len(data)):
                score = float(confs[i]) if confs is not None and len(confs) > i else 0.0
                if score > best_score:
                    best_score, best_kpts = score, data[i].tolist()

        if best_kpts is None:
            out["pose_reason"] = "no_pose_detected"
            return out

        confidence, reason = fallen_confidence_from_keypoints(best_kpts)
        out["pose_confidence"] = round(confidence, 3)
        out["pose_verified"] = bool(confidence >= thresh)
        out["pose_reason"] = reason
    except Exception as exc:  # never let pose break fall alerting
        out["pose_reason"] = f"error:{exc}"
    return out
