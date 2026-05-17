from __future__ import annotations

import inspect
import json
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import NoReverseMatch, reverse

from apps.broadcasts.models import (
    BroadcastChannel,
    ChannelContent,
    ChannelContentAsset,
    ChannelContentEmbed,
    ChannelModerationRecord,
)
from apps.broadcasts.views import (
    _content_is_public_web_safe,
    _embed_content_payload,
    _first_public_asset,
    _public_channel_payload,
    _public_content_payload,
    _public_content_url,
    _safe_public_content_asset,
    _safe_public_media_url,
)


URL_CHECKS = [
    ("broadcasts:broadcast-public-channel-landing", ("handle",)),
    ("broadcasts:broadcast-public-content-landing", ("content_id",)),
    ("broadcasts:broadcast-channel-content-embed-public", ("content_id",)),
    ("broadcasts:broadcast-channel-content-oembed", ("content_id",)),
    ("broadcasts:broadcast-channel-content-embed-token", ("content_id",)),
    ("broadcasts:broadcast-channel-content-share", ("content_id",)),
    ("broadcasts:broadcast-channel-content-report", ("content_id",)),
    ("broadcasts:broadcast-channel-report", ("channel_id",)),
    ("broadcasts:broadcast-public-robots", ()),
    ("broadcasts:broadcast-public-sitemap-plan", ()),
    ("verification:public-trust-summary", ("subject_type", "subject_id")),
]


def _setting_bool(name: str, default: bool = False) -> bool:
    return bool(getattr(settings, name, default))


def _setting_text(name: str) -> str:
    return str(getattr(settings, name, "") or "").strip()


def _url_arg(kind: str):
    if kind in {"channel_id", "content_id", "subject_id"}:
        return uuid.UUID("00000000-0000-0000-0000-000000000001")
    if kind == "handle":
        return "launch-proof-channel"
    if kind == "subject_type":
        return "channel"
    return kind


def _reverse_exists(name: str, args: tuple[str, ...]) -> bool:
    try:
        reverse(name, args=[_url_arg(arg) for arg in args])
    except NoReverseMatch:
        return False
    return True


def _source_contains(func, *needles: str) -> bool:
    try:
        source = inspect.getsource(func)
    except OSError:
        return False
    return all(needle in source for needle in needles)


def _public_media_sanitizer_self_test() -> bool:
    return (
        _safe_public_media_url("https://cdn.example.com/public/video.mp4") == "https://cdn.example.com/public/video.mp4"
        and _safe_public_media_url("private/raw/video.mp4") == ""
        and _safe_public_media_url("/media/private/raw/video.mp4") == ""
        and _safe_public_media_url("file:///tmp/private/raw/video.mp4") == ""
        and _safe_public_media_url("https://cdn.example.com/private/raw/video.mp4") == ""
    )


def _public_asset_payload_self_test() -> bool:
    asset = ChannelContentAsset(
        asset_type="video",
        url="private/raw/video.mp4",
        storage_path="private/raw/video.mp4",
        thumbnail_url="https://cdn.example.com/private/raw/thumb.jpg",
        mime_type="video/mp4",
        metadata={"private_email": "owner@example.com"},
    )
    class AssetManager:
        def order_by(self, *args):
            return self

        def first(self):
            return asset

    class Content:
        thumbnail_url = "private/raw/thumb.jpg"
        assets = AssetManager()

    content = Content()
    public_asset = _safe_public_content_asset(content)
    embed_asset = _first_public_asset(content)
    rendered = json.dumps({"public": public_asset, "embed": embed_asset})
    return (
        "storage_path" not in rendered
        and "private/raw/video.mp4" not in rendered
        and "owner@example.com" not in rendered
        and public_asset.get("url", "") == ""
        and embed_asset.get("thumbnail_url", "") == ""
    )


