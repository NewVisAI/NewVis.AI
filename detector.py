import torch
from ultralytics import YOLO, RTDETR


class HumanDetector:
    def __init__(self, model_type="yolo", weights=None):
        self.model_type = model_type.lower()
        
        # Determine device
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[HumanDetector] Using execution device: {self.device.upper()}")

        if self.model_type == "rtdetr":
            model_weights = weights or "rtdetr-l.pt"
            print(f"[HumanDetector] Loading RT-DETR model with weights: {model_weights}...")
            self.model = RTDETR(model_weights)
        else:
            model_weights = weights or "yolov8s.pt"
            print(f"[HumanDetector] Loading YOLO model with weights: {model_weights}...")
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
                if cls_id in self.TARGET_CLASSES and conf > 0.20:
                    detections.append((
                        x1,
                        y1,
                        x2,
                        y2,
                        conf,
                        cls_id
                    ))

        return detections