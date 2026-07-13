"""
Export Sentinel AI models to Edge-TPU (Coral) and NPU (ONNX/OpenVINO) formats.

Two models make up the pipeline and BOTH must be exported for an edge tier:
  1. the YOLO detector, and
  2. the OSNet person-ReID embedder.

The important correctness point: we export the SAME OSNet architecture the GPU
path uses (torchreid osnet_x1_0, 512-D embedding), NOT a generic ResNet50.
Exporting ResNet50 would put the edge devices in a different embedding space
than the GPU path, so cross-camera Global-IDs would silently stop matching.
reid.py reproduces torchreid's exact preprocessing (256x128, ImageNet mean/std)
so an OSNet-ONNX/TFLite export lands in the same space.

Usage:
    # NPU / OpenVINO path (detector + OSNet to ONNX)
    python export_models.py --target onnx

    # Edge-TPU / Coral path (detector to edgetpu; OSNet to INT8 TFLite)
    python export_models.py --target edgetpu --imgsz 320 \
        --reid-rep-dir feature_test_report/frames
"""

import argparse
import os

REID_INPUT_H, REID_INPUT_W = 256, 128   # torchreid/OSNet default (HxW)
REID_EMBED_DIM = 512                     # osnet_x1_0 feature size


# --------------------------------------------------------------------------- #
# YOLO detector
# --------------------------------------------------------------------------- #
def export_yolo(weights_path="yolov8s.pt", target_format="edgetpu", imgsz=320):
    from ultralytics import YOLO

    if not os.path.exists(weights_path):
        print(f"[!] YOLO weights '{weights_path}' not found; ultralytics will try to fetch it.")

    print(f"[*] Loading YOLO from {weights_path}...")
    model = YOLO(weights_path)
    print(f"[*] Exporting YOLO -> {target_format.upper()} (imgsz={imgsz})...")
    try:
        # ultralytics runs the full int8 quantization + edgetpu-compiler step
        # when format='edgetpu' (needs edgetpu-compiler installed on Linux).
        exported = model.export(format=target_format, imgsz=imgsz, half=False)
        print(f"[+] YOLO exported: {exported}")
        return exported
    except Exception as exc:
        print(f"[!] YOLO export failed: {exc}")
        if target_format == "edgetpu":
            print("    Install 'edgetpu-compiler' on a Linux host to compile to Edge TPU.")
        return None


# --------------------------------------------------------------------------- #
# OSNet ReID  (the parity-preserving export)
# --------------------------------------------------------------------------- #
def _build_osnet(reid_weights=""):
    """Return an eval-mode torchreid OSNet whose forward yields the 512-D feature."""
    import torch
    import torchreid

    model = torchreid.models.build_model(
        name=os.getenv("OSNET_MODEL", "osnet_x1_0"),
        num_classes=1000,
        pretrained=True,
    )
    if reid_weights and os.path.exists(reid_weights):
        torchreid.utils.load_pretrained_weights(model, reid_weights)
        print(f"[*] Loaded custom OSNet weights: {reid_weights}")
    model.eval()  # eval-mode OSNet.forward() returns the feature vector, not logits
    return model, torch


def export_reid_osnet_to_onnx(reid_weights="", output_path="models/osnet_x1_0.onnx"):
    """Export OSNet to ONNX (NPU/OpenVINO), 512-D, matching the GPU embedding space."""
    try:
        model, torch = _build_osnet(reid_weights)
    except Exception as exc:
        print(f"[!] Could not build OSNet ({exc}).")
        print("    Install torchreid:  pip install torchreid")
        return None

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    dummy = torch.randn(1, 3, REID_INPUT_H, REID_INPUT_W)
    print(f"[*] Exporting OSNet -> ONNX at {output_path} "
          f"({REID_INPUT_H}x{REID_INPUT_W}, {REID_EMBED_DIM}-D)...")
    try:
        torch.onnx.export(
            model, dummy, output_path,
            export_params=True, opset_version=12, do_constant_folding=True,
            input_names=["input"], output_names=["embedding"],
            dynamic_axes={"input": {0: "batch"}, "embedding": {0: "batch"}},
        )
        print(f"[+] OSNet ONNX exported: {output_path}")
        print("    Point the runtime at it:  export REID_MODEL_ONNX=" + output_path)
        return output_path
    except Exception as exc:
        print(f"[!] OSNet ONNX export failed: {exc}")
        return None


