"""
Central inference / deployment configuration (single source of truth).

Which inference backend runs (GPU vs Edge-TPU vs NPU), the model paths, the
ONNX execution-provider preference, and the ReID match threshold are all
resolved here — from an optional ``deployment.json`` file, with environment
variables overriding it. Both ``detector.py`` and ``reid.py`` consult this
module instead of scattering ``os.getenv`` calls, so a deployment's tier can be
set in one place (and, later, driven by the license tier).

Precedence for every setting:  environment variable  >  deployment.json  >  default.

Example deployment.json (Budget/Edge tier):
    {
      "tier": "budget",
      "detector_weights": "models/yolov8s_320_edgetpu.tflite",
      "reid_tflite": "models/osnet_x1_0_edgetpu.tflite",
      "onnx_providers": ["OpenVINOExecutionProvider", "CPUExecutionProvider"]
    }
"""

import json
import os
from functools import lru_cache
from typing import List, Optional

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deployment.json")

# Recommended cosine-similarity thresholds per ReID backend. Quantized / generic
# models separate identities less cleanly than OSNet, so they need a lower bar.
# These are sane starting points — validate on real footage per deployment.
_DEFAULT_REID_THRESHOLDS = {
    "osnet": 0.80,      # purpose-built person-ReID (reference)
    "onnx": 0.80,       # OSNet exported to ONNX — same embedding space
    "fastreid": 0.80,
    "resnet50": 0.72,   # generic backbone, weaker separation
    "tflite": 0.62,     # INT8 Edge-TPU quantization loses precision
    "fallback": 0.80,   # colour histogram (handled separately anyway)
}

# ONNX execution providers, most-preferred first. onnxruntime silently ignores
# any that aren't installed, and reid.py intersects this with what's available.
_DEFAULT_ONNX_PROVIDERS = [
    "TensorrtExecutionProvider",   # NVIDIA
    "CUDAExecutionProvider",       # NVIDIA
    "OpenVINOExecutionProvider",   # Intel NPU / NCS2 / iGPU
    "CPUExecutionProvider",        # always available
]


