import os
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Set, Tuple

import cv2
import numpy as np

import inference_config
from event import normalize_object_type

STRONG_REID_THRESHOLD = 0.80
# Widened from 10s: lets a person keep their global id after a longer occlusion
# gap (walking behind a pillar, leaving frame briefly) instead of being handed a
# brand-new id, which is the main cause of one person fragmenting into many GIDs.
MAX_TIME_DIFF_SECONDS = 20.0
SOFT_MATCH_THRESHOLD = 0.45
# A soft (score-based) match may only fire if appearance similarity clears this
# floor. Stops the weak colour/time terms from ever merging two people who
# simply don't look alike — the guard that keeps the widened time window safe.
MIN_SOFT_REID_SIMILARITY = 0.55


def _normalize_embedding(vector: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vector)
    if norm == 0:
        return vector.astype(np.float32, copy=False)
    return (vector / norm).astype(np.float32, copy=False)


# --------------------------------------------------------------------------- #
# OSNet / torchreid preprocessing.
#
# The ONNX and Edge-TPU/TFLite backends must reproduce EXACTLY what torchreid's
# FeatureExtractor does, otherwise an OSNet model exported to ONNX/TFLite lands
# in a different embedding space than the GPU path and cross-camera matching
# silently degrades. torchreid resizes to 256x128 (HxW), scales to [0,1], then
# applies ImageNet mean/std normalisation.
# --------------------------------------------------------------------------- #
OSNET_INPUT_HW = (256, 128)  # (height, width)
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _preprocess_person_crop(crop: np.ndarray, input_hw, mean_std: bool = True) -> np.ndarray:
    """BGR crop -> HxWx3 float32 RGB in [0,1], optionally ImageNet-normalised."""
    height, width = input_hw
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (width, height)).astype(np.float32) / 255.0
    if mean_std:
        resized = (resized - _IMAGENET_MEAN) / _IMAGENET_STD
    return resized


