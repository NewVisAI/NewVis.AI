"""
DEVELOPER-ONLY license generator. DO NOT ship this file or the dev_keys/
directory to clients — the private signing key inside dev_keys/ is what
keeps clients from minting their own licenses.

Examples:
  # 30-day Adiva trial: 20 cameras, all features, locked to their machine
  python generate_key.py --client ADIVA_TRIAL --preset trial --fingerprint <their-fingerprint> --save

  # Full contract: 10,000 cameras, all features, 1-year term
  python generate_key.py --client ADIVA_SCHOOL --cameras 10000 --expiry 2027-07-02 --fingerprint ANY

  # Feature-tiered license (client only paid for alerts + reports)
  python generate_key.py --client BUDGET_CLIENT --cameras 50 --expiry 2027-01-01 \
      --features core_tracking,nl_search,zone_alerts,reports
"""

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timedelta

# Windows consoles often default to a legacy code page that can't encode the
# emoji used in the messages below; a failed print must never crash the tool.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from license_validator import ALL_FEATURES, get_hardware_fingerprint

PRIVATE_KEY_PATH = os.path.join("dev_keys", "license_signing_key.pem")

PRESETS = {
    # (max_cameras, days_valid, features)
    "trial": (20, 30, ["all"]),
    "full": (10000, 365, ["all"]),
}


def _load_private_key() -> Ed25519PrivateKey:
    if not os.path.exists(PRIVATE_KEY_PATH):
        print(
            f"❌ Private signing key not found at {PRIVATE_KEY_PATH}.\n"
            "   This tool only works on the developers' machine. If you are a\n"
            "   developer setting up a new machine, copy the key from the team's\n"
            "   secure key store — do NOT generate a new one, or previously\n"
            "   issued licenses will stop verifying."
        )
        sys.exit(1)
    with open(PRIVATE_KEY_PATH, "rb") as handle:
        key = serialization.load_pem_private_key(handle.read(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        print("❌ dev_keys/license_signing_key.pem is not an Ed25519 key.")
        sys.exit(1)
    return key


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def generate_license(
    client_id: str,
    max_cameras: int,
    expiration_date: str,
    hardware_hash: str,
    features,
) -> str:
    payload = {
        "client_id": client_id,
        "max_cameras": max_cameras,
        "expiration": expiration_date,
        "hardware_hash": hardware_hash,
        "features": features,
        "issued": datetime.now().strftime("%Y-%m-%d"),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = _load_private_key().sign(payload_bytes)
    return f"{_b64url(payload_bytes)}.{_b64url(signature)}"


def main():
    parser = argparse.ArgumentParser(description="Sentinel AI CCTV License Key Generator (developer-only)")
    parser.add_argument("--client", type=str, required=True, help="Client/School name (e.g. ADIVA_SCHOOL)")
    parser.add_argument("--preset", choices=sorted(PRESETS), help="'trial' = 20 cameras / 30 days, 'full' = 10000 cameras / 1 year")
    parser.add_argument("--cameras", type=int, help="Maximum allowed cameras (overrides preset)")
    parser.add_argument("--expiry", type=str, help="Expiration date YYYY-MM-DD (overrides preset)")
    parser.add_argument(
        "--features",
        type=str,
        default=None,
        help=f"Comma-separated feature list, or 'all'. Available: {','.join(ALL_FEATURES)}",
    )
    parser.add_argument(
        "--fingerprint",
        type=str,
        default="",
        help="Client machine's hardware fingerprint. 'ANY' disables the hardware lock; blank uses this machine's.",
    )
    parser.add_argument("--save", action="store_true", help="Save the generated key directly to 'license.key'")

    args = parser.parse_args()

    max_cameras, days_valid, features = PRESETS.get(args.preset, (None, None, ["all"]))
    if args.cameras is not None:
        max_cameras = args.cameras
    if max_cameras is None:
        parser.error("Provide --cameras or --preset.")

    expiry = args.expiry or (datetime.now() + timedelta(days=days_valid or 365)).strftime("%Y-%m-%d")

    if args.features:
        requested = [f.strip() for f in args.features.split(",") if f.strip()]
        unknown = [f for f in requested if f != "all" and f not in ALL_FEATURES]
        if unknown:
            parser.error(f"Unknown feature(s): {', '.join(unknown)}")
        features = requested

    target_fingerprint = args.fingerprint.strip()
    if not target_fingerprint:
        target_fingerprint = get_hardware_fingerprint()
        print(f"Locked to current machine's fingerprint: {target_fingerprint}")
    elif target_fingerprint.upper() == "ANY":
        target_fingerprint = "ANY"
        print("Hardware lock: Disabled (key will work on any machine)")
    else:
        print(f"Locked to specified hardware fingerprint: {target_fingerprint}")

    key = generate_license(args.client, max_cameras, expiry, target_fingerprint, features)

    print("\n" + "=" * 50)
    print("GENERATED LICENSE KEY:")
    print("=" * 50)
    print(key)
    print("=" * 50)
    print(f"Client: {args.client} | Cameras: {max_cameras} | Expires: {expiry} | Features: {','.join(features)}")

    if args.save:
        with open("license.key", "w", encoding="utf-8") as f:
            f.write(key)
        print("\nSaved successfully to 'license.key'.")


if __name__ == "__main__":
    main()