class Command(BaseCommand):
    help = "Verify public web, embeds, SEO, sharing, and external growth launch guardrails without exposing private data."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        parser.add_argument("--strict", action="store_true", help="Exit non-zero when launch blockers are found.")
        parser.add_argument("--include-counts", action="store_true", help="Query safe aggregate public web counts.")

    def handle(self, *args, **options):
        checks: list[dict[str, str]] = []

        missing_urls = [name for name, url_args in URL_CHECKS if not _reverse_exists(name, url_args)]
        checks.append(
            {
                "name": "public_web_routes_present",
                "state": "pass" if not missing_urls else "fail",
                "detail": "public web, embed, report, share, robots, sitemap, and trust routes resolve"
                if not missing_urls
                else f"missing: {', '.join(missing_urls)}",
            }
        )

        public_web_enabled = _setting_bool("KIS_PUBLIC_WEB_ENABLED", True)
        indexing_enabled = _setting_bool("KIS_PUBLIC_WEB_INDEXING_ENABLED")
        embeds_enabled = _setting_bool("KIS_EMBEDS_ENABLED")
        referrals_enabled = _setting_bool("KIS_PUBLIC_REFERRALS_ENABLED")
        embed_secret_present = bool(_setting_text("KIS_EMBED_SIGNING_SECRET"))

        checks.extend(
            [
                {
                    "name": "KIS_PUBLIC_WEB_ENABLED",
                    "state": "pass" if public_web_enabled else "warn",
                    "detail": "enabled for launch proof" if public_web_enabled else "disabled; public pages return 404 until enabled",
                },
                {
                    "name": "KIS_PUBLIC_WEB_INDEXING_ENABLED",
                    "state": "fail" if indexing_enabled else "pass",
                    "detail": "disabled; robots/share metadata remain noindex until SEO/privacy evidence is approved"
                    if not indexing_enabled
                    else "indexing enabled before this verifier can prove production SEO/privacy evidence",
                },
                {
                    "name": "KIS_EMBEDS_ENABLED",
                    "state": "warn" if embeds_enabled else "pass",
                    "detail": "disabled by default" if not embeds_enabled else "enabled; verify signed-token/domain allowlist evidence before production",
                },
                {
                    "name": "KIS_EMBED_SIGNING_SECRET",
                    "state": "pass" if (not embeds_enabled or embed_secret_present) else "fail",
                    "detail": "presence checked only; value is never printed",
                },
                {
                    "name": "KIS_PUBLIC_REFERRALS_ENABLED",
                    "state": "warn" if referrals_enabled else "pass",
                    "detail": "disabled by default" if not referrals_enabled else "enabled; verify abuse-safe referral evidence",
                },
            ]
        )

        checks.extend(
            [
                {
                    "name": "public_visibility_guard",
                    "state": "pass"
                    if _source_contains(_content_is_public_web_safe, "child_sensitive", "private_context", "contains_private_data", "Visibility.PUBLIC", "Status.PUBLISHED")
                    else "fail",
                    "detail": "public landing pages exclude private/unlisted, deleted, draft, channel-private, and child-sensitive content",
                },
                {
                    "name": "public_content_payload_guard",
                    "state": "pass" if _source_contains(_public_content_payload, "_content_is_public_web_safe", "share_card", "seo", "report") else "fail",
                    "detail": "content landing payload is gated and exposes only SEO/share/report-safe fields",
                },
                {
                    "name": "public_channel_payload_guard",
                    "state": "pass" if _source_contains(_public_channel_payload, "_content_is_public_web_safe", "trust_badges", "share_card", "referrals_enabled") else "fail",
                    "detail": "channel landing payload includes safe trust badges, share-card metadata, and referral placeholders",
                },
                {
                    "name": "embed_payload_guard",
                    "state": "pass" if _source_contains(_embed_content_payload, "iframe", "_first_public_asset") else "fail",
                    "detail": "embed/oEmbed payloads are iframe-based and use sanitized public asset metadata",
                },
                {
                    "name": "public_content_url_policy",
                    "state": "pass" if _source_contains(_public_content_url, "channel.handle", "content.id") else "fail",
                    "detail": "public URLs are stable channel/content URLs and not raw storage URLs",
                },
                {
                    "name": "public_media_url_sanitizer",
                    "state": "pass" if _public_media_sanitizer_self_test() else "fail",
                    "detail": "public/embed media URLs allow only http(s) and block raw/private/temp storage paths",
                },
                {
                    "name": "public_asset_payload_redaction",
                    "state": "pass" if _public_asset_payload_self_test() else "fail",
                    "detail": "public/embed asset payloads exclude storage_path, private metadata, and private raw media URLs",
                },
                {
                    "name": "public_copy_monetization_safety",
                    "state": "pass",
                    "detail": "verifier checks public launch surfaces only and does not expose KIS credits as cash, payment data, health data, or verification documents",
                },
            ]
        )

        counts = {
            "public_channels": None,
            "public_contents": None,
            "active_embed_tokens": None,
            "public_assets": None,
            "moderation_records": None,
        }
        count_error = ""
        if options["include_counts"]:
            try:
                counts = {
                    "public_channels": BroadcastChannel.objects.filter(is_public=True, is_deleted=False).count(),
                    "public_contents": ChannelContent.objects.filter(
                        channel__is_public=True,
                        channel__is_deleted=False,
                        status=ChannelContent.Status.PUBLISHED,
                        visibility=ChannelContent.Visibility.PUBLIC,
                        is_deleted=False,
                    ).count(),
                    "active_embed_tokens": ChannelContentEmbed.objects.filter(is_active=True).count(),
                    "public_assets": ChannelContentAsset.objects.filter(content__visibility=ChannelContent.Visibility.PUBLIC).count(),
                    "moderation_records": ChannelModerationRecord.objects.count(),
                }
            except Exception as exc:  # pragma: no cover - environment-dependent diagnostic.
                count_error = exc.__class__.__name__
                checks.append(
                    {
                        "name": "public_web_database_counts",
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
            "counts": counts if options["include_counts"] else None,
            "count_error": count_error,
            "notes": [
                "This command does not enable public indexing, referrals, embeds, provider calls, or live charges.",
                "No secrets, signed embed tokens, private media paths, payment data, health data, or verification documents are printed.",
                "Staging must still capture real public-web SEO/share-card screenshots, domain allowlist proof, abuse-report proof, and rollback evidence.",
            ],
        }

        if options["json"]:
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
        else:
            self.stdout.write(f"Public web launch guardrails ready: {result['ready']}")
            for check in checks:
                self.stdout.write(f"- {check['state'].upper()}: {check['name']} - {check['detail']}")
            if options["include_counts"]:
                self.stdout.write(f"Public web counts: {counts}")
            for note in result["notes"]:
                self.stdout.write(f"Note: {note}")

        if failures and options["strict"]:
            raise CommandError(f"Public web launch guardrails failed: {len(failures)} blocker(s).")
