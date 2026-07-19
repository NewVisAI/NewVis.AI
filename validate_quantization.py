"""
Accuracy-parity gate for quantized Sentinel models.

Quantization only reduces deployment cost if it does NOT reduce accuracy. This
harness proves that by comparing a quantized model against the FP32 reference on
REAL frames, and printing a PASS/FAIL gate. Nothing quantized should ship until
it passes here.

Two checks:

  --fp16 <osnet_fp16.onnx>
      OSNet Re-ID parity. Extracts person crops from real frames (via YOLO),
      embeds each crop with FP32 OSNet and the quantized OSNet, and reports the
      cosine similarity between the two embedding sets. Re-ID matching runs on
      cosine similarity, so this is the exact quantity that must not move.
      GATE: mean cosine >= 0.9990 and min cosine >= 0.9950.

  --yolo-engine <yolo.engine>   (NVIDIA GPU only)
      Detector parity. Runs the FP32 .pt and the quantized engine on the same
      frames and reports person-detection recall retained + mean confidence
      delta. Missing a person is the failure that matters for safety events.
      GATE: person recall retained >= 0.98 (i.e. <=2% of FP32 person detections lost).

Both checks read frames from --frames (default feature_test_report/frames).

Usage:
    python validate_quantization.py --fp16 models/osnet_x1_0_fp16.onnx
    python validate_quantization.py --yolo-engine models/yolov8n.engine   # on GPU box
"""

import argparse
import glob
import os
from typing import List, Tuple

import numpy as np

import reid  # reuse the EXACT preprocessing the live pipeline uses

FP32_OSNET = "models/osnet_x1_0.onnx"
DEFAULT_FRAMES = "feature_test_report/frames"

# Parity gates
OSNET_MEAN_COS_GATE = 0.9990
OSNET_MIN_COS_GATE = 0.9950
YOLO_RECALL_GATE = 0.98
SUBSTREAM_RECALL_GATE = 0.95


def _list_frames(frames_dir: str, limit: int) -> List[str]:
    exts = ("*.jpg", "*.jpeg", "*.png")
    paths: List[str] = []
    for ext in exts:
        paths.extend(glob.glob(os.path.join(frames_dir, ext)))
    paths.sort()
    return paths[:limit]


def _extract_person_crops(frame_paths: List[str], max_crops: int) -> List[np.ndarray]:
    """Run YOLO on frames and return person crops (the real Re-ID inputs)."""
    import cv2
    from detector import HumanDetector

    det = HumanDetector(model_type="yolo", weights="yolov8n.pt")
    crops: List[np.ndarray] = []
    for path in frame_paths:
        frame = cv2.imread(path)
        if frame is None:
            continue
        for (x1, y1, x2, y2, conf, cls_id) in det.detect(frame):
            if cls_id != 0:  # person only
                continue
            x1, y1, x2, y2 = reid._clip_bbox(frame.shape, (x1, y1, x2, y2))
            crop = frame[y1:y2, x1:x2]
            if crop.size > 0:
                crops.append(crop)
            if len(crops) >= max_crops:
                return crops
    return crops


def _onnx_embed(session, crop: np.ndarray) -> np.ndarray:
    """Embed one BGR crop with an OSNet ONNX session, using reid.py preprocessing."""
    inp = session.get_inputs()[0]
    shape = inp.shape
    h = shape[2] if len(shape) == 4 and isinstance(shape[2], int) else reid.OSNET_INPUT_HW[0]
    w = shape[3] if len(shape) == 4 and isinstance(shape[3], int) else reid.OSNET_INPUT_HW[1]
    proc = reid._preprocess_person_crop(crop, (int(h), int(w)), mean_std=True)
    data = np.expand_dims(np.transpose(proc, (2, 0, 1)), axis=0).astype(np.float32)
    out = session.run(None, {inp.name: data})
    return reid._normalize_embedding(np.asarray(out[0], dtype=np.float32).reshape(-1))


