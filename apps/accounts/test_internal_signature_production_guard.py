"""
Regression tests proving Phase 7's new production.py guards actually
reject unsafe boot-time configuration — mirrors the exact subprocess-based
proof-of-real-rejection pattern established in
apps.otp.test_otp_production_guard (imports config.settings.production
directly in a clean subprocess, the same top-level module code Django
executes at real process startup).

Run:
  python3 manage.py test apps.accounts.test_internal_signature_production_guard -v 2
"""
import os
import secrets
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_production_import(overrides: dict[str, str]) -> subprocess.CompletedProcess:
    base_env = {
        "PATH": os.environ.get("PATH", ""),
        "ALLOWED_HOSTS": "example.com",
        "SECRET_KEY": secrets.token_hex(32),
        "JWT_SECRET": secrets.token_hex(32),
        "DJANGO_INTERNAL_TOKEN": secrets.token_hex(32),
        "DATABASE_URL": "postgresql://test:test@localhost:5432/test_db",
        "REDIS_URL": "redis://localhost:6379/0",
        "OBJECT_STORAGE_PROVIDER": "s3",
        "ALLOW_ALL_HOSTS": "",
        "OTP_DEBUG_LOG_CODES": "",
        "VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED": "",
        "VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED": "",
        "OTP_OVERRIDE_ENABLED": "",
        "OTP_OVERRIDE_CODE": "",
        "INTERNAL_SIGNATURE_REQUIRED": "",
        "NEST_INTERNAL_TOKEN": "",
    }
    base_env.update(overrides)
    return subprocess.run(
        [sys.executable, "-c", "import config.settings.production; print('IMPORT_OK')"],
        cwd=str(REPO_ROOT),
        env=base_env,
        capture_output=True,
        text=True,
        timeout=30,
    )


class InternalSignatureProductionGuardTests(unittest.TestCase):
    def test_boots_cleanly_with_no_override_configured(self):
        result = _run_production_import({})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("IMPORT_OK", result.stdout)

    def test_boots_cleanly_with_a_strong_separate_nest_internal_token(self):
        result = _run_production_import({"NEST_INTERNAL_TOKEN": secrets.token_hex(32)})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_refuses_to_boot_when_signature_requirement_is_explicitly_disabled(self):
        result = _run_production_import({"INTERNAL_SIGNATURE_REQUIRED": "false"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("INTERNAL_SIGNATURE_REQUIRED must not be disabled in production", result.stderr)

    def test_refuses_to_boot_when_signature_requirement_is_explicitly_zero(self):
        result = _run_production_import({"INTERNAL_SIGNATURE_REQUIRED": "0"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("INTERNAL_SIGNATURE_REQUIRED must not be disabled in production", result.stderr)

    def test_refuses_to_boot_when_a_separate_nest_internal_token_is_weak(self):
        result = _run_production_import({"NEST_INTERNAL_TOKEN": "short"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("NEST_INTERNAL_TOKEN must be set to a strong value", result.stderr)
