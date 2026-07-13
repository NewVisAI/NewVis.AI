# Edge / NPU Deployment — Sentinel AI

How Sentinel runs on low-cost **Edge TPU (Google Coral)** and **NPU (ONNX/OpenVINO)**
hardware instead of GPUs, for the budget deployment tiers. This documents the
gap-closing work on top of the initial Edge-TPU/NPU commit.

## Backends & selection

Both the detector and the ReID embedder pick a backend automatically:

| Backend | Detector | ReID | Selected when |
|---|---|---|---|
| GPU / CPU (PyTorch) | `.pt` | OSNet (torchreid) | default |
| NPU (ONNX/OpenVINO) | `.onnx` | `.onnx` | model path ends `.onnx` |
| Edge TPU (Coral) | `_edgetpu.tflite` | `_edgetpu.tflite` | model path ends `_edgetpu.tflite` |

Selection is centralised in **`inference_config.py`** — one source of truth read by
both `detector.py` and `reid.py`. Configure via `deployment.json` (checked in per
site) or environment variables; **env overrides JSON overrides defaults**.

```json
// deployment.json — Budget/Edge tier
{
  "tier": "budget",
  "detector_weights": "models/yolov8s_320_edgetpu.tflite",
  "reid_tflite": "models/osnet_x1_0_edgetpu.tflite",
  "onnx_providers": ["OpenVINOExecutionProvider", "CPUExecutionProvider"]
}
```

Env equivalents: `DETECTOR_WEIGHTS`, `REID_MODEL_ONNX`, `REID_MODEL_TFLITE`,
`REID_ONNX_PROVIDERS`, `REID_SIMILARITY_THRESHOLD`, `SENTINEL_TIER`.

## Embedding parity (why we export OSNet, not ResNet50)

The GPU path's quality comes from **OSNet** (512-D). The edge exports must land in
the **same embedding space**, or cross-camera Global-IDs stop matching. Two things
guarantee parity:

1. `export_models.py` exports **the same OSNet** (`osnet_x1_0`, 512-D) to ONNX/TFLite.
2. `reid.py` reproduces torchreid's **exact preprocessing** for every backend —
   resize 256×128, scale `[0,1]`, ImageNet mean/std, plus correct INT8
   de-quantization for TFLite outputs.

## Match thresholds per backend

Quantized/edge embeddings separate identities less cleanly, so the match threshold
is backend-aware (in `inference_config.py`, override with `REID_SIMILARITY_THRESHOLD`):

| Backend | Default cosine threshold |
|---|---|
| osnet / onnx (OSNet) | 0.80 |
| resnet50 | 0.72 |
| tflite (INT8) | 0.62 |

These are **starting points — validate on real footage** before quoting accuracy.

## Exporting models

```bash
# NPU / OpenVINO (detector + OSNet -> ONNX)
python export_models.py --target onnx

# Edge TPU / Coral (detector -> edgetpu; OSNet -> INT8 TFLite)
python export_models.py --target edgetpu --imgsz 320 \
    --reid-rep-dir feature_test_report/frames
```

**Edge-TPU ReID is a 3-stage pipeline** (build host, Linux):
1. OSNet → ONNX  ✅ automated
2. ONNX → INT8 TFLite (needs `onnx2tf` + a folder of person crops for calibration)  ✅ automated
3. TFLite → `_edgetpu.tflite` via `edgetpu_compiler`  ← run manually on Linux

Detector Edge-TPU export is one step (`ultralytics` handles int8 + compile when
`edgetpu-compiler` is installed).

## Runtime dependencies

- NPU: install **`onnxruntime-openvino`** (not plain `onnxruntime`, which is CPU-only).
- GPU: install **`onnxruntime-gpu`**.
- Coral: install **`pycoral`** + `libedgetpu` (Linux).
- `reid.py` logs which ONNX provider actually bound, and warns if it silently fell
  back to CPU.

## Measuring capacity (don't guess — benchmark)

```bash
python benchmark_backends.py --iters 100 --target-fps 5 --people-per-frame 4
```

Reports ms/frame, frames/s, and **cameras-per-box** at the target fps for the
configured backend — so the "cameras per Coral/NPU" figures in the deployment
proposals become measured numbers on the real target hardware.

## Fallback safety

If an edge model is missing or a runtime isn't installed, each backend fails
gracefully down the chain: **TFLite → ONNX → OSNet → colour-histogram fallback**.
The pipeline never crashes on a mis-configured edge model.
