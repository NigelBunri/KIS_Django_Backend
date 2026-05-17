from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import NoReverseMatch, reverse

from apps.bible.models import (
    BibleBookmark,
    BibleContentAuditLog,
    BibleCourse,
    BibleDailyPassage,
    BibleHighlight,
    BibleLesson,
    BibleMeditationPost,
    BibleNote,
    BiblePrayerMonth,
    BiblePublishStatus,
    BibleReadingPlanEvent,
    BibleTranslation,
    BibleTranslationLicenseReviewStatus,
    BibleTranslationMetadata,
    BibleTranslationValidationStatus,
    ReadingPlan,
)
from apps.bible.serializers import _public_attachment_list
from apps.media.safety import (
    attachment_requires_safety_review,
    live_provider_calls_enabled as media_live_provider_calls_enabled,
    media_safety_enabled,
)
from apps.partners.seed import LEGACY_DEFAULT_PARTNER_SLUGS


STATIC_URL_NAMES = [
    ("bible:bible-translations", ()),
    ("bible:bible-books", ()),
    ("bible:bible-chapters", ()),
    ("bible:bible-reader", ()),
    ("bible:bible-reader-parallel", ()),
    ("bible:bible-search", ()),
    ("bible:bible-daily", ()),
    ("bible:bible-stats", ()),
    ("bible:bible-spiritual-growth-summary", ()),
    ("bible:bible-course-react", (1,)),
    ("bible:bible-course-share", (1,)),
    ("bible:bible-lesson-react", (1,)),
    ("bible:bible-credential-share", ("launch-proof-token",)),
]

ROUTER_URL_NAMES = [
    "bible:bible-translation-registry-list",
    "bible:bible-daily-passages-list",
    "bible:bible-meditation-posts-list",
    "bible:bible-prayer-months-list",
    "bible:bible-prayer-days-list",
    "bible:bible-content-audit-list",
    "bible:bible-plans-list",
    "bible:bible-plan-enrollments-list",
    "bible:bible-history-list",
    "bible:bible-reading-events-list",
    "bible:bible-reading-events-from-selection",
    "bible:bible-bookmarks-list",
    "bible:bible-notes-list",
    "bible:bible-highlights-list",
    "bible:bible-highlights-colors",
    "bible:bible-memory-list",
    "bible:bible-preferences-list",
    "bible:bible-preferences-current",
    "bible:bible-courses-list",
    "bible:bible-lessons-list",
    "bible:bible-course-enrollments-list",
    "bible:bible-lesson-progress-list",
    "bible:bible-course-comments-list",
    "bible:bible-lesson-comments-list",
    "bible:bible-live-sessions-list",
    "bible:bible-live-recordings-list",
    "bible:bible-credentials-list",
]


def _setting_bool(name: str, default: bool = False) -> bool:
    return bool(getattr(settings, name, default))


def _reverse_exists(name: str, args: tuple = ()) -> bool:
    try:
        reverse(name, args=args)
    except NoReverseMatch:
        return False
    return True


def _lesson_attachment_redaction_self_test() -> bool:
    rendered = json.dumps(
        _public_attachment_list(
            [
                {
                    "url": "https://cdn.example.com/safe.pdf",
                    "storage_path": "private/bible/raw/safe.pdf",
                    "token": "secret-token",
                    "metadata": {"path": "/private/raw/file.pdf", "safe": "ok"},
                }
            ]
        )
    )
    return (
        "storage_path" not in rendered
        and "private/bible/raw" not in rendered
        and "secret-token" not in rendered
        and "/private/raw" not in rendered
        and "https://cdn.example.com/safe.pdf" in rendered
        and "safe" in rendered
    )


def _translation_publication_self_test() -> bool:
    metadata = BibleTranslationMetadata(
        code="LAUNCH_PROOF",
        language="en",
        full_name="Launch Proof Translation",
        source_path="en/launch-proof.json",
        source_filename="launch-proof.json",
        is_public=True,
        is_licensed=True,
        copyright_status="public_domain",
        license_review_status=BibleTranslationLicenseReviewStatus.NOT_REQUIRED,
        validation_status=BibleTranslationValidationStatus.VALID,
    )
    return metadata.can_be_public