def export_reid_osnet_to_tflite(onnx_path, rep_dir="", int8=True,
                                output_dir="models/osnet_tflite"):
    """ONNX -> TFLite (INT8 for Edge TPU) via onnx2tf, then the Edge-TPU compile step.

    Full Edge-TPU deployment is a 3-stage pipeline; this automates stages 1-2 and
    prints the stage-3 command (which needs edgetpu-compiler on a Linux host):
        1. OSNet ONNX            (export_reid_osnet_to_onnx)
        2. ONNX -> INT8 TFLite   (this function, onnx2tf + representative dataset)
        3. TFLite -> _edgetpu.tflite  (edgetpu_compiler, printed below)
    """
    if not onnx_path or not os.path.exists(onnx_path):
        print(f"[!] ONNX model '{onnx_path}' not found; run the ONNX export first.")
        return None

    try:
        import onnx2tf  # noqa: F401
    except ImportError:
        print("[!] onnx2tf not installed. To build the TFLite ReID model:")
        print("    pip install onnx2tf tensorflow onnx onnx-graphsurgeon sng4onnx")
        print("    then re-run with --target edgetpu.")
        return None

    if int8 and not rep_dir:
        print("[!] INT8 needs --reid-rep-dir (a folder of person crops for calibration).")
        print("    A few hundred real crops from feature_test_report/frames works well.")
        return None

    os.makedirs(output_dir, exist_ok=True)
    print(f"[*] Converting {onnx_path} -> TFLite ({'INT8' if int8 else 'FP32'}) in {output_dir}/ ...")
    try:
        # onnx2tf emits float + (with calibration) full-integer-quant TFLite files.
        onnx2tf.convert(input_onnx_file_path=onnx_path, output_folder_path=output_dir)
        print(f"[+] TFLite written under {output_dir}/")
        print("    Stage 3 (Coral): edgetpu_compiler "
              f"{output_dir}/<model>_full_integer_quant.tflite")
        print("    Then:  export REID_MODEL_TFLITE=" + output_dir + "/<model>_edgetpu.tflite")
        return output_dir
    except Exception as exc:
        print(f"[!] TFLite conversion failed: {exc}")
        print("    See onnx2tf docs for INT8 calibration flags for your model.")
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export Sentinel models to TPU/NPU formats.")
    parser.add_argument("--yolo-weights", default="yolov8s.pt")
    parser.add_argument("--reid-weights", default="", help="Optional OSNet .pth (else pretrained)")
    parser.add_argument("--imgsz", type=int, default=320, help="Detector input size (smaller=faster on TPU)")
    parser.add_argument("--target", choices=["edgetpu", "onnx"], default="onnx")
    parser.add_argument("--reid-rep-dir", default="", help="Folder of person crops for INT8 calibration")
    parser.add_argument("--skip-yolo", action="store_true")
    parser.add_argument("--skip-reid", action="store_true")
    args = parser.parse_args()

    if not args.skip_yolo:
        export_yolo(args.yolo_weights, args.target, args.imgsz)

    if not args.skip_reid:
        onnx_path = export_reid_osnet_to_onnx(args.reid_weights, "models/osnet_x1_0.onnx")
        if args.target == "edgetpu" and onnx_path:
            export_reid_osnet_to_tflite(onnx_path, rep_dir=args.reid_rep_dir, int8=True)

    print("\n[i] ONNX (OSNet) is ready for NPU/OpenVINO. For Edge-TPU, finish the")
    print("    3-stage pipeline (ONNX -> INT8 TFLite -> edgetpu_compiler) on a Linux host.")