@lru_cache(maxsize=1)
def _load() -> dict:
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, encoding="utf-8") as handle:
                return json.load(handle) or {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def reload() -> None:
    """Drop the cached config (call after editing deployment.json at runtime)."""
    _load.cache_clear()


def _resolve(json_key: str, env_var: str, default=None):
    value = os.environ.get(env_var)
    if value not in (None, ""):
        return value
    return _load().get(json_key, default)


def get_tier() -> str:
    return str(_resolve("tier", "SENTINEL_TIER", "professional"))


def get_detector_weights() -> Optional[str]:
    """Weights path for the detector; None lets detector.py pick its .pt default."""
    return _resolve("detector_weights", "DETECTOR_WEIGHTS", None)


def get_reid_tflite_path() -> str:
    return str(_resolve("reid_tflite", "REID_MODEL_TFLITE", "") or "")


def get_reid_onnx_path() -> str:
    return str(_resolve("reid_onnx", "REID_MODEL_ONNX", "") or "")


def get_onnx_providers() -> List[str]:
    cfg = _resolve("onnx_providers", "REID_ONNX_PROVIDERS", None)
    if isinstance(cfg, str) and cfg:
        return [p.strip() for p in cfg.split(",") if p.strip()]
    if isinstance(cfg, list) and cfg:
        return cfg
    return list(_DEFAULT_ONNX_PROVIDERS)


def reid_deferred() -> bool:
    """When true, OSNet Re-ID is NOT run in the live path — the cheap colour/edge
    fallback embedding is used for within-camera continuity, representative crops
    are saved, and full OSNet cross-camera matching runs only on-demand at search
    time (reid_search.py). This removes the single heaviest continuous GPU cost."""
    v = os.environ.get("REID_DEFERRED")
    if v not in (None, ""):
        return str(v).strip() in ("1", "true", "True")
    return bool(_load().get("reid_deferred", False))


def use_substream() -> bool:
    """When true, the analytics decoder pulls each camera's low-res SUB-stream
    instead of the full-res main stream. Person detection is unaffected at 720p,
    but decode cost (the usual bottleneck on GPU boxes) drops ~2x. The full-res
    main stream is left untouched for the NVR's evidence recording. Off by default
    so existing deployments are unchanged; enable per GPU node once validated."""
    v = os.environ.get("USE_SUBSTREAM")
    if v not in (None, ""):
        return str(v).strip() in ("1", "true", "True")
    return bool(_load().get("use_substream", False))


def to_substream_url(source, explicit: Optional[str] = None) -> str:
    """Derive the low-res sub-stream URL from a camera's main RTSP source.

    An explicit per-camera ``substream_source`` always wins. Local video files and
    unrecognised URL patterns are returned unchanged (analytics still runs — just
    without the decode saving), so this is always safe to call.
    """
    if explicit:
        return str(explicit)
    s = str(source)
    if not s.lower().startswith(("rtsp://", "rtmp://", "http://", "https://")):
        return s  # local video file — no sub-stream concept
    # Common IP-camera conventions, most-specific first:
    #   Adiva / Hikvision OEM : /ch1/main/av_stream -> /ch1/sub/av_stream
    #   Dahua                 : subtype=0            -> subtype=1
    for main_token, sub_token in (("/main/", "/sub/"), ("subtype=0", "subtype=1")):
        if main_token in s:
            return s.replace(main_token, sub_token, 1)
    # Unknown pattern: fall back to the main stream (correct, just no saving).
    return s


def decode_backend() -> str:
    """Video-decode backend for RTSP (lever ②): 'cpu' (software, default),
    'nvdec'/'cuda' (NVIDIA GPU), 'quicksync'/'qsv' (Intel iGPU), or 'vaapi'.
    Hardware backends offload decode off the CPU. IMPORTANT: they require an
    OpenCV/FFmpeg build that ships the hwaccel — the stock PyPI `opencv-python`
    wheel is CPU-only and will silently fall back to software decode. Verify on the
    GPU node via the CAP_PROP_HW_ACCELERATION log line the reader prints."""
    v = _resolve("decode_backend", "DECODE_BACKEND", "cpu")
    return (str(v).strip().lower() or "cpu")


_HWACCEL_TOKEN = {
    "nvdec": "cuda", "cuda": "cuda",
    "quicksync": "qsv", "qsv": "qsv",
    "vaapi": "vaapi",
}


def ffmpeg_capture_options() -> str:
    """OPENCV_FFMPEG_CAPTURE_OPTIONS string. Always forces TCP transport + a finite
    read timeout; appends an ``hwaccel`` when decode_backend() selects a hardware
    decoder. Set before the first cv2.VideoCapture; an explicit env override wins."""
    base = "rtsp_transport;tcp|stimeout;5000000"
    token = _HWACCEL_TOKEN.get(decode_backend())
    return f"{base}|hwaccel;{token}" if token else base


def motion_gating() -> bool:
    """When true, the analytics reader thins its DECODE rate on cameras that report
    no motion (via ONVIF), dropping to a slow heartbeat instead of decoding every
    frame — the decode saving the adaptive-rate gate can't give (that gate only
    throttles inference). Fail-safe: a camera with no working motion source is
    always treated as active (never skipped). Off by default."""
    v = os.environ.get("MOTION_GATING")
    if v not in (None, ""):
        return str(v).strip() in ("1", "true", "True")
    return bool(_load().get("motion_gating", False))


def frame_dedup() -> bool:
    """When true, the analytics loop skips a frame that is byte-identical to the one it
    just processed (lever #13, frame-dedup half; the embedding-cache half already lives
    in reid.py's dynamic-stride cache). Lossless — identical pixels give identical
    detections, so nothing is ever missed. A healthy camera's sensor noise makes frames
    ~never byte-identical, so this only pays off on a stalled/frozen feed (a stuck decoder
    repeating the last buffer); the per-frame signature it costs isn't worth imposing on
    every node, hence OFF by default. Enable per node with feeds known to freeze."""
    v = os.environ.get("FRAME_DEDUP")
    if v not in (None, ""):
        return str(v).strip() in ("1", "true", "True")
    return bool(_load().get("frame_dedup", False))


def motion_window_s() -> float:
    """Seconds after the last motion event during which a camera stays 'active'
    (full-rate decode). A person who triggers motion keeps the camera hot for this
    long even if the next event is late."""
    override = os.environ.get("MOTION_WINDOW_S")
    if override not in (None, ""):
        try:
            return float(override)
        except ValueError:
            pass
    cfg = _load().get("motion_window_s")
    return float(cfg) if isinstance(cfg, (int, float)) else 30.0


def idle_decode_fps() -> float:
    """Heartbeat decode rate on a motion-idle camera. Low enough to save decode,
    high enough that a person who appears without tripping motion is still caught
    quickly (then the person-gate/hangover takes over). Default 2 fps."""
    override = os.environ.get("IDLE_DECODE_FPS")
    if override not in (None, ""):
        try:
            return float(override)
        except ValueError:
            pass
    cfg = _load().get("idle_decode_fps")
    return float(cfg) if isinstance(cfg, (int, float)) else 2.0


def get_reid_similarity_threshold(backend: str) -> float:
    """Backend-aware match threshold. An explicit override always wins."""
    override = os.environ.get("REID_SIMILARITY_THRESHOLD")
    if override not in (None, ""):
        try:
            return float(override)
        except ValueError:
            pass
    cfg = _load().get("reid_similarity_threshold")
    if isinstance(cfg, (int, float)):
        return float(cfg)
    return _DEFAULT_REID_THRESHOLDS.get(backend, 0.80)
