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


def analytics_full_res() -> bool:
    """When true AND use_substream is on, the live tile keeps the fast SUB-stream but
    ANALYTICS decodes the full-res MAIN stream in a second capture (split decode). This
    gives OSNet sharp person crops so one person keeps a single global id across track
    breaks — low-res sub-stream crops score near the re-ID threshold and fragment one
    person into GID 1/2/3. Costs a second decode (CPU); off by default, opt-in per node."""
    v = os.environ.get("ANALYTICS_FULL_RES")
    if v not in (None, ""):
        return str(v).strip() in ("1", "true", "True")
    return bool(_load().get("analytics_full_res", False))


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
    # rtsp_transport;tcp + a finite stimeout -> a stalled camera can never block a
    # decode thread. Do NOT add fflags;nobuffer / flags;low_delay here: many camera
    # SUB-streams are HEVC/H.265 with reference frames, and those flags discard the
    # refs the decoder needs — measured on the Adiva sub-stream it HALVED the frame
    # rate (23 -> 9.5 fps) and flooded "Error constructing the frame RPS". Low
    # latency comes from decoding the light sub-stream (use_substream), not from
    # starving the decoder.
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


def motion_delta() -> bool:
    """When true, the analytics loop skips the detector pass on a frame whose content is
    essentially unchanged from the last processed one (a cheap grayscale frame-difference
    below MOTION_DELTA_THRESH) — exploiting the same temporal redundancy DeltaCNN-style
    methods do, but at the frame level and CPU-only. Applied ONLY while the adaptive
    person-gate is idle (empty camera), so a tracked or falling person is never skipped,
    and a MOTION_DELTA_FULLSCAN_S heartbeat forces a periodic full detector pass so a
    person who appears with little motion is still caught within a bounded delay.

    This is the compute-REDUCING half of the accuracy/speed work: on a static idle scene
    the detector goes near-silent instead of running at the idle rate. Lossless for a
    populated scene (YOLO keeps the gate active on any person, incl. a motionless/fallen
    one, so the skip only engages once nobody has been seen for the hangover). Off by
    default; enable per node once validated on that site's footage."""
    v = os.environ.get("MOTION_DELTA")
    if v not in (None, ""):
        return str(v).strip() in ("1", "true", "True")
    return bool(_load().get("motion_delta", False))


def motion_delta_thresh() -> float:
    """Fraction of changed pixels (0..1) below which an idle-camera frame counts as
    'static' and its detector pass is skipped (MOTION_DELTA). Measured on a small
    grayscale frame-diff with a per-pixel intensity tolerance, so sensor noise reads as
    ~zero change while a person entering reads well above it. Lower = more conservative."""
    override = os.environ.get("MOTION_DELTA_THRESH")
    if override not in (None, ""):
        try:
            return float(override)
        except ValueError:
            pass
    cfg = _load().get("motion_delta_thresh")
    return float(cfg) if isinstance(cfg, (int, float)) else 0.002


def motion_delta_fullscan_s() -> float:
    """Max seconds between forced full detector passes while MOTION_DELTA is skipping a
    static idle scene. Bounds how long a person who appears with little motion can wait
    before detection — a safety heartbeat mirroring motion gating's idle heartbeat."""
    override = os.environ.get("MOTION_DELTA_FULLSCAN_S")
    if override not in (None, ""):
        try:
            return float(override)
        except ValueError:
            pass
    cfg = _load().get("motion_delta_fullscan_s")
    return float(cfg) if isinstance(cfg, (int, float)) else 4.0


def pose_verify() -> bool:
    """When true, a pose model runs on the person crop ONLY when the cheap bbox fall
    heuristic already fired, to attach a keypoint-based confidence that the person is
    really in a fallen posture. It AUGMENTS the fall alert (pose_verified/pose_confidence)
    and NEVER suppresses it — a safety system must not drop a real fall because pose
    disagreed. Average compute stays ~zero because it fires only on the rare fall event.

    Default ON: augment-only, so it can never silence an alert; the recall gain on
    falls toward/away from the camera (where bbox never flips horizontal) is worth
    the near-zero cost. Set POSE_VERIFY=0 to disable on a node that lacks the pose
    weights or wants to skip the one-off model load on the first real fall."""
    v = os.environ.get("POSE_VERIFY")
    if v not in (None, ""):
        return str(v).strip() in ("1", "true", "True")
    return bool(_load().get("pose_verify", True))


