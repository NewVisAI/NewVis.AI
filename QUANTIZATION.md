# Quantization — Reducing Deployment Cost Without Losing Accuracy

Goal: lower the **GPU cost per camera** so a fixed camera fleet needs fewer / cheaper GPUs,
**without** degrading detection or Re-ID accuracy. This documents the tooling added
(`quantize.py`, `validate_quantization.py`) and how to run it.

## Why FP16 first (not INT8)
- **FP16 (half precision):** ~1.5–2× GPU throughput, less VRAM, **negligible accuracy loss.**
  This is the "cheaper without losing efficiency" sweet spot → the default.
- **INT8:** larger win (2–4×) and lower VRAM, but needs calibration and **risks recall on
  safety events** (a missed fall/intrusion is unacceptable). Supported, but **gated behind
  validation** and never enabled blindly.

## What was measured (this repo, CPU box)
**OSNet Re-ID FP16 vs FP32 — proven zero accuracy loss.** Across 150 real person crops from
`feature_test_report/frames`, cosine similarity between FP16 and FP32 embeddings:

| metric | value | gate |
|---|---|---|
| cosine **mean** | **0.999999** | ≥ 0.9990 ✅ |
| cosine p1 | 0.999997 | — |
| cosine **min** | **0.999996** | ≥ 0.9950 ✅ |

Re-ID matching decides identity on exactly this cosine value, so **identity separation is
unchanged.** Model file also shrank **9.5 MB → 5.1 MB (~46%)**.

> Speed/cost numbers require the GPU and are measured on the RTX box (below). This machine is
> CPU-only, so it proves **accuracy parity** here and defers **throughput** to the GPU box —
> the same split we used for the earlier benchmark.

## The tooling

### `quantize.py` — produce lower-precision models
```bash
# OSNet FP32 ONNX -> FP16 ONNX  (runs on CPU; already validated above)
# Prereq: models/osnet_x1_0.onnx exists (export_models.py --target onnx --skip-yolo)
#         and `pip install onnxconverter-common`.
python quantize.py osnet-fp16
#   -> models/osnet_x1_0_fp16.onnx

# YOLO detector -> TensorRT FP16 engine   (NVIDIA GPU box only)
python quantize.py yolo-trt --precision fp16 --weights yolov8n.pt --imgsz 640

# YOLO -> TensorRT INT8 engine   (GPU only; needs calibration frames)
python quantize.py yolo-trt --precision int8 --weights yolov8n.pt \
    --calib-frames feature_test_report/frames
```

### `validate_quantization.py` — the accuracy gate (run before shipping any quantized model)
```bash
# OSNet Re-ID parity (CPU-friendly): PASS needs mean cos >=0.999, min >=0.995
python validate_quantization.py --fp16 models/osnet_x1_0_fp16.onnx

# YOLO detector parity (GPU box): PASS needs >=98% of FP32 person detections retained
python validate_quantization.py --yolo-engine models/yolov8n.engine
```
Exits non-zero on FAIL, so it can gate a deploy script / CI step.

### `gpu_benchmark.py` — now precision-aware
It honours `DETECTOR_WEIGHTS` and `REID_MODEL_ONNX`, and prints the active precision, so you
benchmark FP32 vs quantized by swapping env vars — no code change.

## Runbook on the GPU box (friend's RTX 5060) — FP16 only

> **This round is FP16 only** (proven zero accuracy loss). INT8 is deferred until it clears the
> recall gate on real footage — see Status/next steps.
>
> **Prerequisites before `quantize.py osnet-fp16`:**
> 1. The FP32 OSNet ONNX must exist: `python export_models.py --target onnx --skip-yolo`
>    → `models/osnet_x1_0.onnx` (the converter and the validator both read this reference).
> 2. `pip install onnxconverter-common` — the FP16 converter needs it (this is CPU-side too).

Install a CUDA build + TensorRT extras in a fresh venv (repo `.venv` is CPU-only):
```bash
pip install torch --index-url https://download.pytorch.org/whl/cu124   # or the CUDA build you use
pip install onnxruntime-gpu tensorrt onnxconverter-common
```
Then, from the repo root:
```bash
# 1. Build quantized models  (FP16)
python export_models.py --target onnx --skip-yolo                  # prereq -> models/osnet_x1_0.onnx
python quantize.py osnet-fp16                                       # -> models/osnet_x1_0_fp16.onnx
python quantize.py yolo-trt --precision fp16 --weights yolov8n.pt --imgsz 640   # -> yolov8n.engine

# 2. GATE on accuracy (must PASS before step 3)
python validate_quantization.py --fp16 models/osnet_x1_0_fp16.onnx \
    --yolo-engine models/yolov8n.engine

# 3. Measure the cost win: FP32 baseline vs FP16, watching nvidia-smi -l 1
REID_DEFERRED=1 python gpu_benchmark.py <video-or-rtsp> 300                       # FP32 baseline
DETECTOR_WEIGHTS=models/yolov8n.engine REID_MODEL_ONNX=models/osnet_x1_0_fp16.onnx \
    REID_DEFERRED=1 python gpu_benchmark.py <video-or-rtsp> 300                   # FP16 quantized
```
Report `fps` and `cameras per machine` for each. The delta is the deployment-cost reduction.

## Turning it on in a real deployment (env-driven, not forced)
Quantization is **not** enabled by default (it would slow the CPU dev box). On a GPU node, set:
```bash
export DETECTOR_WEIGHTS=models/yolov8n.engine        # TensorRT FP16/INT8 detector
export REID_MODEL_ONNX=models/osnet_x1_0_fp16.onnx   # FP16 OSNet (onnxruntime-gpu)
export REID_ONNX_PROVIDERS=TensorrtExecutionProvider,CUDAExecutionProvider,CPUExecutionProvider
```
`reid.py` already has the ONNX backend + OSNet-parity preprocessing, and `detector.py` loads
the engine natively — so no code changes are needed to switch a node to quantized inference.

## Expected cost impact (to confirm with GPU numbers)
Stacked on the **+22.3%** already gained from deferred Re-ID:
- **FP16** typically adds ~1.5–2× on model inference → materially more cameras per GPU.
- **INT8** (if it passes the recall gate) lowers VRAM → **smaller/cheaper GPUs** become viable,
  cutting both one-time hardware cost and the power bill.

## Status / next steps
- [x] OSNet FP16 export + **accuracy parity proven** (cosine ~0.999999) on CPU.
- [x] Validation gate + precision-aware benchmark in place.
- [ ] Build YOLO TensorRT FP16 engine on the GPU box + validate detector recall.
- [ ] Run the FP32-vs-FP16 benchmark sweep → attach real cameras-per-GPU numbers here.
- [ ] Evaluate INT8 (only ship if it clears the recall gate).
