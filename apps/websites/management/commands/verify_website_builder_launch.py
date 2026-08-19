from __future__ import annotations

import inspect
import json
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand
from django.urls import NoReverseMatch, reverse

from apps.accounts.tier_presets import TIER_PRESETS
from apps.websites import kis_content_resolvers, views
from apps.websites.models import Website, WebsitePage

URL_CHECKS = [
    ("websites:public-site", ("website-slug",)),
    ("websites:public-page", ("website-slug", "page-slug")),
    ("websites:public-sitemap-plan", ()),
    ("websites:mine", ()),
    ("websites:detail", ("website_id",)),
    ("websites:publish", ("website_id",)),
    ("websites:unpublish", ("website_id",)),
    ("websites:preview-token", ("website_id",)),
    ("websites:page-list-create", ("website_id",)),
    ("websites:page-detail", ("website_id", "page_id")),
    ("websites:page-publish", ("website_id", "page_id")),
    ("websites:page-unpublish", ("website_id", "page_id")),
    ("websites:kis-content-search", ("target_type",)),
]

REQUIRED_TIER_KEYS = (
    "websites_limit",
    "website_pages_limit",
    "website_kis_content_sections_limit",
    "website_custom_branding",
    "website_publish",
)


def _url_arg(kind: str):
    if kind in {"website_id", "page_id"}:
        return uuid.UUID("00000000-0000-0000-0000-000000000001")
    if kind == "website-slug":
        return "launch-proof-site"
    if kind == "page-slug":
        return "about"
    if kind == "target_type":
        return "course"
    return kind


def _reverse_exists(name: str, args: tuple[str, ...]) -> bool:
    try:
        reverse(name, args=[_url_arg(arg) for arg in args])
    except NoReverseMatch:
        return False
    return True


def _setting_bool(name: str) -> bool:
    value = getattr(settings, name, "")
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _public_gate_calls_present() -> bool:
    source = inspect.getsource(views)
    return "public_web_enabled()" in source and "verify_website_preview_token" in source


def _tier_presets_have_website_keys() -> bool:
    for preset in TIER_PRESETS:
        features = preset.get("features_json", {})
        if not all(key in features for key in REQUIRED_TIER_KEYS):
            return False
    return True


def _resolvers_are_callable() -> bool:
    try:
        for resolver in kis_content_resolvers.RESOLVERS.values():
            resolver(owner_type="shop", owner_id=uuid.uuid4(), target_ids=[], limit=1)
    except Exception:
        return False
    return True


class Command(BaseCommand):
    help = "Verify non-secret Website Builder launch guardrails without touching production data."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        parser.add_argument("--include-counts", action="store_true", help="Query website/page launch counts.")

    def handle(self, *args, **options):
        checks: list[dict[str, str]] = []

        missing_urls = [name for name, url_args in URL_CHECKS if not _reverse_exists(name, url_args)]
        checks.append({
            "name": "website_builder_urls_present",
            "state": "pass" if not missing_urls else "fail",
            "detail": "public/owner-CRUD/kis-content-search routes resolve" if not missing_urls else f"missing: {', '.join(missing_urls)}",
        })

        checks.append({
            "name": "KIS_WEBSITE_PUBLIC_BASE_URL",
            "state": "pass" if getattr(settings, "KIS_WEBSITE_PUBLIC_BASE_URL", "") else "fail",
            "detail": "configured",
        })

        indexing_enabled = _setting_bool("KIS_PUBLIC_WEB_INDEXING_ENABLED")
        checks.append({
            "name": "KIS_PUBLIC_WEB_INDEXING_ENABLED",
            "state": "fail" if indexing_enabled else "pass",
            "detail": "disabled; website builder public pages stay noindex until launch evidence is approved"
            if not indexing_enabled else "public indexing enabled without this command verifying production SEO/privacy evidence",
        })

        checks.append({
            "name": "public_web_gate_guard",
            "state": "pass" if _public_gate_calls_present() else "fail",
            "detail": "public site/page views still call the shared public-web enabled/preview-token gates",
        })

        checks.append({
            "name": "tier_presets_website_keys",
            "state": "pass" if _tier_presets_have_website_keys() else "fail",
            "detail": f"all 6 tiers define {', '.join(REQUIRED_TIER_KEYS)}",
        })

        checks.append({
            "name": "kis_content_resolvers_importable",
            "state": "pass" if _resolvers_are_callable() else "fail",
            "detail": "every kis_content target_type resolver is callable against an empty queryset",
        })

        counts = {"websites": None, "pages": None, "published_websites": None}
        count_error = ""
        if options["include_counts"]:
            try:
                counts = {
                    "websites": Website.objects.count(),
                    "pages": WebsitePage.objects.count(),
                    "published_websites": Website.objects.filter(status="published").count(),
                }
            except Exception as exc:  # pragma: no cover - environment-dependent diagnostic.
                count_error = exc.__class__.__name__
                checks.append({
                    "name": "website_builder_database_counts",
                    "state": "warn",
                    "detail": f"database summary unavailable: {count_error}",
                })

        failures = [c for c in checks if c["state"] == "fail"]
        warnings = [c for c in checks if c["state"] == "warn"]
        result = {
            "ready": not failures,
            "summary": {"checks": len(checks), "failures": len(failures), "warnings": len(warnings)},
            "checks": checks,
            "counts": counts,
            "count_error": count_error,
            "notes": [
                "This command does not enable public indexing or mutate any Website/WebsitePage rows.",
                "No secrets, preview tokens, or private legacy-landing data are printed.",
            ],
        }

        if options["json"]:
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
        else:
            self.stdout.write(f"Website Builder launch guardrails ready: {result['ready']}")
            for check in checks:
                self.stdout.write(f"- {check['state'].upper()}: {check['name']} - {check['detail']}")
            if options["include_counts"]:
                self.stdout.write(f"Website/page counts: {counts}")
            for note in result["notes"]:
                self.stdout.write(f"Note: {note}")