def _clip_bbox(frame_shape, bbox: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    height, width = frame_shape[:2]
    x1, y1, x2, y2 = [int(value) for value in bbox]

    x1 = max(0, min(x1, width - 1))
    y1 = max(0, min(y1, height - 1))
    x2 = max(x1 + 1, min(x2, width))
    y2 = max(y1 + 1, min(y2, height))
    return x1, y1, x2, y2


def _bbox_iou(left: Tuple[int, int, int, int], right: Tuple[int, int, int, int]) -> float:
    left_x1, left_y1, left_x2, left_y2 = left
    right_x1, right_y1, right_x2, right_y2 = right

    inter_x1 = max(left_x1, right_x1)
    inter_y1 = max(left_y1, right_y1)
    inter_x2 = min(left_x2, right_x2)
    inter_y2 = min(left_y2, right_y2)

    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    intersection = inter_w * inter_h
    if intersection <= 0:
        return 0.0

    left_area = max(1, (left_x2 - left_x1) * (left_y2 - left_y1))
    right_area = max(1, (right_x2 - right_x1) * (right_y2 - right_y1))
    union = left_area + right_area - intersection
    if union <= 0:
        return 0.0
    return float(intersection / union)


def _extract_fallback_embedding(frame, bbox: Tuple[int, int, int, int]) -> np.ndarray:
    x1, y1, x2, y2 = _clip_bbox(frame.shape, bbox)
    frame_height, frame_width = frame.shape[:2]
    box_width = max(1, x2 - x1)
    box_height = max(1, y2 - y1)
    frame_area = max(1, frame_width * frame_height)

    geometry_features = np.array(
        [
            box_width / max(1.0, float(frame_width)),
            box_height / max(1.0, float(frame_height)),
            (box_width * box_height) / float(frame_area),
            min((box_width / max(1.0, float(box_height))) / 4.0, 1.0),
        ],
        dtype=np.float32,
    )

    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        histogram = np.zeros(48, dtype=np.float32)
        edge_hist = np.zeros(32, dtype=np.float32)
    else:
        resized = cv2.resize(crop, (64, 128), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
        hist_h = cv2.calcHist([hsv], [0], None, [16], [0, 180]).flatten()
        hist_s = cv2.calcHist([hsv], [1], None, [16], [0, 256]).flatten()
        hist_v = cv2.calcHist([hsv], [2], None, [16], [0, 256]).flatten()
        histogram = np.concatenate(
            [
                hist_h.astype(np.float32),
                hist_s.astype(np.float32),
                hist_v.astype(np.float32),
            ]
        )

        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 100, 200)
        edge_hist = cv2.calcHist([edges], [0], None, [32], [0, 256]).flatten().astype(np.float32)

    embedding = np.concatenate([histogram, edge_hist, geometry_features * 4.0])
    return _normalize_embedding(embedding)


def extract_detection_features(frame, bbox: Tuple[int, int, int, int]) -> np.ndarray:
    return _extract_fallback_embedding(frame, bbox)


def extract_color_histogram(frame, bbox: Tuple[int, int, int, int]) -> Optional[np.ndarray]:
    return extract_shirt_color(frame, bbox)


def extract_shirt_color(frame, bbox: Tuple[int, int, int, int]) -> Optional[np.ndarray]:
    x1, y1, x2, y2 = _clip_bbox(frame.shape, bbox)
    box_height = max(1, y2 - y1)
    upper_y1 = y1 + int(box_height * 0.18)
    upper_y2 = y1 + int(box_height * 0.55)
    upper_y2 = max(upper_y1 + 1, min(upper_y2, y2))

    box_width = max(1, x2 - x1)
    center_x1 = x1 + int(box_width * 0.35)
    center_x2 = x1 + int(box_width * 0.65)
    center_x2 = max(center_x1 + 1, min(center_x2, x2))

    crop = frame[upper_y1:upper_y2, center_x1:center_x2]
    if crop.size == 0:
        return None

    rgb_crop = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    return np.mean(rgb_crop.reshape(-1, 3), axis=0).astype(np.float32)


def color_distance(left: Optional[np.ndarray], right: Optional[np.ndarray]) -> float:
    if left is None or right is None:
        return 0.0
    if left.size != 3 or right.size != 3:
        return 0.0
    max_distance = float(np.sqrt(3 * (255.0 ** 2)))
    return min(float(np.linalg.norm(left.astype(np.float32) - right.astype(np.float32)) / max_distance), 1.0)


def color_similarity(left: Optional[np.ndarray], right: Optional[np.ndarray]) -> float:
    if left is None or right is None:
        return 0.0
    return 1.0 - color_distance(left, right)


def cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    if left.size == 0 or right.size == 0:
        return 0.0
    return float(np.dot(left, right))


class NeuralReIDEmbedder:
    def __init__(self):
        self.backend_name = "fallback"
        self.embedding_size = 84
        self._model = None
        self._osnet = None
        self._device = "cpu"
        self._preprocess = None
        self._build_backend()

    def _build_backend(self) -> None:
        # -2. Attempt EdgeTPU / TFLite (Prioritize for embedded deployments)
        tflite_weights = inference_config.get_reid_tflite_path()
        if tflite_weights and os.path.exists(tflite_weights):
            try:
                # Try pycoral (Edge TPU) first, fall back to plain tflite_runtime.
                # A bare tflite_runtime can also load an _edgetpu.tflite as long as
                # the Edge TPU delegate is registered; pycoral is the happy path.
                try:
                    from pycoral.utils.edgetpu import make_interpreter
                    self._model = make_interpreter(tflite_weights)
                except Exception:
                    import tflite_runtime.interpreter as tflite
                    self._model = tflite.Interpreter(model_path=tflite_weights)

                self._model.allocate_tensors()
                self._input_details = self._model.get_input_details()
                self._output_details = self._model.get_output_details()

                self.backend_name = "tflite"
                # Infer embedding size from output tensor shape
                self.embedding_size = int(self._output_details[0]['shape'][-1])
                print(f"[NeuralReIDEmbedder] Edge TPU / TFLite ReID initialized with {tflite_weights}.")
                return
            except Exception as e:
                print(f"[NeuralReIDEmbedder] TFLite initialization failed ({e}); falling back...")

        # -1. Attempt ONNX / NPU
        onnx_weights = inference_config.get_reid_onnx_path()
        if onnx_weights and os.path.exists(onnx_weights):
            try:
                import onnxruntime as ort
                # Explicitly select providers by preference, intersected with what
                # this onnxruntime build actually has. Plain `onnxruntime` only
                # ships CPU; NPU needs `onnxruntime-openvino`, GPU needs
                # `onnxruntime-gpu` — so we log which provider actually bound.
                preferred = inference_config.get_onnx_providers()
                available = set(ort.get_available_providers())
                providers = [p for p in preferred if p in available] or ["CPUExecutionProvider"]
                self._model = ort.InferenceSession(onnx_weights, providers=providers)
                active = self._model.get_providers()
                self.backend_name = "onnx"
                self.embedding_size = int(self._model.get_outputs()[0].shape[-1])
                print(f"[NeuralReIDEmbedder] ONNX ReID initialized with {onnx_weights} "
                      f"(provider: {active[0] if active else 'unknown'}).")
                if active and active[0] == "CPUExecutionProvider":
                    print("[NeuralReIDEmbedder] NOTE: running ONNX on CPU. For NPU install "
                          "'onnxruntime-openvino'; for GPU install 'onnxruntime-gpu'.")
                return
            except Exception as e:
                print(f"[NeuralReIDEmbedder] ONNX initialization failed ({e}); falling back...")

        # 0. Attempt OSNet (torchreid) — a purpose-built person-ReID architecture,
        #    far better at separating identities than a generic classification net.
        #    Uses ImageNet-pretrained OSNet by default; drop in Market-1501 / campus
        #    weights by setting OSNET_WEIGHTS to a .pth path (no code change needed).
        try:
            import torch
            from torchreid.reid.utils import FeatureExtractor

            device = "cuda" if torch.cuda.is_available() else "cpu"
            weights = os.getenv("OSNET_WEIGHTS", "")
            model_path = weights if weights and os.path.exists(weights) else ""
            self._osnet = FeatureExtractor(
                model_name=os.getenv("OSNET_MODEL", "osnet_x1_0"),
                model_path=model_path,
                device=device,
            )
            self.backend_name = "osnet"
            self.embedding_size = 512
            tag = "custom/market weights" if model_path else "imagenet-pretrained"
            print(f"[NeuralReIDEmbedder] OSNet ReID initialized ({tag}).")
            return
        except Exception as e:
            print(f"[NeuralReIDEmbedder] OSNet unavailable ({e}); falling back to ResNet50/FastReID.")

        # 1. Attempt FastReID next (if config and weights exist)
        config_path = os.getenv("FASTREID_CONFIG")
        weights_path = os.getenv("FASTREID_WEIGHTS")
        if config_path and weights_path and os.path.exists(config_path) and os.path.exists(weights_path):
            try:
                from fastreid.config import get_cfg
                from fastreid.engine.defaults import DefaultPredictor
                import torch
                
                device = "cuda" if torch.cuda.is_available() else "cpu"
                cfg = get_cfg()
                cfg.merge_from_file(config_path)
                cfg.MODEL.WEIGHTS = weights_path
                if hasattr(cfg.MODEL, "DEVICE"):
                    cfg.MODEL.DEVICE = device
                self._model = DefaultPredictor(cfg)
                self.backend_name = "fastreid"
                self.embedding_size = 2048 # typical for FastReID ResNet50
                print("[NeuralReIDEmbedder] Successfully initialized FastReID backend.")
                return
            except Exception as e:
                print(f"[NeuralReIDEmbedder] Failed to initialize FastReID: {e}. Falling back to PyTorch ResNet50.")

        # 2. Attempt PyTorch ResNet50 next
        try:
            import torch
            import torchvision.models as models
            import torchvision.transforms as T
            from torchvision.models import ResNet50_Weights

            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            print(f"[NeuralReIDEmbedder] Loading pre-trained ResNet50 on {self._device.upper()}...")
            
            # Load resnet50 and strip classifier
            model = models.resnet50(weights=ResNet50_Weights.DEFAULT)
            model.fc = torch.nn.Identity()
            model.to(self._device)
            model.eval()
            self._model = model

            self._preprocess = T.Compose([
                T.ToPILImage(),
                T.Resize((128, 64)),
                T.ToTensor(),
                T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
            ])

            self.backend_name = "resnet50"
            self.embedding_size = 2048
            print("[NeuralReIDEmbedder] ResNet50 Neural ReID initialized successfully.")
        except Exception as e:
            self.backend_name = "fallback"
            self.embedding_size = 84
            print(f"[NeuralReIDEmbedder] Failed to initialize ResNet50: {e}. Using fallback histogram embeddings.")

    def extract(self, frame, bbox: Tuple[int, int, int, int]) -> np.ndarray:
        x1, y1, x2, y2 = _clip_bbox(frame.shape, bbox)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return _extract_fallback_embedding(frame, bbox)

        if self.backend_name == "tflite" and self._model is not None:
            try:
                # TFLite input is NHWC. Read the model's real size instead of guessing.
                detail = self._input_details[0]
                _, input_h, input_w, _ = detail['shape']
                # OSNet-parity float preprocessing (RGB, [0,1], ImageNet mean/std).
                proc = _preprocess_person_crop(crop, (int(input_h), int(input_w)), mean_std=True)

                dtype = detail['dtype']
                if dtype in (np.uint8, np.int8):
                    # Apply the model's quantization: q = value/scale + zero_point.
                    scale, zero_point = detail.get('quantization', (0.0, 0))
                    if scale and scale > 0:
                        proc = proc / scale + zero_point
                    input_data = np.expand_dims(proc, axis=0).astype(dtype)
                else:
                    input_data = np.expand_dims(proc, axis=0).astype(np.float32)

                self._model.set_tensor(detail['index'], input_data)
                self._model.invoke()
                output = self._model.get_tensor(self._output_details[0]['index'])
                # De-quantize the output embedding if it came back INT8.
                out_detail = self._output_details[0]
                if out_detail['dtype'] in (np.uint8, np.int8):
                    o_scale, o_zero = out_detail.get('quantization', (0.0, 0))
                    if o_scale and o_scale > 0:
                        output = (output.astype(np.float32) - o_zero) * o_scale
                vector = np.asarray(output, dtype=np.float32).reshape(-1)
                if vector.size > 0:
                    return _normalize_embedding(vector)
            except Exception as e:
                print(f"[NeuralReIDEmbedder] TFLite extract error: {e}")

        elif self.backend_name == "onnx" and self._model is not None:
            try:
                # ONNX input is NCHW. Read the model's real HxW; default to OSNet's.
                inp = self._model.get_inputs()[0]
                shape = inp.shape
                input_h = shape[2] if len(shape) == 4 and isinstance(shape[2], int) else OSNET_INPUT_HW[0]
                input_w = shape[3] if len(shape) == 4 and isinstance(shape[3], int) else OSNET_INPUT_HW[1]
                # OSNet-parity preprocessing, then HWC -> CHW -> NCHW.
                proc = _preprocess_person_crop(crop, (int(input_h), int(input_w)), mean_std=True)
                input_data = np.expand_dims(np.transpose(proc, (2, 0, 1)), axis=0).astype(np.float32)

                result = self._model.run(None, {inp.name: input_data})
                vector = np.asarray(result[0], dtype=np.float32).reshape(-1)
                if vector.size > 0:
                    return _normalize_embedding(vector)
            except Exception as e:
                print(f"[NeuralReIDEmbedder] ONNX extract error: {e}")

        if self.backend_name == "osnet" and self._osnet is not None:
            try:
                # torchreid FeatureExtractor expects RGB numpy; our crop is BGR.
                rgb_crop = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                features = self._osnet([rgb_crop])
                vector = features.detach().cpu().numpy().reshape(-1)
                if vector.size > 0:
                    return _normalize_embedding(vector)
            except Exception:
                pass

        if self.backend_name == "fastreid" and self._model is not None:
            try:
                features = self._model(crop)
                if hasattr(features, "detach"):
                    features = features.detach()
                if hasattr(features, "cpu"):
                    features = features.cpu()
                vector = np.asarray(features, dtype=np.float32).reshape(-1)
                if vector.size > 0:
                    self.embedding_size = int(vector.size)
                    return _normalize_embedding(vector)
            except Exception:
                pass

        elif self.backend_name == "resnet50" and self._model is not None:
            try:
                import torch
                # BGR to RGB
                rgb_crop = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                input_tensor = self._preprocess(rgb_crop).unsqueeze(0).to(self._device)
                
                with torch.no_grad():
                    features = self._model(input_tensor)
                
                vector = features.squeeze().cpu().numpy()
                if vector.size > 0:
                    return _normalize_embedding(vector)
            except Exception as e:
                print(f"[NeuralReIDEmbedder] ResNet50 inference error: {e}")
                pass

        # Fallback to local histograms/geometry
        return _extract_fallback_embedding(frame, bbox)


@dataclass
class IdentityRecord:
    global_id: int
    object_type: str
    embedding: np.ndarray
    embedding_history: Deque[np.ndarray]
    shirt_color: Optional[np.ndarray]
    color_history: Deque[np.ndarray]
    last_camera_id: int
    last_seen_time: float
    camera_history: List[int]


@dataclass
class EmbeddingCacheRecord:
    bbox: Tuple[int, int, int, int]
    embedding: np.ndarray
    last_seen_time: float


class GlobalIdentityManager:
    def __init__(
        self,
        similarity_threshold: Optional[float] = None,
        spatial_threshold: float = 150.0,
        cross_camera_similarity_threshold: Optional[float] = None,
        match_window_seconds: float = 20.0,
        embedding_cache_ttl_seconds: float = 0.75,
        embedding_cache_iou_threshold: float = 0.85,
        score_threshold: float = 0.45,
        embedding_memory_size: int = 16,
    ):
        # Build the embedder first so we know which backend is active, then pick a
        # backend-appropriate match threshold (Edge-TPU/INT8 embeddings separate
        # identities less cleanly than OSNet, so they need a lower bar). An
        # explicit similarity_threshold argument always wins.
        self.embedder = NeuralReIDEmbedder()
        if similarity_threshold is None:
            similarity_threshold = inference_config.get_reid_similarity_threshold(
                self.embedder.backend_name
            )
            print(f"[GlobalIdentityManager] ReID backend '{self.embedder.backend_name}' "
                  f"-> match threshold {similarity_threshold:.2f}")
        self.similarity_threshold = similarity_threshold
        self.spatial_threshold = spatial_threshold
        self.cross_camera_similarity_threshold = (
            cross_camera_similarity_threshold or similarity_threshold
        )
        self.match_window_seconds = match_window_seconds
        self.embedding_cache_ttl_seconds = embedding_cache_ttl_seconds
        self.embedding_cache_iou_threshold = embedding_cache_iou_threshold
        self.score_threshold = score_threshold
        self.embedding_memory_size = max(1, int(embedding_memory_size))
        self.next_global_id = 1
        self.global_id_map: Dict[int, np.ndarray] = {}
        self.track_id_to_global_id: Dict[Tuple[int, int], int] = {}
        self.local_to_global = self.track_id_to_global_id
        self.identity_store: Dict[int, IdentityRecord] = {}
        self.camera_time_assignments: Dict[Tuple[int, float], Set[int]] = {}
        self.embedding_cache: Dict[Tuple[int, int], EmbeddingCacheRecord] = {}
        self.track_frame_counters: Dict[Tuple[int, int], int] = {}
        self.embedding_size = self.embedder.embedding_size

    def _color_similarity(
        self,
        left: Optional[np.ndarray],
        right: Optional[np.ndarray],
    ) -> float:
        return color_similarity(left, right)

    def _log_match_attempt(
        self,
        candidate_gid: int,
        reid_similarity: float,
        color_similarity: float,
        time_diff: float,
        final_score: float,
        decision: str,
    ) -> None:
        print(
            "[DEBUG MATCH]\n"
            f"GID: {candidate_gid}\n"
            f"ReID: {reid_similarity:.4f}\n"
            f"Color: {color_similarity:.4f}\n"
            f"Time: {time_diff:.2f}\n"
            f"Score: {final_score:.4f}\n"
            f"Decision: {decision}"
        )

    def _allocate_global_id(self) -> int:
        global_id = self.next_global_id
        self.next_global_id += 1
        return global_id

    def _cross_camera_allowed(self, record_camera_id, camera_id, time_diff) -> bool:
        """A cross-camera re-id match is only plausible if the two cameras are
        adjacent in the topology graph AND the time gap is within that edge's
        max transit time. Same camera is always allowed; if no topology is
        configured we fail open (old single-graph behaviour)."""
        try:
            if int(record_camera_id) == int(camera_id):
                return True
        except (TypeError, ValueError):
            return True
        try:
            from camera_registry import transit_seconds
            allowed = transit_seconds(record_camera_id, camera_id)
        except Exception:
            return True
        if allowed is None:
            return False  # not adjacent — a person can't teleport between them
        return abs(float(time_diff)) <= allowed

    def _is_valid_global_id(self, global_id: Optional[int]) -> bool:
        return isinstance(global_id, int) and global_id > 0

    def _get_embedding(
        self,
        camera_id: int,
        track_id: int,
        frame,
        bbox: Tuple[int, int, int, int],
        current_time: float,
    ) -> np.ndarray:
        local_key = (camera_id, track_id)
        cached = self.embedding_cache.get(local_key)
        if cached is not None:
            if (
                abs(float(current_time) - float(cached.last_seen_time)) <= self.embedding_cache_ttl_seconds
                and _bbox_iou(cached.bbox, bbox) >= self.embedding_cache_iou_threshold
            ):
                return cached.embedding

        embedding = self.embedder.extract(frame, bbox)
        self.embedding_size = self.embedder.embedding_size
        self.embedding_cache[local_key] = EmbeddingCacheRecord(
            bbox=bbox,
            embedding=embedding,
            last_seen_time=float(current_time),
        )
        return embedding

    def assign_global_id(
        self,
        camera_id: int,
        track_id: int,
        frame,
        bbox: Tuple[int, int, int, int],
        object_type: str,
        current_time: float,
        active_track_count: int = 1,
    ) -> int:
        local_key = (camera_id, track_id)
        object_type = normalize_object_type(object_type)
        
        # Track frame counters for lazy ReID extraction
        count = self.track_frame_counters.get(local_key, 0)
        self.track_frame_counters[local_key] = count + 1
        
        existing_global_id = self.track_id_to_global_id.get(local_key)
        
        # Determine dynamic ReID stride based on crowd density
        if active_track_count <= 2:
            stride = 5
        elif active_track_count <= 5:
            stride = 15
        else:
            stride = 30
            
        # Lazy ReID embedding: only run model if track is new or every N frames
        need_embedding = (existing_global_id is None) or (count % stride == 0)
        
        if not need_embedding:
            cached = self.embedding_cache.get(local_key)
            if cached is not None:
                embedding = cached.embedding
                # Keep cache fresh
                cached.bbox = bbox
                cached.last_seen_time = float(current_time)
            else:
                embedding = self._get_embedding(camera_id, track_id, frame, bbox, current_time)
        else:
            embedding = self._get_embedding(camera_id, track_id, frame, bbox, current_time)
            
        shirt_color = extract_shirt_color(frame, bbox)
        self._prune_runtime_caches(current_time)

        existing_global_id = self.track_id_to_global_id.get(local_key)
        if self._is_valid_global_id(existing_global_id):
            print(
                "[MATCH FOUND] "
                f"GID {existing_global_id} reused for camera {camera_id} track {track_id} "
                "(existing local track)"
            )
            self._refresh_record(
                existing_global_id,
                object_type,
                embedding,
                shirt_color,
                camera_id,
                current_time,
            )
            self._mark_camera_time_assignment(camera_id, current_time, existing_global_id)
            return existing_global_id

        if local_key in self.track_id_to_global_id:
            self.track_id_to_global_id.pop(local_key, None)

        matched_global_id = self._match_existing_identity(
            camera_id,
            object_type,
            embedding,
            shirt_color,
            current_time,
        )

        if not self._is_valid_global_id(matched_global_id):
            matched_global_id = self._allocate_global_id()
            print(
                "[NEW ID CREATED] "
                f"GID {matched_global_id} for camera {camera_id} track {track_id}"
            )

        self._refresh_record(
            matched_global_id,
            object_type,
            embedding,
            shirt_color,
            camera_id,
            current_time,
        )

        self.track_id_to_global_id[local_key] = matched_global_id
        self._mark_camera_time_assignment(camera_id, current_time, matched_global_id)
        return matched_global_id

    def _assignment_slot_key(self, camera_id: int, current_time: float) -> Tuple[int, float]:
        return camera_id, round(float(current_time), 3)

    def _mark_camera_time_assignment(self, camera_id: int, current_time: float, global_id: int) -> None:
        slot_key = self._assignment_slot_key(camera_id, current_time)
        assigned_global_ids = self.camera_time_assignments.setdefault(slot_key, set())
        assigned_global_ids.add(global_id)

    def _prune_runtime_caches(self, current_time: float) -> None:
        stale_assignment_keys = [
            slot_key
            for slot_key in self.camera_time_assignments
            if abs(slot_key[1] - float(current_time)) >= self.match_window_seconds
        ]
        for slot_key in stale_assignment_keys:
            self.camera_time_assignments.pop(slot_key, None)

        stale_embedding_keys = [
            cache_key
            for cache_key, record in self.embedding_cache.items()
            if abs(float(record.last_seen_time) - float(current_time))
            > max(self.match_window_seconds, self.embedding_cache_ttl_seconds)
        ]
        for cache_key in stale_embedding_keys:
            self.embedding_cache.pop(cache_key, None)

        # Evict identities that have not been seen for longer than the ReID match window
        stale_identities = [
            gid
            for gid, record in self.identity_store.items()
            if abs(float(current_time) - float(record.last_seen_time)) > max(600.0, self.match_window_seconds * 5.0)
        ]
        for gid in stale_identities:
            self.identity_store.pop(gid, None)
            self.global_id_map.pop(gid, None)

    def _match_existing_identity(
        self,
        camera_id: int,
        object_type: str,
        embedding: np.ndarray,
        shirt_color: Optional[np.ndarray],
        current_time: float,
    ) -> Optional[int]:
        object_type = normalize_object_type(object_type)

        best_global_id = None
        best_score = float("-inf")
        best_time_diff = float("inf")
        assigned_in_same_camera_slot = self.camera_time_assignments.get(
            self._assignment_slot_key(camera_id, current_time),
            set(),
        )

        for global_id, record in self.identity_store.items():
            if record.object_type != object_type:
                continue

            if global_id in assigned_in_same_camera_slot:
                continue

            # Topology gate: block physically impossible cross-camera hand-offs.
            if not self._cross_camera_allowed(record.last_camera_id, camera_id,
                                              float(current_time) - float(record.last_seen_time)):
                continue

            reid_similarity = cosine_similarity(embedding, record.embedding)
            color_similarity = self._color_similarity(shirt_color, record.shirt_color)
            time_diff = float(current_time) - float(record.last_seen_time)

            if time_diff < 0 or time_diff >= self.match_window_seconds:
                self._log_match_attempt(
                    candidate_gid=global_id,
                    reid_similarity=reid_similarity,
                    color_similarity=color_similarity,
                    time_diff=abs(time_diff),
                    final_score=float("-inf"),
                    decision="new_id(candidate_filter)",
                )
                continue

            if reid_similarity > STRONG_REID_THRESHOLD:
                self._log_match_attempt(
                    candidate_gid=global_id,
                    reid_similarity=reid_similarity,
                    color_similarity=color_similarity,
                    time_diff=abs(time_diff),
                    final_score=reid_similarity,
                    decision="match(stage1_strong_reid)",
                )
                print(f"[STRONG MATCH] GID {global_id}")
                print(f"[MATCH FOUND] GID {global_id}")
                return global_id

            # Appearance floor: never let colour/time carry a soft match when the
            # two crops don't actually look alike. Prevents different people from
            # merging into one global id under the widened time window.
            if reid_similarity < MIN_SOFT_REID_SIMILARITY:
                self._log_match_attempt(
                    candidate_gid=global_id,
                    reid_similarity=reid_similarity,
                    color_similarity=color_similarity,
                    time_diff=abs(time_diff),
                    final_score=float("-inf"),
                    decision="new_id(below_reid_floor)",
                )
                continue

            time_penalty = min(abs(time_diff) / MAX_TIME_DIFF_SECONDS, 1.0)
            score = (
                0.7 * reid_similarity
                + 0.05 * color_similarity
                - 0.2 * time_penalty
            )

            decision = "match(stage2_soft)" if score > self.score_threshold else "new_id"
            self._log_match_attempt(
                candidate_gid=global_id,
                reid_similarity=reid_similarity,
                color_similarity=color_similarity,
                time_diff=abs(time_diff),
                final_score=score,
                decision=decision,
            )

            if score <= self.score_threshold:
                continue

            if score < best_score:
                continue

            if score == best_score and time_diff >= best_time_diff:
                continue

            best_score = score
            best_time_diff = time_diff
            best_global_id = global_id

        if best_global_id is not None:
            print(f"[MATCH FOUND] GID {best_global_id}")

        return best_global_id

    def _refresh_record(
        self,
        global_id: int,
        object_type: str,
        embedding: np.ndarray,
        shirt_color: Optional[np.ndarray],
        camera_id: int,
        current_time: float,
    ) -> None:
        object_type = normalize_object_type(object_type)
        record = self.identity_store.get(global_id)
        if record is None:
            history = deque([embedding], maxlen=self.embedding_memory_size)
            avg_embedding = _normalize_embedding(np.mean(np.stack(history), axis=0))
            color_history: Deque[np.ndarray] = deque(maxlen=self.embedding_memory_size)
            if shirt_color is not None:
                color_history.append(shirt_color)
            self.global_id_map[global_id] = avg_embedding
            self.identity_store[global_id] = IdentityRecord(
                global_id=global_id,
                object_type=object_type,
                embedding=avg_embedding,
                embedding_history=history,
                shirt_color=self._average_color(color_history),
                color_history=color_history,
                last_camera_id=camera_id,
                last_seen_time=float(current_time),
                camera_history=[camera_id],
            )
            return

        record.embedding_history.append(embedding)
        avg_embedding = _normalize_embedding(np.mean(np.stack(record.embedding_history), axis=0))
        record.embedding = avg_embedding
        self.global_id_map[global_id] = avg_embedding
        record.object_type = object_type
        if shirt_color is not None:
            record.color_history.append(shirt_color)
            record.shirt_color = self._average_color(record.color_history)
        record.last_camera_id = camera_id
        record.last_seen_time = float(current_time)
        record.camera_history.append(camera_id)
        if len(record.camera_history) > self.embedding_memory_size:
            record.camera_history = record.camera_history[-self.embedding_memory_size:]

    def _average_color(self, color_history: Deque[np.ndarray]) -> Optional[np.ndarray]:
        if not color_history:
            return None
        return np.mean(np.stack(color_history), axis=0).astype(np.float32)

    def clear_camera_track_mappings(self, camera_id: Optional[int] = None) -> None:
        if camera_id is None:
            self.track_id_to_global_id.clear()
            self.camera_time_assignments.clear()
            self.embedding_cache.clear()
            self.track_frame_counters.clear()
            return

        stale_track_keys = [key for key in self.track_id_to_global_id if key[0] == camera_id]
        for key in stale_track_keys:
            self.track_id_to_global_id.pop(key, None)
            self.track_frame_counters.pop(key, None)

        stale_assignment_keys = [key for key in self.camera_time_assignments if key[0] == camera_id]
        for key in stale_assignment_keys:
            self.camera_time_assignments.pop(key, None)

        stale_embedding_keys = [key for key in self.embedding_cache if key[0] == camera_id]
        for key in stale_embedding_keys:
            self.embedding_cache.pop(key, None)
