# Sentinel — GPU-Laptop Validation Runbook

**For:** whoever has the NVIDIA GPU laptop (RTX 30/40-class).
**Goal:** run the work that a CPU box can't — get the **decode-vs-inference verdict** and
**validate the GPU cost-reduction levers** with real numbers, without losing any accuracy.

> Companion to `REPORT_for-friend_optimizations-and-GPU.md` (setup + the older
> adaptive-gate / deferred-Re-ID measurement). This runbook adds the **new levers**:
> FP16 (#5), hardware decode / NVDEC (#3), TensorRT (#9), sub-stream (#4), and the
> build-and-measure specs for batching (#2), async pipelining (#10), zero-copy (#8).

Every lever here is **accuracy-neutral** — each has a validation gate that must PASS
before its number counts. Run on **Ubuntu** if you can (DeepStream + NVDEC-OpenCV are far
easier on Linux); Windows notes are called out where they differ.

---

## 0. What this run answers (in priority order)

1. **Decode-bound vs inference-bound?** — the single most important result. It decides
   whether the hard architectural levers (#2 batching, #8 zero-copy) are even worth
   building, or whether the win is all in decode (#3, #4).
2. **FP16 (#5)** — real throughput gain + re-confirm the 0.999999 Re-ID parity.
3. **TensorRT (#9)** — detector engine speedup, with a person-recall parity gate.
4. **NVDEC (#3)** — does hardware decode actually engage and offload the CPU?
5. **Sub-stream (#4)** — confirm 720p keeps person recall (already 98.8% on CPU frames).
6. **Batching (#2) / async (#10) / zero-copy (#8)** — build, then measure (specs in §6).

---

## 1. Setup (fresh CUDA venv — the repo `.venv` is CPU-only)

```bash
git clone https://github.com/haronnk/SENTINEL-2.0.git
cd SENTINEL-2.0
python -m venv .venv-gpu
source .venv-gpu/bin/activate            # Windows: .venv-gpu\Scripts\activate
python -m pip install -U pip

# 1) CUDA PyTorch — match your CUDA (see `nvidia-smi`, top-right). CUDA 12.1 example:
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121

# 2) GPU ONNX Runtime (NOT plain onnxruntime — that's CPU-only)
pip uninstall -y onnxruntime 2>/dev/null; pip install onnxruntime-gpu

# 3) Rest of the stack
pip install ultralytics supervision opencv-python fastapi "uvicorn[standard]" \
            cryptography torchreid numpy onnx

# 4) TensorRT (for lever #9). Easiest via pip; else use NVIDIA's TensorRT tarball.
pip install tensorrt

# Confirm the GPU is visible + which ONNX providers are available:
python -c "import torch; print('CUDA:', torch.cuda.is_available(), torch.cuda.get_device_name(0))"
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
```

You want to see `CUDAExecutionProvider` (and ideally `TensorrtExecutionProvider`) in the
providers list. Drop `license.key` (get it from Haron — it's gitignored) into the repo root.

Grab a test clip with people (any of the `view-*.mp4` used in the demo works), and in a
second terminal keep the GPU monitor running the whole time:

```bash
nvidia-smi dmon -s u        # per-second: sm% (compute), dec% (NVDEC decoder), mem
```

---

## 2. THE key result — decode-bound vs inference-bound

`gpu_benchmark.py` runs the full pipeline over a video and prints ms/frame, fps, and
cameras-per-machine. Run the two Re-ID modes and watch `nvidia-smi dmon`:

```bash
# full OSNet Re-ID live (heavier)
REID_DEFERRED=0 python gpu_benchmark.py path/to/test.mp4 300
# deferred Re-ID (OSNet skipped live)
REID_DEFERRED=1 python gpu_benchmark.py path/to/test.mp4 300
```

**Read the verdict from `nvidia-smi dmon` while it runs:**
- **`sm%` high, `dec%` low → inference-bound** → #2 batching, #9 TensorRT, #5 FP16 matter most.
- **`dec%` high, `sm%` low → decode-bound** → #3 NVDEC + #4 sub-stream matter most; batching
  (#2) is likely **not worth building**.

Record both `fps`/`cameras per machine` lines and the sm%/dec% split. This one number set
steers everything below.

---

## 3. Lever #5 — FP16 (validate, then measure)

```bash
# a) Produce the FP16 OSNet (pure graph conversion — CPU or GPU)
python quantize.py osnet-fp16               # -> models/osnet_x1_0_fp16.onnx

# b) GATE: prove FP16 == FP32 in the embedding space (must PASS before it counts)
python validate_quantization.py --fp16 models/osnet_x1_0_fp16.onnx
#    PASS = mean cosine >= 0.9990 AND min cosine >= 0.9950

# c) Measure the throughput gain: point Re-ID at the FP16 ONNX and re-benchmark
REID_MODEL_ONNX=models/osnet_x1_0_fp16.onnx REID_DEFERRED=0 \
    python gpu_benchmark.py path/to/test.mp4 300
```
Compare (c) fps against §2's `REID_DEFERRED=0` baseline — the delta is the FP16 win.
Expect ~1.5–2×; accuracy delta must be ~0 (from the gate).

---

## 4. Lever #9 — TensorRT detector engine (validate, then measure)

```bash
# a) Build the engine (NVIDIA GPU only; engine is specific to THIS GPU + TensorRT version)
python quantize.py yolo-trt --precision fp16 --weights yolov8n.pt --imgsz 640
#    -> models/yolov8n.engine

# b) GATE: detector parity — person recall retained vs the FP32 .pt
python validate_quantization.py --yolo-engine models/yolov8n.engine
#    PASS = person recall retained >= 0.98 (<=2% of FP32 person detections lost)

# c) Measure: run the pipeline with the engine as the detector
DETECTOR_WEIGHTS=models/yolov8n.engine REID_DEFERRED=1 \
    python gpu_benchmark.py path/to/test.mp4 300
```
> An `.engine` is **not portable** — rebuild it on the production GPU. The laptop proves
> the workflow + the speedup, not the final number. INT8 (`--precision int8 --calib-frames
> feature_test_report/frames`) is a bigger win but only ship it if (b) still passes.

---

## 5. Lever #3 (NVDEC) + #4 (sub-stream) — decode-side

**#3 Hardware decode (NVDEC).** Needs an OpenCV built with NVIDIA hwaccel — the stock
`opencv-python` wheel is CPU-only and **silently falls back to software decode**. Two paths:
build OpenCV with `-D WITH_CUDA=ON -D WITH_NVCUVID=ON`, or use an FFmpeg-with-cuda + set the
capture options. Then run the live server and watch the decoder column:

```bash
# tell the pipeline to request NVDEC, and force the FFmpeg hwaccel option:
export DECODE_BACKEND=nvdec
export OPENCV_FFMPEG_CAPTURE_OPTIONS="rtsp_transport;tcp|hwaccel;cuda"
DISABLE_AI_ENGINE=1 python -m uvicorn backend.server:app --host 0.0.0.0 --port 8000
# -> Video Analytics -> Run live analytics on a camera
```
**Confirm it engaged:** the reader logs a `CAP_PROP_HW_ACCELERATION` line, and in
`nvidia-smi dmon -s u` the **`dec%` column rises** while CPU decode drops. If `dec%` stays 0,
your OpenCV has no hwaccel (rebuild it). Consumer GPUs cap concurrent NVDEC sessions
(~3–8) — note how many streams you can decode before it falls back.

**#4 Sub-stream (720p).** Recall gate (no GPU needed, but run it here for completeness):
```bash
python validate_quantization.py --substream --sub-height 720
#    reports person recall at 720p vs full-res (expect >= ~0.95; was 98.8% on CPU frames)
```
Then measure decode savings live with `USE_SUBSTREAM=1` and watch `dec%`/CPU drop:
```bash
USE_SUBSTREAM=1 DISABLE_AI_ENGINE=1 python -m uvicorn backend.server:app --port 8000
```

**Cameras-per-box for the configured backend** (point `deployment.json`/env at the FP16 or
engine model first):
```bash
python benchmark_backends.py --iters 100 --target-fps 5 --people-per-frame 4
```

---

## 6. Levers to BUILD, then measure (#2, #10, #8)

These aren't in the code yet. Build only the ones §2's verdict justifies.

### #2 Cross-camera batching  *(build only if INFERENCE-bound)*
- **Where:** `detector.py`'s `detect()` is one frame at a time; the per-camera call sites are
  `app.py` / `live_analytics.py`. ultralytics YOLO already accepts a **list of frames** in one
  call (`model([f1, f2, ...])`).
- **Build:** add `HumanDetector.detect_batch(frames)`; add a small shared queue that collects
  the newest frame from each active analytics thread within a ~10–20 ms window, runs ONE
  batched detect, and routes results back per camera.
- **Measure:** a tiny harness timing `detect([frame]*1)` vs `detect([frame]*8)` on the GPU →
  the per-frame-cost drop is the batching win (expect ~3–5× at batch-8 for yolov8n). Then
  re-run `gpu_benchmark`-style cameras/GPU with the batched queue.

### #10 Async pipelining
- **Where:** `live_analytics` already splits reader vs analytics threads. Next step: overlap
  **GPU inference** of frame N+1 with **CPU post-processing** (tracking/events) of frame N,
  and/or use a CUDA stream so decode/infer/post don't serialize.
- **Measure:** sustained fps with vs without the overlap on the same clip; it can't beat the
  slowest single stage, so compare against the per-stage times.

### #8 Zero-copy decode → inference  *(largest effort, Linux only)*
- Requires the **DeepStream SDK** (Ubuntu). Prototype a `deepstream-app` graph:
  `nvv4l2decoder` (NVDEC) → `nvinfer` (the TensorRT engine from §4) → probe for detections,
  keeping frames **GPU-resident** (NVMM) end-to-end (no CPU round-trip).
- **Measure:** decode+inference throughput per GPU vs the OpenCV path; this is where #2+#3
  combine natively. Only pursue if the verdict is decode+inference-bound at high density.

---

## 7. Flag / env cheat-sheet

| Env var | Lever | Effect |
|---|---|---|
| `REID_DEFERRED=1` | — | skip live OSNet; cross-camera Re-ID becomes on-demand search |
| `REID_MODEL_ONNX=…fp16.onnx` | #5 | run Re-ID on the FP16 OSNet |
| `DETECTOR_WEIGHTS=…yolov8n.engine` | #9 | run detection on the TensorRT engine |
| `DECODE_BACKEND=nvdec` (+ `OPENCV_FFMPEG_CAPTURE_OPTIONS`) | #3 | hardware decode via NVDEC |
| `USE_SUBSTREAM=1` | #4 | analyze the 720p sub-stream |
| `FRAME_DEDUP=1` | #13 | skip byte-identical frames (stalled feeds) |
| `DISABLE_AI_ENGINE=1` | — | use per-camera live_analytics, not the pool engine |

---

## 8. Report back — fill this in and send it

```
GPU: __________________ (e.g. RTX 4070 Laptop, 8 GB)   CUDA: ____   driver: ____
Test clip: __________  people/frame ~ ____

§2 VERDICT: sm% ____  dec% ____  ->  [ ] inference-bound  [ ] decode-bound
   REID_DEFERRED=0: ____ fps  (~____ cams/machine)
   REID_DEFERRED=1: ____ fps  (~____ cams/machine)

#5 FP16:  gate cosine mean ____ / min ____  [PASS/FAIL]   fps ____ (vs ____ baseline)
#9 TRT :  gate person-recall ____  [PASS/FAIL]            fps ____ (vs ____ baseline)
#3 NVDEC: dec% before ____ -> after ____   CPU before ____ -> after ____   [engaged? Y/N]
#4 SUB :  recall@720p ____  [PASS/FAIL]     dec%/CPU drop with USE_SUBSTREAM: ____
benchmark_backends: ms/frame ____  fps ____  cameras/box ____

#2 batching (if built): batch-1 ____ ms  vs  batch-8 ____ ms/frame  -> ____x
#10 async  (if built): fps before ____ -> after ____
#8 DeepStream (if attempted): notes ____________________________________
```

---

## 9. Caveats (so the numbers are read honestly)

- **A laptop is for proving mechanisms + relative speedups, not final sizing.** Laptop RTX
  vs datacenter **L4/T4** differ in memory bandwidth, VRAM, NVDEC session limits, and
  **thermals** (laptops throttle under sustained load → pessimistic sustained fps).
- **Re-measure cams/GPU once on a production-class card** before any quote-grade number.
- **TensorRT engines are per-GPU** — rebuild on the target hardware.
- Anything that **fails its accuracy gate does not ship** — a missed fall/intrusion costs
  more than a GPU.
```