def validate_osnet_fp16(fp16_path: str, frames_dir: str, max_crops: int) -> bool:
    import onnxruntime as ort

    if not os.path.exists(FP32_OSNET):
        print(f"[!] FP32 reference '{FP32_OSNET}' missing — run export_models.py --target onnx first.")
        return False
    if not os.path.exists(fp16_path):
        print(f"[!] FP16 model '{fp16_path}' missing — run quantize.py osnet-fp16 first.")
        return False

    print(f"[*] Collecting person crops from {frames_dir} ...")
    frames = _list_frames(frames_dir, limit=max_crops * 2)
    crops = _extract_person_crops(frames, max_crops)
    if not crops:
        print("[!] No person crops found in the frames — cannot validate.")
        return False
    print(f"[*] Got {len(crops)} person crops.")

    fp32 = ort.InferenceSession(FP32_OSNET, providers=["CPUExecutionProvider"])
    fp16 = ort.InferenceSession(fp16_path, providers=["CPUExecutionProvider"])

    cosines = []
    for crop in crops:
        e32 = _onnx_embed(fp32, crop)
        e16 = _onnx_embed(fp16, crop)
        cosines.append(float(np.dot(e32, e16)))  # both L2-normalised -> dot == cosine

    cosines = np.array(cosines)
    mean_c, min_c, p1_c = float(cosines.mean()), float(cosines.min()), float(np.percentile(cosines, 1))

    print("\n===== OSNet FP16 vs FP32 parity =====")
    print(f"crops evaluated : {len(cosines)}")
    print(f"cosine mean     : {mean_c:.6f}   (gate >= {OSNET_MEAN_COS_GATE})")
    print(f"cosine  p1      : {p1_c:.6f}")
    print(f"cosine min      : {min_c:.6f}   (gate >= {OSNET_MIN_COS_GATE})")

    passed = mean_c >= OSNET_MEAN_COS_GATE and min_c >= OSNET_MIN_COS_GATE
    print(f"\nRESULT: {'PASS ✅ — FP16 is safe to ship (no accuracy loss)' if passed else 'FAIL ❌ — do NOT ship FP16 as-is'}")
    return passed


def validate_yolo_engine(engine_path: str, frames_dir: str, max_frames: int) -> bool:
    import cv2
    import torch
    from detector import HumanDetector

    if not torch.cuda.is_available():
        print("[!] YOLO engine validation needs the GPU box (TensorRT). Run this there.")
        return False
    if not os.path.exists(engine_path):
        print(f"[!] Engine '{engine_path}' not found.")
        return False

    frames = _list_frames(frames_dir, max_frames)
    if not frames:
        print(f"[!] No frames in {frames_dir}.")
        return False

    fp32 = HumanDetector(model_type="yolo", weights="yolov8n.pt")
    quant = HumanDetector(model_type="yolo", weights=engine_path)

    def persons(det, frame):
        return [d for d in det.detect(frame) if d[5] == 0]

    def iou(a, b):
        return reid._bbox_iou(a[:4], b[:4])

    total_ref, matched, conf_deltas = 0, 0, []
    for path in frames:
        frame = cv2.imread(path)
        if frame is None:
            continue
        ref, qry = persons(fp32, frame), persons(quant, frame)
        total_ref += len(ref)
        used = set()
        for r in ref:
            best_j, best_iou = -1, 0.0
            for j, q in enumerate(qry):
                if j in used:
                    continue
                i = iou(r, q)
                if i > best_iou:
                    best_iou, best_j = i, j
            if best_iou >= 0.5 and best_j >= 0:
                matched += 1
                used.add(best_j)
                conf_deltas.append(abs(r[4] - qry[best_j][4]))

    recall = matched / total_ref if total_ref else 0.0
    mean_dconf = float(np.mean(conf_deltas)) if conf_deltas else 0.0

    print("\n===== YOLO engine vs FP32 .pt parity =====")
    print(f"frames          : {len(frames)}")
    print(f"FP32 persons    : {total_ref}")
    print(f"recall retained : {recall:.4f}   (gate >= {YOLO_RECALL_GATE})")
    print(f"mean |Δconf|     : {mean_dconf:.4f}")

    passed = recall >= YOLO_RECALL_GATE
    print(f"\nRESULT: {'PASS ✅ — detector quantization keeps recall' if passed else 'FAIL ❌ — recall dropped, do NOT ship'}")
    return passed


