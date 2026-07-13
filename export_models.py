import os
import argparse
from ultralytics import YOLO

def export_yolo(weights_path="yolov8s.pt", target_format="edgetpu", imgsz=320):
    """
    Exports a YOLO model to the specified format (edgetpu or onnx).
    """
    if not os.path.exists(weights_path):
        print(f"[!] Error: YOLO weights '{weights_path}' not found.")
        return

    print(f"[*] Loading YOLO model from {weights_path}...")
    model = YOLO(weights_path)
    
    print(f"[*] Exporting YOLO to {target_format.upper()} format (imgsz={imgsz})...")
    # ultralytics export natively supports format="edgetpu" which runs the full int8 quantization
    # and compiler process if the edgetpu-compiler is installed on the host.
    try:
        exported_path = model.export(format=target_format, imgsz=imgsz, half=False)
        print(f"[+] YOLO exported successfully to: {exported_path}")
    except Exception as e:
        print(f"[!] YOLO export failed: {e}")
        if target_format == "edgetpu":
            print("    Make sure you have 'edgetpu-compiler' installed on your Linux machine to compile to EdgeTPU.")

def export_reid_resnet_to_onnx(output_path="reid_resnet50.onnx"):
    """
    Exports the PyTorch ResNet50 Re-ID model to ONNX for NPU inference.
    """
    try:
        import torch
        import torchvision.models as models
        from torchvision.models import ResNet50_Weights
        
        print("[*] Loading ResNet50 for ONNX export...")
        model = models.resnet50(weights=ResNet50_Weights.DEFAULT)
        model.fc = torch.nn.Identity()
        model.eval()

        # Dummy input: NCHW, batch=1, channels=3, height=128, width=64
        dummy_input = torch.randn(1, 3, 128, 64)
        
        print(f"[*] Exporting ResNet50 ReID to ONNX at {output_path}...")
        torch.onnx.export(
            model,
            dummy_input,
            output_path,
            export_params=True,
            opset_version=12,
            do_constant_folding=True,
            input_names=['input'],
            output_names=['output'],
            dynamic_axes={'input': {0: 'batch_size'}, 'output': {0: 'batch_size'}}
        )
        print(f"[+] ReID ONNX exported successfully to: {output_path}")
    except Exception as e:
        print(f"[!] ReID ONNX export failed: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export Sentinel AI models to TPU/NPU formats.")
    parser.add_argument("--yolo-weights", type=str, default="yolov8s.pt", help="Path to PyTorch YOLO weights")
    parser.add_argument("--imgsz", type=int, default=320, help="Input image size (smaller = faster on TPU)")
    parser.add_argument("--target", type=str, choices=["edgetpu", "onnx"], default="edgetpu", help="Target hardware format")
    args = parser.parse_args()

    export_yolo(weights_path=args.yolo_weights, target_format=args.target, imgsz=args.imgsz)
    
    if args.target == "onnx":
        export_reid_resnet_to_onnx()
    
    print("\n[i] Note: To fully quantize a PyTorch Re-ID model for Edge TPU (TFLite INT8),")
    print("    you must use TensorFlow/TFLite Converter with a representative dataset.")
    print("    The ONNX format generated here is perfect for OpenVINO/NPU execution.")
