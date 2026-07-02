import numpy as np
import supervision as sv


class PersonTracker:
    def __init__(self):
        self.tracker = sv.ByteTrack(
            track_activation_threshold=0.20,
            lost_track_buffer=150
        )

    def update(self, frame, detections):
        """
        detections format:
        [(x1, y1, x2, y2, conf, cls_id)]
        """
        print("\n================ FRAME START ================")
        print(f"[INFO] Total detections: {len(detections)}")

        if not detections:
            xyxy = np.empty((0, 4), dtype=np.float32)
            confidence = np.empty((0,), dtype=np.float32)
            class_id = np.empty((0,), dtype=np.int32)
        else:
            xyxy = np.array([[d[0], d[1], d[2], d[3]] for d in detections], dtype=np.float32)
            confidence = np.array([d[4] for d in detections], dtype=np.float32)
            class_id = np.array([d[5] for d in detections], dtype=np.int32)

        sv_detections = sv.Detections(
            xyxy=xyxy,
            confidence=confidence,
            class_id=class_id
        )

        # Update tracker
        tracked_detections = self.tracker.update_with_detections(sv_detections)
        print(f"[INFO] Tracks returned: {len(tracked_detections)}")

        results = []
        if tracked_detections.tracker_id is not None:
            for i in range(len(tracked_detections)):
                box = tracked_detections.xyxy[i]
                track_id = tracked_detections.tracker_id[i]
                cls_id = tracked_detections.class_id[i]

                print(f"[TRACK CONFIRMED] "
                      f"Track ID: {track_id} "
                      f"BBOX: ({int(box[0])}, {int(box[1])}, {int(box[2])}, {int(box[3])}) "
                      f"Class: {cls_id}")

                results.append((
                    int(box[0]),
                    int(box[1]),
                    int(box[2]),
                    int(box[3]),
                    int(track_id),
                    int(cls_id)
                ))

        print("[INFO] Final tracked objects:", len(results))
        print("================ FRAME END ==================\n")

        return results