def validate_substream(frames_dir: str, max_frames: int, sub_height: int = 720) -> bool:
    """Sub-stream recall gate: does analyzing the low-res sub-stream lose people vs
    the full-res main stream? Proxy: run YOLO on each full-res frame ('main') and on
    the same frame downscaled to `sub_height` ('sub'), rescale the sub detections back,
    and match by IoU. GATE: >=95% of full-res person detections retained at sub-res.
    """
    import cv2
    from detector import HumanDetector

    frames = _list_frames(frames_dir, max_frames)
    if not frames:
        print(f"[!] No frames in {frames_dir}.")
        return False

    det = HumanDetector(model_type="yolo", weights="yolov8n.pt")

    def persons(frame):
        return [d for d in det.detect(frame) if d[5] == 0]

    total_ref, matched, conf_deltas, downscaled = 0, 0, [], 0
    for path in frames:
        frame = cv2.imread(path)
        if frame is None:
            continue
        h, w = frame.shape[:2]
        scale = sub_height / float(h) if h > sub_height else 1.0
        if scale != 1.0:
            downscaled += 1
            sub = cv2.resize(frame, (max(1, int(round(w * scale))), int(round(h * scale))))
        else:
            sub = frame
        ref = persons(frame)                 # full-res "main"
        qry_raw = persons(sub)               # low-res "sub"
        inv = 1.0 / scale
        qry = [(d[0] * inv, d[1] * inv, d[2] * inv, d[3] * inv, d[4], d[5]) for d in qry_raw]
        total_ref += len(ref)
        used = set()
        for r in ref:
            best_j, best_iou = -1, 0.0
            for j, q in enumerate(qry):
                if j in used:
                    continue
                i = reid._bbox_iou(r[:4], q[:4])
                if i > best_iou:
                    best_iou, best_j = i, j
            if best_iou >= 0.5 and best_j >= 0:
                matched += 1
                used.add(best_j)
                conf_deltas.append(abs(r[4] - qry[best_j][4]))

    recall = matched / total_ref if total_ref else 0.0
    mean_dconf = float(np.mean(conf_deltas)) if conf_deltas else 0.0

    print("\n===== Sub-stream (720p) vs full-res person-detection parity =====")
    print(f"frames evaluated : {len(frames)}   (actually downscaled: {downscaled})")
    print(f"full-res persons : {total_ref}")
    print(f"recall retained  : {recall:.4f}   (gate >= {SUBSTREAM_RECALL_GATE})")
    print(f"mean |Δconf|      : {mean_dconf:.4f}")
    if downscaled == 0:
        print("NOTE: no frame was above the sub height, so nothing was actually downscaled — "
              "re-run against real 1080p+ footage for a meaningful test.")

    passed = recall >= SUBSTREAM_RECALL_GATE and downscaled > 0
    print(f"\nRESULT: {'PASS ✅ — sub-stream keeps detection recall' if passed else 'FAIL ❌ / inconclusive — review before enabling USE_SUBSTREAM'}")
    return passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prove quantized models keep FP32 accuracy.")
    parser.add_argument("--fp16", default="", help="OSNet FP16 ONNX to validate (Re-ID parity)")
    parser.add_argument("--yolo-engine", default="", help="YOLO TensorRT engine to validate (GPU)")
    parser.add_argument("--substream", action="store_true", help="Validate sub-stream (720p) detection recall")
    parser.add_argument("--sub-height", type=int, default=720, help="Sub-stream height for the --substream check")
    parser.add_argument("--frames", default=DEFAULT_FRAMES, help="Folder of real frames")
    parser.add_argument("--max-crops", type=int, default=150, help="Max person crops for OSNet check")
    parser.add_argument("--max-frames", type=int, default=100, help="Max frames for YOLO/sub-stream checks")
    args = parser.parse_args()

    if not args.fp16 and not args.yolo_engine and not args.substream:
        parser.error("give --fp16 <model>, --yolo-engine <engine>, and/or --substream")

    ok = True
    if args.fp16:
        ok = validate_osnet_fp16(args.fp16, args.frames, args.max_crops) and ok
    if args.yolo_engine:
        ok = validate_yolo_engine(args.yolo_engine, args.frames, args.max_frames) and ok
    if args.substream:
        ok = validate_substream(args.frames, args.max_frames, args.sub_height) and ok

    raise SystemExit(0 if ok else 1)
