import torch
from ultralytics import YOLO, RTDETR

import inference_config


class HumanDetector:
    def __init__(self, model_type="yolo", weights=None):
        self.model_type = model_type.lower()
        # If no explicit weights are passed, take them from the central
        # deployment config (lets a Budget/Edge tier point at an _edgetpu.tflite
        # or .onnx model without any code change). Falls through to the .pt
        # defaults below when neither is set.
        if weights is None:
            weights = inference_config.get_detector_weights()
        # Hardware Detection: Check file extension
        self.is_tpu = str(weights).endswith('_edgetpu.tflite') if weights else False
        self.is_onnx = str(weights).endswith('.onnx') if weights else False
        
        # Determine device
        if self.is_tpu:
            self.device = "cpu"  # PyCoral handles TPU routing under the hood
            print("[HumanDetector] Hardware: EDGE TPU DETECTED. Using Google Coral Accelerator.")
        elif self.is_onnx:
            self.device = "cpu"  # ONNX Runtime handles NPU/CPU
            print("[HumanDetector] Hardware: NPU/ONNX DETECTED. Using ONNX Runtime.")
        else:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            print(f"[HumanDetector] Hardware: GPU/CPU DETECTED. Using PyTorch on {self.device.upper()}")

        if self.model_type == "rtdetr":
            model_weights = weights or "rtdetr-l.pt"
            print(f"[HumanDetector] Loading RT-DETR model with weights: {model_weights}...")
            self.model = RTDETR(model_weights)
        else:
            model_weights = weights or "yolov8s.pt"
            print(f"[HumanDetector] Loading YOLO model with weights: {model_weights}...")
            # Ultralytics natively loads .tflite and .onnx using the same YOLO() call!
            self.model = YOLO(model_weights)

        # Target classes (COCO dataset)
        self.TARGET_CLASSES = [0, 1, 2, 3, 5, 7]
        # 0 = person
        # 1 = bicycle
        # 2 = car
        # 3 = motorcycle
        # 5 = bus
        # 7 = truck

    def detect(self, frame):
        # Run inference specifying the detected device
        results = self.model(frame, device=self.device, verbose=False)

        detections = []

        for result in results:
            boxes = result.boxes
            for box in boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                conf = float(box.conf[0])
                cls_id = int(box.cls[0])

                # Filter only selected classes
                if cls_id in self.TARGET_CLASSES and conf > 0.45:
                    detections.append((
                        x1,
                        y1,
                        x2,
                        y2,
                        conf,
                        cls_id
                    ))

        return detections