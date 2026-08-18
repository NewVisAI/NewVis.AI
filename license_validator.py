"""
License Validator and Camera Governance module.

Provides mechanisms to:
1. Lock software to a specific hardware node (MAC-based hardware fingerprinting).
2. Limit the number of cameras that can be registered and run simultaneously.
3. Gate individual product features per client (feature tiers — clients only get
   what they paid for).
4. Cryptographically verify license keys with Ed25519 signatures.

Security model: only the PUBLIC verification key ships with this software.
The PRIVATE signing key stays with the development team (see generate_key.py,
dev_keys/license_signing_key.pem — never distribute that file). A client
reading this source code can verify licenses but can NOT mint or alter one,
so trial limits, expiry dates, and camera caps cannot be forged.

Without any license the software runs in evaluation mode: 1 camera, core
features only. Trial/extension keys (e.g. 20 cameras for an evaluation,
10,000 on a full contract) are issued by the developers with generate_key.py.
"""

import base64
import binascii
import hashlib
import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Ed25519 PUBLIC verification key (safe to ship — cannot sign licenses).
# Regenerated locally on 2026-07-03 for this dev/test machine (the original
# team signing key was not present here); the matching private key lives in
# dev_keys/license_signing_key.pem. To hand off to real deployments, restore
# the team's public key here and re-issue licenses from the team's private key.
_PUBLIC_KEY_HEX = "fc1b893e9b92c984f340647283d129e1fc049ee30fbb8356d1c5d490b82391e8"

LICENSE_FILE = "license.key"
LICENSE_ENV_VAR = "SENTINEL_LICENSE"

# Product feature codes gated by the license "features" list.
ALL_FEATURES = [
    "core_tracking",       # detection / tracking / zones
    "nl_search",           # natural-language search
    "zone_alerts",         # restricted-area / after-hours / schedule alerts
    "fall_detection",
    "running_detection",
    "dress_code",
    "notifications",       # notification inbox + snapshot evidence
    "reports",             # headcount / periodic reports
    "api_access",          # REST/WebSocket backend
    "loitering",           # dwell / loitering timer
    "line_crossing",       # in/out counts
    "occupancy_alerts",    # per-zone density limits
    "pose_verification",   # event-gated pose confidence for falls
    "violence_detection",  # altercation / proximity+motion heuristic
    "ai_summary",          # AI activity summary
    "cross_camera_reid",   # global person ID across cameras
    "investigation_graph", # trace / cascades / recurring actors
]

# Features available with no license at all (evaluation mode).
EVALUATION_FEATURES = ["core_tracking", "nl_search"]
EVALUATION_MAX_CAMERAS = 1

# Product edition bundles used by marketing/pricing. Each bundle is the exact
# set of feature codes that ship with that edition. Editions are cumulative:
# Premium contains Basic; Pro contains Premium.
EDITION_BUNDLES: Dict[str, List[str]] = {
    "basic": [
        "core_tracking",
        "zone_alerts",
        "notifications",
        "reports",
        "api_access",
    ],
    "premium": [
        "core_tracking",
        "zone_alerts",
        "notifications",
        "reports",
        "api_access",
        "loitering",
        "line_crossing",
        "occupancy_alerts",
        "running_detection",
        "fall_detection",
        "dress_code",
    ],
    "pro": [
        "core_tracking",
        "zone_alerts",
        "notifications",
        "reports",
        "api_access",
        "loitering",
        "line_crossing",
        "occupancy_alerts",
        "running_detection",
        "fall_detection",
        "dress_code",
        "pose_verification",
        "violence_detection",
        "ai_summary",
        "nl_search",
        "cross_camera_reid",
        "investigation_graph",
    ],
}


def get_edition_features(edition: str) -> List[str]:
    """Return the feature-code bundle for a marketing edition name.

    Unknown editions return an empty list so callers fail closed rather than
    over-granting features.
    """
    return list(EDITION_BUNDLES.get(edition.lower(), []))


def get_hardware_fingerprint() -> str:
    """
    Generates a unique hardware fingerprint using the primary network MAC address.
    """
    mac_num = uuid.getnode()
    hasher = hashlib.sha256(str(mac_num).encode("utf-8"))
    return hasher.hexdigest()[:16].upper()