def fall_stillness_seconds() -> float:
    """Window (video-time seconds) after a bbox-triggered fall during which the person's
    subsequent motion is watched. If they stay near-still for the whole window, the fall
    is CONFIRMED (stillness_confirmed=True, confidence bumps to "high"). If they get up
    inside it, stillness_confirmed=False and confidence drops. Only augments — never
    suppresses the alert. Default 3.0s balances confirmation latency against risk of a
    real fall the person recovers from quickly."""
    override = os.environ.get("FALL_STILLNESS_SECONDS")
    if override not in (None, ""):
        try:
            return float(override)
        except ValueError:
            pass
    cfg = _load().get("fall_stillness_seconds")
    return float(cfg) if isinstance(cfg, (int, float)) else 3.0


def fall_stillness_movement_ratio() -> float:
    """Maximum centroid movement (in bbox-heights) during the stillness window still
    counted as 'still'. Default 1.0 = the person may drift up to their own body-height
    (a rolled onto side / small squirm) and still confirm as fallen; a get-up jumps
    well beyond this. Tuned once per site once real footage exists."""
    override = os.environ.get("FALL_STILLNESS_MOVEMENT_RATIO")
    if override not in (None, ""):
        try:
            return float(override)
        except ValueError:
            pass
    cfg = _load().get("fall_stillness_movement_ratio")
    return float(cfg) if isinstance(cfg, (int, float)) else 1.0


def _running_float(env_var: str, json_key: str, default: float) -> float:
    override = os.environ.get(env_var)
    if override not in (None, ""):
        try:
            return float(override)
        except ValueError:
            pass
    cfg = _load().get(json_key)
    return float(cfg) if isinstance(cfg, (int, float)) else default


# --- Running detector (running.py) tunables ------------------------------------------- #
def running_speed_threshold() -> float:
    """Speed (bbox-heights per second) above which a track is 'running'. Bbox-heights
    normalisation gives distance invariance (a person near vs far reads the same).
    Default 2.5 was tuned by inspection on classroom hallway footage — validate per site."""
    return _running_float("RUNNING_SPEED_THRESHOLD", "running_speed_threshold", 2.5)


def running_min_samples() -> int:
    """Warmup: number of per-track updates before the speed reading is trusted.
    Kills the false positive from the very first frames where velocity is meaningless."""
    return int(_running_float("RUNNING_MIN_SAMPLES", "running_min_samples", 6))


def running_sustained_samples() -> int:
    """Consecutive updates in which speed must exceed threshold before firing (a single
    jittery-bbox spike does NOT count as running). Default 3 = ~0.1s at 30 fps."""
    return int(_running_float("RUNNING_SUSTAINED_SAMPLES", "running_sustained_samples", 3))


def running_direction_check() -> bool:
    """When true, in addition to sustained speed the smoothed velocity direction must
    stay coherent across the sustained window (dot product of successive velocity
    vectors > 0). A real run keeps direction; bbox jitter oscillates. Default on."""
    v = os.environ.get("RUNNING_DIRECTION_CHECK")
    if v not in (None, ""):
        return str(v).strip() in ("1", "true", "True")
    return bool(_load().get("running_direction_check", True))


def running_edge_margin_pct() -> float:
    """Fraction of the frame width/height near the boundary within which a bbox is
    considered 'at the edge' and suppressed for running (bbox distortion at edges
    inflates apparent speed). 0.05 = 5% margin. Set to 0 to disable edge suppression."""
    return _running_float("RUNNING_EDGE_MARGIN_PCT", "running_edge_margin_pct", 0.05)


def pose_verify_weights() -> str:
    """Weights for the fall-verification pose model. Defaults to ultralytics' pose net,
    which emits the same 17 COCO keypoints as PoseNet and reuses the YOLO stack already
    loaded (no new runtime). Point at an exported ONNX/TensorRT build on a GPU node."""
    return str(_resolve("pose_verify_weights", "POSE_VERIFY_WEIGHTS", "yolov8n-pose.pt"))