class Command(BaseCommand):
    help = "Verify non-secret Bible/KCAN launch guardrails without external provider calls."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        parser.add_argument("--strict", action="store_true", help="Exit non-zero when launch blockers are found.")
        parser.add_argument("--include-counts", action="store_true", help="Query Bible/KCAN content counts.")

    def handle(self, *args, **options):
        checks: list[dict[str, str]] = []

        missing_static = [name for name, args in STATIC_URL_NAMES if not _reverse_exists(name, args)]
        missing_router = [name for name in ROUTER_URL_NAMES if not _reverse_exists(name)]
        missing_urls = [*missing_static, *missing_router]
        checks.append(
            {
                "name": "bible_urls_present",
                "state": "pass" if not missing_urls else "fail",
                "detail": "reader, plans, notes, highlights, courses, KCAN content, and credentials URLs resolve"
                if not missing_urls
                else f"missing: {', '.join(missing_urls)}",
            }
        )

        checks.extend(
            [
                {
                    "name": "licensed_translation_publication_rule",
                    "state": "pass" if _translation_publication_self_test() else "fail",
                    "detail": "public Bible translations require public/licensed/valid metadata",
                },
                {
                    "name": "bible_attachment_public_serialization",
                    "state": "pass" if _lesson_attachment_redaction_self_test() else "fail",
                    "detail": "Bible lesson/submission attachment serializers remove private paths and tokens",
                },
                {
                    "name": "MEDIA_SAFETY_ENABLED",
                    "state": "pass" if media_safety_enabled() else "fail",
                    "detail": "enabled" if media_safety_enabled() else "must be enabled for devotional/course media launch",
                },
                {
                    "name": "MEDIA_SAFETY_LIVE_PROVIDER_CALLS_ENABLED",
                    "state": "fail" if media_live_provider_calls_enabled() else "pass",
                    "detail": "disabled by default" if not media_live_provider_calls_enabled() else "live media provider calls enabled without launch proof",
                },
                {
                    "name": "KIS_AI_LIVE_PROVIDER_CALLS_ENABLED",
                    "state": "fail" if _setting_bool("KIS_AI_LIVE_PROVIDER_CALLS_ENABLED") else "pass",
                    "detail": "disabled by default" if not _setting_bool("KIS_AI_LIVE_PROVIDER_CALLS_ENABLED") else "live AI calls enabled without Bible pastoral/safety evidence",
                },
                {
                    "name": "bible_media_safety_gate",
                    "state": "pass" if attachment_requires_safety_review({"scan_status": "pending_review"}) else "fail",
                    "detail": "pending-review devotional/course attachments are recognized before visibility",
                },
                {
                    "name": "KIS_PUBLIC_WEB_INDEXING_ENABLED",
                    "state": "fail" if _setting_bool("KIS_PUBLIC_WEB_INDEXING_ENABLED") else "pass",
                    "detail": "disabled; Bible/KCAN public indexing remains gated until launch evidence is approved"
                    if not _setting_bool("KIS_PUBLIC_WEB_INDEXING_ENABLED")
                    else "public indexing is enabled without this command verifying production SEO/privacy evidence",
                },
            ]
        )

        counts = {
            "public_translations": None,
            "reading_plans": None,
            "reading_events": None,
            "bookmarks": None,
            "highlights": None,
            "notes": None,
            "published_daily_passages": None,
            "published_meditation_posts": None,
            "published_prayer_months": None,
            "published_bible_courses": None,
            "bible_lessons": None,
            "kcan_content_audit_events": None,
        }
        count_error = ""
        if options["include_counts"]:
            try:
                counts = {
                    "public_translations": BibleTranslation.objects.filter(
                        is_active=True,
                        metadata__is_public=True,
                        metadata__is_licensed=True,
                        metadata__validation_status__in=["valid", "warning"],
                    ).count(),
                    "reading_plans": ReadingPlan.objects.count(),
                    "reading_events": BibleReadingPlanEvent.objects.count(),
                    "bookmarks": BibleBookmark.objects.count(),
                    "highlights": BibleHighlight.objects.count(),
                    "notes": BibleNote.objects.count(),
                    "published_daily_passages": BibleDailyPassage.objects.filter(
                        partner__slug__in=LEGACY_DEFAULT_PARTNER_SLUGS,
                        status=BiblePublishStatus.PUBLISHED,
                    ).count(),
                    "published_meditation_posts": BibleMeditationPost.objects.filter(
                        partner__slug__in=LEGACY_DEFAULT_PARTNER_SLUGS,
                        status=BiblePublishStatus.PUBLISHED,
                    ).count(),
                    "published_prayer_months": BiblePrayerMonth.objects.filter(
                        partner__slug__in=LEGACY_DEFAULT_PARTNER_SLUGS,
                        status=BiblePublishStatus.PUBLISHED,
                    ).count(),
                    "published_bible_courses": BibleCourse.objects.filter(is_bible_course=True, is_public=True, published=True).count(),
                    "bible_lessons": BibleLesson.objects.count(),
                    "kcan_content_audit_events": BibleContentAuditLog.objects.filter(
                        partner__slug__in=LEGACY_DEFAULT_PARTNER_SLUGS
                    ).count(),
                }
                if counts["public_translations"] == 0:
                    checks.append(
                        {
                            "name": "public_bible_translations_available",
                            "state": "warn",
                            "detail": "no public/licensed translations found in the current database",
                        }
                    )
            except Exception as exc:  # pragma: no cover - environment-dependent diagnostic.
                count_error = exc.__class__.__name__
                checks.append(
                    {
                        "name": "bible_database_counts",
                        "state": "warn",
                        "detail": f"database summary unavailable: {count_error}",
                    }
                )

        failures = [check for check in checks if check["state"] == "fail"]
        warnings = [check for check in checks if check["state"] == "warn"]
        result = {
            "ready": not failures,
            "summary": {"checks": len(checks), "failures": len(failures), "warnings": len(warnings)},
            "checks": checks,
            "counts": counts,
            "count_error": count_error,
            "notes": [
                "This command does not make live AI, media-safety, public-indexing, or provider calls.",
                "No secrets, private media paths, raw storage paths, certificate tokens, or private devotional payloads are printed.",
                "Bible translation publication must stay limited to public/licensed/valid translations.",
                "KCAN/Bible public publishing and indexing should remain gated until staging evidence is attached.",
            ],
        }

        if options["json"]:
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
        else:
            self.stdout.write(f"Bible/KCAN launch guardrails ready: {result['ready']}")
            for check in checks:
                self.stdout.write(f"- {check['state'].upper()}: {check['name']} - {check['detail']}")
            if options["include_counts"]:
                self.stdout.write(f"Bible/KCAN counts: {counts}")
            for note in result["notes"]:
                self.stdout.write(f"Note: {note}")

        if failures and options["strict"]:
            raise CommandError(f"Bible/KCAN launch guardrails failed: {len(failures)} blocker(s).")
