"""
Regression tests proving apps.otp's OTP_OVERRIDE_CODE bypass cannot exist in
production. These import config.settings.production directly in a clean
subprocess (bypassing python-dotenv's real .env pull-in from this repo's
local dev environment) — the same top-level module code Django actually
executes when DJANGO_SETTINGS_MODULE=config.settings.production at real
process startup, so this proves boot-time rejection, not just a unit-level
assumption.

Uses random, ephemeral, non-secret placeholder values (secrets.token_hex)
purely to satisfy the OTHER production guards (SECRET_KEY/JWT_SECRET/
DJANGO_INTERNAL_TOKEN strength checks) so the process reaches the OTP
checks under test — none of these values are real credentials.

Run:
  python3 manage.py test apps.otp.test_otp_production_guard -v 2
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
        # Explicitly blank every other production guard this module checks,
        # so a value in this repo's real .env (loaded by python-dotenv
        # inside base.py) can't leak in and cause an unrelated failure.
        "ALLOW_ALL_HOSTS": "",
        "OTP_DEBUG_LOG_CODES": "",
        "VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED": "",
        "VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED": "",
        "OTP_OVERRIDE_ENABLED": "",
        "OTP_OVERRIDE_CODE": "",
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


class OtpProductionGuardTests(unittest.TestCase):
    def test_boots_cleanly_with_no_override_configured(self):
        result = _run_production_import({})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("IMPORT_OK", result.stdout)

    def test_refuses_to_boot_when_override_enabled_flag_is_set(self):
        result = _run_production_import({"OTP_OVERRIDE_ENABLED": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OTP_OVERRIDE_ENABLED must not be enabled in production", result.stderr)

    def test_refuses_to_boot_when_override_code_is_set(self):
        result = _run_production_import({"OTP_OVERRIDE_CODE": "676139"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OTP_OVERRIDE_CODE must not be set in production", result.stderr)

    def test_refuses_to_boot_when_both_are_set(self):
        result = _run_production_import({"OTP_OVERRIDE_ENABLED": "true", "OTP_OVERRIDE_CODE": "676139"})
        self.assertNotEqual(result.returncode, 0)
        # Whichever check runs first is fine — either message proves rejection.
        self.assertTrue(
            "OTP_OVERRIDE_ENABLED must not be enabled in production" in result.stderr
            or "OTP_OVERRIDE_CODE must not be set in production" in result.stderr
        )