def load_license_key() -> str:
    """Reads the license key from license.key or the SENTINEL_LICENSE env var."""
    if os.path.exists(LICENSE_FILE):
        with open(LICENSE_FILE, "r", encoding="utf-8") as handle:
            key = handle.read().strip()
        if key:
            return key
    return os.environ.get(LICENSE_ENV_VAR, "").strip()


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def decode_license_payload(license_key: str) -> Optional[Dict[str, Any]]:
    """
    Verifies the Ed25519 signature and returns the license payload dict,
    or None if the key is missing/malformed/tampered.
    """
    if not license_key:
        return None

    parts = license_key.split(".")
    if len(parts) != 2:
        return None

    payload_b64, signature_b64 = parts
    try:
        payload_bytes = _b64url_decode(payload_b64)
        signature = _b64url_decode(signature_b64)
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(_PUBLIC_KEY_HEX))
        public_key.verify(signature, payload_bytes)
        return json.loads(payload_bytes.decode("utf-8"))
    except (InvalidSignature, ValueError, binascii.Error, json.JSONDecodeError):
        return None


def get_license_info(license_key: Optional[str] = None) -> Dict[str, Any]:
    """
    Returns the effective license state for display/enforcement:
    {mode, client_id, max_cameras, expiration, features, hardware_locked, valid, message}
    """
    if license_key is None:
        license_key = load_license_key()

    if not license_key:
        return {
            "mode": "evaluation",
            "client_id": "UNLICENSED",
            "max_cameras": EVALUATION_MAX_CAMERAS,
            "expiration": None,
            "features": list(EVALUATION_FEATURES),
            "hardware_locked": False,
            "valid": True,
            "message": (
                f"Evaluation mode (no license): limited to {EVALUATION_MAX_CAMERAS} camera "
                "and core features only."
            ),
        }

    payload = decode_license_payload(license_key)
    if payload is None:
        return {
            "mode": "invalid",
            "client_id": None,
            "max_cameras": 0,
            "expiration": None,
            "features": [],
            "hardware_locked": False,
            "valid": False,
            "message": "License signature verification failed (tampered or malformed key).",
        }

    exp_str = payload.get("expiration")
    if exp_str:
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d")
        except ValueError:
            return {
                "mode": "invalid",
                "client_id": payload.get("client_id"),
                "max_cameras": 0,
                "expiration": exp_str,
                "features": [],
                "hardware_locked": False,
                "valid": False,
                "message": "Invalid license expiration date format.",
            }
        # Compare at the date level to avoid timezone-naive datetime differences
        if datetime.now().date() > exp_date.date():
            return {
                "mode": "expired",
                "client_id": payload.get("client_id"),
                "max_cameras": 0,
                "expiration": exp_str,
                "features": [],
                "hardware_locked": False,
                "valid": False,
                "message": (
                    f"License expired on {exp_str}. Camera access revoked — "
                    "contact the developers to renew or extend."
                ),
            }

    allowed_hash = payload.get("hardware_hash")
    hardware_locked = bool(allowed_hash and allowed_hash != "ANY")
    if hardware_locked:
        current_fingerprint = get_hardware_fingerprint()
        allowed_hashes = [h.strip().upper() for h in allowed_hash.split(",") if h.strip()]
        if current_fingerprint not in allowed_hashes:
            return {
                "mode": "invalid",
                "client_id": payload.get("client_id"),
                "max_cameras": 0,
                "expiration": exp_str,
                "features": [],
                "hardware_locked": True,
                "valid": False,
                "message": (
                    f"Hardware mismatch. Licensed for {allowed_hash}, "
                    f"but running on {current_fingerprint}."
                ),
            }

    features = payload.get("features") or ["all"]
    if features == ["all"] or "all" in features:
        features = list(ALL_FEATURES)

    return {
        "mode": "licensed",
        "client_id": payload.get("client_id", "Unknown Client"),
        "max_cameras": int(payload.get("max_cameras", 0)),
        "expiration": exp_str,
        "features": features,
        "hardware_locked": hardware_locked,
        "valid": True,
        "message": (
            f"License verified for client '{payload.get('client_id', 'Unknown Client')}'. "
            f"Max cameras: {payload.get('max_cameras', 0)}. Expires: {exp_str or 'never'}."
        ),
    }


def verify_license(license_key: Optional[str], current_camera_count: int) -> Tuple[bool, str]:
    """
    Verifies signature, expiration, hardware lock, and camera limit.
    Returns (is_valid, message). An empty/missing key falls back to
    evaluation mode (1 camera) rather than failing outright.
    """
    info = get_license_info(license_key)
    if not info["valid"]:
        return False, info["message"]

    max_cameras = info["max_cameras"]
    if current_camera_count > max_cameras:
        return False, (
            f"Camera limit exceeded. Licensed for {max_cameras} camera(s), "
            f"but {current_camera_count} are configured. Contact the developers to extend."
        )

    return True, info["message"]


def is_feature_enabled(feature: str, license_key: Optional[str] = None) -> bool:
    info = get_license_info(license_key)
    return info["valid"] and feature in info["features"]
