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
