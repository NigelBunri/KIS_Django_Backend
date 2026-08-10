from __future__ import annotations

import json
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.module_loading import import_string


def _setting_text(name: str) -> str:
    return str(getattr(settings, name, "") or "").strip()


def _env_text(name: str) -> str:
    # Deliberately reads the raw environment variable, not the resolved
    # Django setting — Django ships its own global default for EMAIL_HOST
    # ('localhost'), so settings.EMAIL_HOST is truthy even when nobody
    # configured anything, which would make a presence check falsely pass.
    return os.environ.get(name, "").strip()


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
            "detail": "presence checked only; value is never printed" if resend_key_present else "not set — falling back to SMTP (see EMAIL_HOST checks below)",
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

        if resend_key_present:
            checks.append({
                "name": "resend_backend_selected",
                "state": "pass" if backend_path.endswith("ResendEmailBackend") else "fail",
                "detail": "RESEND_API_KEY is set but EMAIL_BACKEND is not the Resend backend" if not backend_path.endswith("ResendEmailBackend") else "consistent",
            })
        elif backend_path == "django.core.mail.backends.smtp.EmailBackend":
            # SMTP is the active path only when Resend isn't configured AND
            # EMAIL_BACKEND actually resolves to the SMTP backend — e.g.
            # local dev uses the console backend regardless, where these
            # would be irrelevant noise. Checked against the raw env var,
            # not settings.EMAIL_HOST, since Django's own global default
            # ('localhost') would otherwise make this falsely pass.
            for name in ("EMAIL_HOST", "EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD"):
                present = bool(_env_text(name))
                checks.append({
                    "name": name,
                    "state": "pass" if present else "fail",
                    "detail": "configured" if present else "not set — SMTP sends will fail immediately",
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
