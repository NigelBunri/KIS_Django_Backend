from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.module_loading import import_string


def _setting_text(name: str) -> str:
    return str(getattr(settings, name, "") or "").strip()


class Command(BaseCommand):
    help = "Verify email provider launch guardrails without sending a live email."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        parser.add_argument("--strict", action="store_true", help="Exit non-zero when launch blockers are found.")

    def handle(self, *args, **options):
        checks: list[dict[str, str]] = []

        resend_key_present = bool(_setting_text("RESEND_API_KEY"))
        backend_path = _setting_text("EMAIL_BACKEND")

        checks.append({
            "name": "RESEND_API_KEY",
            "state": "pass" if resend_key_present else "warn",
            "detail": "presence checked only; value is never printed" if resend_key_present else "not set — Resend is the only email path; sends will fail gracefully (logged, no crash) until this is configured",
        })

        try:
            backend_cls = import_string(backend_path) if backend_path else None
            checks.append({
                "name": "EMAIL_BACKEND",
                "state": "pass" if backend_cls else "fail",
                "detail": backend_path or "not configured",
            })
        except Exception as exc:
            checks.append({
                "name": "EMAIL_BACKEND",
                "state": "fail",
                "detail": f"{backend_path} failed to import: {exc.__class__.__name__}",
            })

        # Only a launch blocker once RESEND_API_KEY is actually set — local/
        # test environments intentionally run the console backend regardless
        # (see config/settings/local.py), which isn't a problem there.
        if resend_key_present:
            checks.append({
                "name": "resend_backend_selected",
                "state": "pass" if backend_path.endswith("ResendEmailBackend") else "fail",
                "detail": "consistent" if backend_path.endswith("ResendEmailBackend") else f"EMAIL_BACKEND is '{backend_path}', expected the Resend backend",
            })

        from_email = _setting_text("DEFAULT_FROM_EMAIL")
        checks.append({
            "name": "DEFAULT_FROM_EMAIL",
            "state": "pass" if from_email and "no-reply@example.com" not in from_email else "fail",
            "detail": "configured" if from_email and "no-reply@example.com" not in from_email else "still the placeholder default — must be a real sending domain",
        })

        failures = [c for c in checks if c["state"] == "fail"]
        warnings = [c for c in checks if c["state"] == "warn"]
        result = {
            "ready": not failures,
            "summary": {"failures": len(failures), "warnings": len(warnings), "checks": len(checks)},
            "checks": checks,
            "notes": [
                "This command does not send a live email.",
                "No secret values are printed — only presence/absence is checked.",
            ],
        }

        if options["json"]:
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
        else:
            self.stdout.write(f"Email launch guardrails ready: {result['ready']}")
            for check in checks:
                self.stdout.write(f"- {check['state'].upper()}: {check['name']} - {check['detail']}")
            for note in result["notes"]:
                self.stdout.write(f"Note: {note}")

        if failures and options["strict"]:
            raise CommandError(f"Email launch guardrails failed: {len(failures)} blocker(s).")
