"""
Phase 9: observability improvements.
  - RequestLoggingMiddleware's per-request summary line was logged at
    DEBUG, which never reaches production (LOG_LEVEL defaults to INFO
    there) — promoted to INFO.
  - health_check() previously returned str(exc) directly to an
    unauthenticated public caller on a db/cache failure — now logs full
    detail server-side and returns a generic "unavailable" string.
  - health_check() gained celery_broker_configured/resend_configured
    booleans (presence-only, never a live probe, never part of the ok/503
    gate).
  - config.settings.production's Sentry init() now sets release from
    RENDER_GIT_COMMIT (a real, Render-provided env var) so an error can be
    tied to the deploy that shipped it.

Run:
  python3 manage.py test common.test_observability_phase9 --keepdb -v 2
"""
import json
import logging
import os
import secrets
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase, override_settings

REPO_ROOT = Path(__file__).resolve().parents[1]


@override_settings(SECURE_SSL_REDIRECT=False)
class RequestLoggingMiddlewareLevelTests(TestCase):
    def test_request_summary_line_is_logged_at_info_not_debug(self):
        with self.assertLogs("common.middleware", level="INFO") as captured:
            Client().get("/health/")
        self.assertTrue(any("REQ END" in line for line in captured.output))
        # Confirm it's genuinely INFO (assertLogs above would also catch a
        # WARNING/ERROR — check the specific record level directly).
        info_records = [r for r in captured.records if "REQ END" in r.getMessage()]
        self.assertTrue(info_records)
        self.assertEqual(info_records[0].levelname, "INFO")

    def test_request_id_is_echoed_back_in_response_header(self):
        res = Client().get("/health/", HTTP_X_REQUEST_ID="my-custom-rid")
        self.assertEqual(res["X-Request-Id"], "my-custom-rid")


@override_settings(SECURE_SSL_REDIRECT=False)
class HealthCheckHardeningTests(TestCase):
    def test_healthy_response_shape(self):
        res = Client().get("/health/")
        self.assertEqual(res.status_code, 200)
        payload = json.loads(res.content)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["checks"]["db"], "ok")
        self.assertEqual(payload["checks"]["cache"], "ok")
        self.assertIn("celery_broker_configured", payload["checks"])
        self.assertIn("resend_configured", payload["checks"])
        self.assertIsInstance(payload["checks"]["celery_broker_configured"], bool)

    def test_db_failure_never_leaks_the_raw_exception_text(self):
        secret_looking_error = "password authentication failed for user \"kis_prod_admin\""
        with patch.object(connection, "ensure_connection", side_effect=Exception(secret_looking_error)):
            with self.assertLogs("health_check", level="ERROR"):
                res = Client().get("/health/")
        self.assertEqual(res.status_code, 503)
        payload = json.loads(res.content)
        self.assertEqual(payload["checks"]["db"], "unavailable")
        self.assertNotIn("kis_prod_admin", res.content.decode())
        self.assertNotIn("password authentication failed", res.content.decode())

    def test_cache_failure_never_leaks_the_raw_exception_text(self):
        with patch.object(cache, "set", side_effect=Exception("redis://user:hunter2@internal-host:6379 unreachable")):
            with self.assertLogs("health_check", level="ERROR"):
                res = Client().get("/health/")
        self.assertEqual(res.status_code, 503)
        payload = json.loads(res.content)
        self.assertEqual(payload["checks"]["cache"], "unavailable")
        self.assertNotIn("hunter2", res.content.decode())
        self.assertNotIn("internal-host", res.content.decode())

    def test_db_failure_is_still_fully_logged_server_side(self):
        with self.assertLogs("health_check", level="ERROR") as captured:
            with patch.object(connection, "ensure_connection", side_effect=Exception("boom detail")):
                Client().get("/health/")
        self.assertTrue(any("boom detail" in line for line in captured.output))

    @override_settings(CELERY_BROKER_URL="", RESEND_API_KEY="")
    def test_missing_celery_and_resend_config_does_not_fail_the_health_check(self):
        # These are informational only — a missing broker/email provider
        # must not make the *web* service's own health check fail.
        res = Client().get("/health/")
        self.assertEqual(res.status_code, 200)
        payload = json.loads(res.content)
        self.assertFalse(payload["checks"]["celery_broker_configured"])
        self.assertFalse(payload["checks"]["resend_configured"])


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
        "SENTRY_DSN": "",
        "RENDER_GIT_COMMIT": "",
    }
    base_env.update(overrides)
    script = (
        "import config.settings.production\n"
        "import sentry_sdk\n"
        "client = sentry_sdk.get_client()\n"
        "print('RELEASE=' + str(client.options.get('release')))\n"
        "print('ENVIRONMENT=' + str(client.options.get('environment')))\n"
        "print('IMPORT_OK')\n"
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        env=base_env,
        capture_output=True,
        text=True,
        timeout=30,
    )


class SentryReleaseTrackingTests(unittest.TestCase):
    def test_release_is_set_from_render_git_commit_when_sentry_configured(self):
        result = _run_production_import({
            "SENTRY_DSN": "https://abc123@o12345.ingest.sentry.io/6789",
            "RENDER_GIT_COMMIT": "deadbeef1234",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("IMPORT_OK", result.stdout)
        self.assertIn("RELEASE=deadbeef1234", result.stdout)
        self.assertIn("ENVIRONMENT=production", result.stdout)

    def test_render_git_commit_takes_priority_over_sentrys_own_git_autodetection(self):
        # When RENDER_GIT_COMMIT is unset, sentry_sdk falls back to
        # auto-detecting the release from the local .git HEAD itself
        # (confirmed empirically — this is documented sentry_sdk behavior,
        # not something this code implements) — so an explicit
        # RENDER_GIT_COMMIT must win over that auto-detection, not just
        # over an empty string.
        result = _run_production_import({
            "SENTRY_DSN": "https://abc123@o12345.ingest.sentry.io/6789",
            "RENDER_GIT_COMMIT": "deadbeef1234",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RELEASE=deadbeef1234", result.stdout)

    def test_release_falls_back_to_sentrys_git_autodetection_when_render_git_commit_is_not_set(self):
        result = _run_production_import({
            "SENTRY_DSN": "https://abc123@o12345.ingest.sentry.io/6789",
            "RENDER_GIT_COMMIT": "",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        release_line = next(line for line in result.stdout.splitlines() if line.startswith("RELEASE="))
        release_value = release_line.split("=", 1)[1]
        # Not the deliberate "no release" case (that would be the literal
        # string "None") — this repo's own git HEAD gets picked up instead.
        self.assertNotEqual(release_value, "None")
        self.assertTrue(release_value)

    def test_boots_cleanly_with_sentry_disabled(self):
        result = _run_production_import({"SENTRY_DSN": ""})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("IMPORT_OK", result.stdout)
