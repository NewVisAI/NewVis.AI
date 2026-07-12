import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import generate_key
import license_validator
from generate_key import generate_license
from license_validator import (
    ALL_FEATURES,
    EVALUATION_MAX_CAMERAS,
    get_hardware_fingerprint,
    get_license_info,
    is_feature_enabled,
    verify_license,
)


class LicenseValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sign with an ephemeral in-memory keypair so the suite does not depend
        # on the developer-only dev_keys/ private key (absent on client/CI
        # machines). Point the validator's public key at this keypair so the
        # full sign -> verify round trip runs end-to-end.
        cls._ephemeral_key = Ed25519PrivateKey.generate()
        cls._orig_public_hex = license_validator._PUBLIC_KEY_HEX
        license_validator._PUBLIC_KEY_HEX = (
            cls._ephemeral_key.public_key().public_bytes_raw().hex()
        )
        cls._orig_loader = generate_key._load_private_key
        generate_key._load_private_key = lambda: cls._ephemeral_key

    @classmethod
    def tearDownClass(cls):
        license_validator._PUBLIC_KEY_HEX = cls._orig_public_hex
        generate_key._load_private_key = cls._orig_loader

    def test_valid_license_validation_passes(self):
        fingerprint = get_hardware_fingerprint()
        key = generate_license("TEST_INC", 10, "2099-01-01", fingerprint, ["all"])

        is_valid, msg = verify_license(key, 5)  # 5 cameras configured (under limit 10)
        self.assertTrue(is_valid)
        self.assertIn("verified for client 'TEST_INC'", msg)

    def test_camera_limit_exceeded_fails(self):
        fingerprint = get_hardware_fingerprint()
        key = generate_license("TEST_INC", 5, "2099-01-01", fingerprint, ["all"])

        is_valid, msg = verify_license(key, 6)
        self.assertFalse(is_valid)
        self.assertIn("Camera limit exceeded", msg)

    def test_expired_license_fails(self):
        fingerprint = get_hardware_fingerprint()
        key = generate_license("TEST_INC", 100, "2020-01-01", fingerprint, ["all"])

        is_valid, msg = verify_license(key, 5)
        self.assertFalse(is_valid)
        self.assertIn("License expired", msg)

    def test_hardware_mismatch_fails(self):
        key = generate_license("TEST_INC", 10, "2099-01-01", "WRONG_FINGERPRINT", ["all"])

        is_valid, msg = verify_license(key, 5)
        self.assertFalse(is_valid)
        self.assertIn("Hardware mismatch", msg)

    def test_tampered_signature_fails(self):
        fingerprint = get_hardware_fingerprint()
        key = generate_license("TEST_INC", 10, "2099-01-01", fingerprint, ["all"])

        tampered_key = key[:-4] + "ABCD"

        is_valid, msg = verify_license(tampered_key, 5)
        self.assertFalse(is_valid)
        self.assertIn("verification failed", msg)

    def test_tampered_payload_fails(self):
        # Editing the payload (e.g. bumping max_cameras) must break the signature.
        fingerprint = get_hardware_fingerprint()
        key = generate_license("TEST_INC", 10, "2099-01-01", fingerprint, ["all"])
        payload_b64, signature = key.split(".")
        tampered_key = payload_b64[:-2] + "AA" + "." + signature

        is_valid, msg = verify_license(tampered_key, 5)
        self.assertFalse(is_valid)

    def test_no_password_bypass_exists(self):
        # The old hardcoded demo passwords must NOT grant access anymore.
        for password in ("123456789", "SentinelDemo2026", "AdivaDemo2026"):
            is_valid, _ = verify_license(password, 5)
            self.assertFalse(is_valid, f"Password bypass still works for {password!r}")

    def test_missing_license_falls_back_to_evaluation(self):
        is_valid, msg = verify_license("", EVALUATION_MAX_CAMERAS)
        self.assertTrue(is_valid)
        self.assertIn("Evaluation mode", msg)

        is_valid_exceeded, _ = verify_license("", EVALUATION_MAX_CAMERAS + 1)
        self.assertFalse(is_valid_exceeded)

    def test_feature_tiers(self):
        fingerprint = get_hardware_fingerprint()
        key = generate_license(
            "TIERED_INC", 10, "2099-01-01", fingerprint,
            ["core_tracking", "zone_alerts"],
        )
        self.assertTrue(is_feature_enabled("zone_alerts", key))
        self.assertFalse(is_feature_enabled("fall_detection", key))

        all_key = generate_license("FULL_INC", 10, "2099-01-01", fingerprint, ["all"])
        info = get_license_info(all_key)
        self.assertEqual(sorted(info["features"]), sorted(ALL_FEATURES))


if __name__ == "__main__":
    unittest.main()