def pose_verify_thresh() -> float:
    """Torso-horizontal ratio (0..1) above which pose CONFIRMS a fall (pose_verified=True).
    0 = perfectly upright torso, 1 = perfectly horizontal (lying). Only affects the
    confidence flag on the alert, never whether the alert fires."""
    override = os.environ.get("POSE_VERIFY_THRESH")
    if override not in (None, ""):
        try:
            return float(override)
        except ValueError:
            pass
    cfg = _load().get("pose_verify_thresh")
    return float(cfg) if isinstance(cfg, (int, float)) else 0.5


# --- Heuristic altercation/anomaly detector (anomaly_detector.py) tunables ------------- #
def anomaly_detection() -> bool:
    """Master switch for the cheap motion-and-proximity altercation heuristic. Default ON.
    It is not a trained fight classifier; disable per node if a deployment doesn't want it."""
    v = os.environ.get("ANOMALY_DETECTION")
    if v not in (None, ""):
        return str(v).strip() in ("1", "true", "True")
    return bool(_load().get("anomaly_detection", True))


def _anomaly_float(env_var: str, json_key: str, default: float) -> float:
    override = os.environ.get(env_var)
    if override not in (None, ""):
        try:
            return float(override)
        except ValueError:
            pass
    cfg = _load().get(json_key)
    return float(cfg) if isinstance(cfg, (int, float)) else default


def anomaly_rel_motion_thresh() -> float:
    """Normalized RELATIVE motion between two people (body-heights/sec) required to count as a
    scuffle — they must move fast *against each other*, not in unison. Higher = fewer alerts."""
    return _anomaly_float("ANOMALY_REL_MOTION", "anomaly_rel_motion_thresh", 1.8)


def anomaly_move_floor() -> float:
    """Minimum normalized speed (body-heights/sec) EACH person must show. Excludes the
    one-person-moving-past-a-still-person false positive."""
    return _anomaly_float("ANOMALY_MOVE_FLOOR", "anomaly_move_floor", 0.4)


def anomaly_proximity_factor() -> float:
    """Two centres within this many average body-WIDTHS count as 'close'."""
    return _anomaly_float("ANOMALY_PROXIMITY", "anomaly_proximity_factor", 1.6)


def anomaly_streak() -> int:
    """Consecutive close+engaged checks required before firing (sustained proximity — a brief
    path-crossing never reaches it). Higher = fewer alerts."""
    return int(_anomaly_float("ANOMALY_STREAK", "anomaly_streak", 4))


def anomaly_cooldown_s() -> float:
    """Minimum seconds between alerts for the same pair."""
    return _anomaly_float("ANOMALY_COOLDOWN_S", "anomaly_cooldown_s", 8.0)


def anomaly_min_duration_seconds() -> float:
    """Minimum wall-clock duration (video-time seconds) the 'engaged' state must be
    sustained before firing, in addition to the frame-count streak. Makes the trigger
    FPS-independent — the same real interaction should fire whether the pipeline is
    running at 10 fps or 30 fps. Default 2.0s. A brief path-crossing is under this;
    a real scuffle sustains it."""
    return _anomaly_float("ANOMALY_MIN_DURATION_S", "anomaly_min_duration_seconds", 2.0)


def anomaly_oscillation_min_flips() -> int:
    """Minimum number of relative-velocity direction reversals ('oscillations') within
    the sustained window. Real shoving reverses direction (jostle back-and-forth); a
    single fast pass-by does not. 0 disables the check. Default 2 requires the pair
    to swap direction twice, which cleanly excludes co-directional fast movers."""
    return int(_anomaly_float("ANOMALY_OSCILLATION_MIN_FLIPS", "anomaly_oscillation_min_flips", 2))


def raised_arms_check() -> bool:
    """When true, on a triggered altercation a pose model runs on the two crops and
    'raised_arms' (wrist above shoulder) is attached to the alert details. Mirrors the
    POSE_VERIFY pattern — augment-only, never suppresses. Default OFF: unlike a fall
    (rare and single-person), altercations require TWO pose invocations and fire more
    often, so enable per-node once you've measured the cost is acceptable."""
    v = os.environ.get("RAISED_ARMS_CHECK")
    if v not in (None, ""):
        return str(v).strip() in ("1", "true", "True")
    return bool(_load().get("raised_arms_check", False))


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
