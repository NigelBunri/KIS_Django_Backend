from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import DatabaseError, OperationalError

from apps.media.models import MediaSafetyScan
from apps.media.safety import (
    configured_allowed_extensions,
    configured_allowed_mime_prefixes,
    configured_allowed_mime_types,
    configured_blocked_extensions,
    explicit_scan_required,
    live_provider_calls_enabled,
    media_safety_enabled,
)


class Command(BaseCommand):
    help = "Read-only media safety launch verifier. Does not print secrets or raw storage paths."

    def add_arguments(self, parser):
        parser.add_argument("--strict", action="store_true", help="Exit non-zero when launch-critical checks fail.")

    def handle(self, *args, **options):
        strict = bool(options.get("strict"))
        failures: list[str] = []
        warnings: list[str] = []

        def check(condition: bool, ok: str, fail: str, *, warning: bool = False):
            if condition:
                self.stdout.write(self.style.SUCCESS(f"PASS {ok}"))
                return
            if warning:
                warnings.append(fail)
                self.stdout.write(self.style.WARNING(f"WARN {fail}"))
            else:
                failures.append(fail)
                self.stdout.write(self.style.ERROR(f"FAIL {fail}"))

        debug = bool(getattr(settings, "DEBUG", False))
        check(media_safety_enabled(), "MEDIA_SAFETY_ENABLED is on.", "MEDIA_SAFETY_ENABLED must be true for launch.")
        check(
            explicit_scan_required() or debug,
            "Explicit-content scan/review is required, or this is a local DEBUG run.",
            "MEDIA_EXPLICIT_SCAN_REQUIRED must be true outside local development.",
        )
        check(
            not live_provider_calls_enabled(),
            "Live explicit-content provider calls are disabled by default.",
            "MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED is enabled; provider evidence is required before launch.",
            warning=True,
        )
        check(
            "application/octet-stream" not in configured_allowed_mime_types(),
            "Generic application/octet-stream uploads are not allowed by default.",
            "application/octet-stream should not be allowed for launch uploads.",
        )
        check(
            bool(configured_allowed_mime_types() or configured_allowed_mime_prefixes()),
            "Allowed MIME policy is configured.",
            "Allowed MIME policy is empty.",
        )
        check(
            bool(configured_allowed_extensions()),
            "Allowed extension policy is configured.",
            "Allowed extension policy is empty.",
        )
        check(
            {".exe", ".sh", ".js", ".html", ".svg"}.issubset(configured_blocked_extensions()),
            "High-risk executable/script extensions are blocked.",
            "High-risk executable/script extensions must be blocked.",
        )

        try:
            total_scans = MediaSafetyScan.objects.filter(is_deleted=False).count()
            pending = MediaSafetyScan.objects.filter(is_deleted=False, status="pending_review").count()
            blocked = MediaSafetyScan.objects.filter(is_deleted=False, status="blocked").count()
            failed = MediaSafetyScan.objects.filter(is_deleted=False, status="failed").count()
            quarantined = MediaSafetyScan.objects.filter(is_deleted=False, quarantine=True).count()
            check(
                True,
                f"Current media safety queue summary: total={total_scans}, pending_review={pending}, blocked={blocked}, failed={failed}, quarantined={quarantined}.",
                "",
            )
        except (DatabaseError, OperationalError) as exc:
            warnings.append("Media safety scan queue summary could not be read from the configured database.")
            self.stdout.write(self.style.WARNING(f"WARN Media safety scan queue summary unavailable: {exc.__class__.__name__}"))

        self.stdout.write("")
        self.stdout.write(f"Result: {len(failures)} fail, {len(warnings)} warning.")
        if failures and strict:
            raise SystemExit(1)
