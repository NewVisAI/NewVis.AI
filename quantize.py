"""
GPU quantization for the Sentinel pipeline — cut deployment cost per camera
WITHOUT losing detection/Re-ID accuracy.

Where export_models.py targets Edge-TPU/NPU (INT8 TFLite), this module targets
the GPU deployment tier (the ~10-GPU / 300-camera cloud+edge plan). It produces
lower-precision copies of the two models so each camera costs less GPU compute
and VRAM — more cameras per GPU, and cheaper GPUs become viable.

Precision strategy (why FP16 first):
  * FP16 (half) — ~1.5-2x throughput on GPU, negligible accuracy loss. This is the
    "reduce cost without reducing efficiency" sweet spot and the default here.
  * INT8 — larger win (2-4x) + less VRAM, but needs calibration and risks recall on
    safety events (a missed fall/intrusion is unacceptable). Offered, gated behind
    validation, never the default.

What runs where:
  * OSNet FP32 ONNX -> FP16 ONNX  ....... runs on CPU (pure graph conversion). Do it here.
  * YOLO -> TensorRT FP16/INT8 engine ... needs the NVIDIA GPU box (guarded below).

ALWAYS run validate_quantization.py after exporting: it proves the quantized model
lands in the same embedding / detection space as FP32 before anything ships.

Usage:
    # OSNet FP32 ONNX -> FP16 ONNX (CPU-friendly)
    python quantize.py osnet-fp16

    # YOLO -> TensorRT FP16 engine (NVIDIA GPU only)
    python quantize.py yolo-trt --precision fp16 --weights yolov8n.pt --imgsz 640

    # YOLO -> TensorRT INT8 engine (NVIDIA GPU only; needs calibration data)
    python quantize.py yolo-trt --precision int8 --weights yolov8n.pt \
        --calib-frames feature_test_report/frames
"""

import argparse
import os

DEFAULT_OSNET_FP32 = "models/osnet_x1_0.onnx"
DEFAULT_OSNET_FP16 = "models/osnet_x1_0_fp16.onnx"


# --------------------------------------------------------------------------- #
# OSNet ReID: FP32 ONNX -> FP16 ONNX   (runs on CPU)
# --------------------------------------------------------------------------- #
def osnet_onnx_to_fp16(src=DEFAULT_OSNET_FP32, dst=DEFAULT_OSNET_FP16,
                       keep_io_fp32=True):
    """Convert an existing FP32 OSNet ONNX to FP16, preserving the graph/embedding
    space. keep_io_fp32 leaves the input/output tensors in float32 so reid.py's
    existing NCHW float32 feed and float32 embedding read work unchanged — only the
    internal weights/compute drop to half precision.
    """
    if not os.path.exists(src):
        print(f"[!] OSNet FP32 ONNX '{src}' not found.")
        print("    Build it first:  python export_models.py --target onnx --skip-yolo")
        return None

    try:
        import onnx
        from onnxconverter_common import float16
    except ImportError as exc:
        print(f"[!] Missing dependency ({exc}). Install:  pip install onnx onnxconverter-common")
        return None

    print(f"[*] Loading FP32 OSNet ONNX: {src}")
    # load_external_data=True folds the .onnx.data weights in so the FP16 output is
    # a single self-contained file.
    model = onnx.load(src, load_external_data=True)

    print("[*] Converting weights/compute to FP16 "
          f"(I/O kept {'FP32' if keep_io_fp32 else 'FP16'})...")
    model_fp16 = float16.convert_float_to_float16(
        model,
        keep_io_types=keep_io_fp32,   # inputs/outputs stay float32 -> no reid.py change
        disable_shape_infer=False,
    )

    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    onnx.save(model_fp16, dst)

    src_mb = os.path.getsize(src) / 1e6
    # FP32 ONNX may carry external .data; report the combined footprint fairly.
    ext = src + ".data"
    if os.path.exists(ext):
        src_mb += os.path.getsize(ext) / 1e6
    dst_mb = os.path.getsize(dst) / 1e6
    print(f"[+] FP16 OSNet written: {dst}")
    print(f"    Size: {src_mb:.1f} MB (FP32) -> {dst_mb:.1f} MB (FP16)  (~{100*(1-dst_mb/src_mb):.0f}% smaller)")
    print(f"    Point the runtime at it:  export REID_MODEL_ONNX={dst}")
    print("    Then VALIDATE:  python validate_quantization.py --fp16 " + dst)
    return dst


# --------------------------------------------------------------------------- #
# YOLO detector: TensorRT FP16 / INT8 engine   (NVIDIA GPU only)
# --------------------------------------------------------------------------- #
def yolo_to_tensorrt(weights="yolov8n.pt", precision="fp16", imgsz=640,
                     calib_frames=""):
    """Export YOLO to a TensorRT .engine at FP16 or INT8. Requires an NVIDIA GPU
    with TensorRT installed (ultralytics drives the build). INT8 needs a folder of
    representative frames for calibration."""
    try:
        import torch
    except ImportError:
        print("[!] torch not available.")
        return None

    if not torch.cuda.is_available():
        print("[!] No CUDA GPU detected. TensorRT engine build must run on the GPU box.")
        print("    This machine is CPU-only — run this command on the RTX box instead.")
        return None

    from ultralytics import YOLO

    print(f"[*] Loading YOLO: {weights}")
    model = YOLO(weights)

    kwargs = dict(format="engine", imgsz=imgsz, device=0)
    if precision == "fp16":
        kwargs.update(half=True)
        print(f"[*] Exporting YOLO -> TensorRT FP16 engine (imgsz={imgsz})...")
    elif precision == "int8":
        if not calib_frames or not os.path.isdir(calib_frames):
            print("[!] INT8 needs --calib-frames <folder of representative frames>.")
            return None
        kwargs.update(int8=True, data=calib_frames)
        print(f"[*] Exporting YOLO -> TensorRT INT8 engine (imgsz={imgsz}, calib={calib_frames})...")
    else:
        print(f"[!] Unknown precision '{precision}' (use fp16 or int8).")
        return None

    try:
        out = model.export(**kwargs)
        print(f"[+] YOLO TensorRT engine exported: {out}")
        print(f"    Point the runtime at it:  export DETECTOR_WEIGHTS={out}")
        print(f"    Then VALIDATE detection parity:  python validate_quantization.py --yolo-engine {out}")
        return out
    except Exception as exc:
        print(f"[!] TensorRT export failed: {exc}")
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GPU quantization for Sentinel models.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_osnet = sub.add_parser("osnet-fp16", help="OSNet FP32 ONNX -> FP16 ONNX (CPU-friendly)")
    p_osnet.add_argument("--src", default=DEFAULT_OSNET_FP32)
    p_osnet.add_argument("--dst", default=DEFAULT_OSNET_FP16)

    p_yolo = sub.add_parser("yolo-trt", help="YOLO -> TensorRT FP16/INT8 engine (NVIDIA GPU)")
    p_yolo.add_argument("--weights", default="yolov8n.pt")
    p_yolo.add_argument("--precision", choices=["fp16", "int8"], default="fp16")
    p_yolo.add_argument("--imgsz", type=int, default=640)
    p_yolo.add_argument("--calib-frames", default="", help="Folder of frames for INT8 calibration")

    args = parser.parse_args()

    if args.cmd == "osnet-fp16":
        osnet_onnx_to_fp16(args.src, args.dst)
    elif args.cmd == "yolo-trt":
        yolo_to_tensorrt(args.weights, args.precision, args.imgsz, args.calib_frames)
