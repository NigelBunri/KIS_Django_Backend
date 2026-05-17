from __future__ import annotations

import json
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import NoReverseMatch, reverse

from apps.broadcasts.media_pipeline import (
    configured_media_provider,
    configured_transcode_provider,
    live_provider_calls_enabled,
    validate_asset_ready_for_publish,
)
from apps.broadcasts.models import (
    BroadcastChannel,
    BroadcastChannelSubscription,
    BroadcastItem,
    BroadcastPlaylist,
    ChannelContent,
    ChannelContentAsset,
    ChannelContentComment,
    ChannelContentEmbed,
    ChannelModerationRecord,
    ChannelWatchHistory,
)
from apps.broadcasts.serializers import ChannelContentAssetSerializer


URL_CHECKS = [
    ("broadcasts:broadcast-feed", ()),
    ("broadcasts:broadcast-channel-list", ()),
    ("broadcasts:broadcast-channel-detail", ("launch-proof-channel",)),
    ("broadcasts:broadcast-channel-subscribe", ("channel_id",)),
    ("broadcasts:broadcast-channel-subscription", ("channel_id",)),
    ("broadcasts:broadcast-channel-report", ("channel_id",)),
    ("broadcasts:broadcast-channel-moderation", ("channel_id",)),
    ("broadcasts:broadcast-channel-analytics", ("channel_id",)),
    ("broadcasts:broadcast-channel-broadcast", ("channel_id",)),
    ("broadcasts:broadcast-channel-contents", ("channel_id",)),
    ("broadcasts:broadcast-channel-playlists", ("channel_id",)),
    ("broadcasts:broadcast-channel-live-streams", ("channel_id",)),
    ("broadcasts:broadcast-channel-content-detail", ("content_id",)),
    ("broadcasts:broadcast-channel-content-publish", ("content_id",)),
    ("broadcasts:broadcast-channel-content-unpublish", ("content_id",)),
    ("broadcasts:broadcast-channel-content-broadcast", ("content_id",)),
    ("broadcasts:broadcast-channel-content-assets", ("content_id",)),
    ("broadcasts:broadcast-channel-content-embed-token", ("content_id",)),
    ("broadcasts:broadcast-channel-content-react", ("content_id",)),
    ("broadcasts:broadcast-channel-content-save", ("content_id",)),
    ("broadcasts:broadcast-channel-content-share", ("content_id",)),
    ("broadcasts:broadcast-channel-content-view", ("content_id",)),
    ("broadcasts:broadcast-channel-content-report", ("content_id",)),
    ("broadcasts:broadcast-channel-content-comments", ("content_id",)),
    ("broadcasts:broadcast-channel-comment-moderate", ("comment_id",)),
    ("broadcasts:broadcast-channel-moderation-action", ("record_id",)),
    ("broadcasts:broadcast-channel-playlist-items", ("playlist_id",)),
    ("broadcasts:broadcast-channel-playlist-item-detail", ("playlist_id", "content_id")),
    ("broadcasts:broadcast-live-stream-detail", ("stream_id",)),
    ("broadcasts:broadcast-live-stream-start", ("stream_id",)),
    ("broadcasts:broadcast-live-stream-end", ("stream_id",)),
    ("broadcasts:broadcast-live-stream-webhook", ("provider",)),
    ("broadcasts:broadcast-channel-content-embed-public", ("content_id",)),
    ("broadcasts:broadcast-channel-content-oembed", ("content_id",)),
    ("broadcasts:broadcast-public-channel-landing", ("handle",)),
    ("broadcasts:broadcast-public-content-landing", ("content_id",)),
    ("broadcasts:broadcast-public-robots", ()),
    ("broadcasts:broadcast-public-sitemap-plan", ()),
]


def _setting_bool(name: str, default: bool = False) -> bool:
    return bool(getattr(settings, name, default))


def _setting_text(name: str) -> str:
    return str(getattr(settings, name, "") or "").strip()


def _url_arg(kind: str):
    if kind in {"channel_id", "content_id", "comment_id", "record_id", "playlist_id", "stream_id"}:
        return uuid.UUID("00000000-0000-0000-0000-000000000001")
    if kind == "provider":
        return "disabled"
    if kind == "handle":
        return "launch-proof-channel"
    return kind


def _reverse_exists(name: str, args: tuple[str, ...]) -> bool:
    try:
        reverse(name, args=[_url_arg(arg) for arg in args])
    except NoReverseMatch:
        return False
    return True


def _asset_serializer_hides_storage_path() -> bool:
    asset = ChannelContentAsset(
        asset_type="video",
        url="https://cdn.example.com/video.mp4",
        storage_path="private/raw/video.mp4",
        mime_type="video/mp4",
        metadata={"pipeline": {"processing_status": "ready"}},
    )
    serialized = json.dumps(ChannelContentAssetSerializer(asset).data)
    return "storage_path" not in serialized and "private/raw/video.mp4" not in serialized


def _media_gate_self_test() -> bool:
    try:
        validate_asset_ready_for_publish(
            {
                "asset_type": "image",
                "url": "https://cdn.example.com/unsafe.jpg",
                "mime_type": "image/jpeg",
                "processing_status": "quarantined",
            }
        )
    except Exception:
        return True
    return False


class Command(BaseCommand):
    help = "Verify non-secret Broadcast/Channels launch guardrails without external provider calls."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        parser.add_argument("--strict", action="store_true", help="Exit non-zero when launch blockers are found.")
        parser.add_argument("--include-counts", action="store_true", help="Query channel/feed/content launch counts.")

    def handle(self, *args, **options):
        checks: list[dict[str, str]] = []

        missing_urls = [name for name, url_args in URL_CHECKS if not _reverse_exists(name, url_args)]
        checks.append(
            {
                "name": "broadcast_channel_urls_present",
                "state": "pass" if not missing_urls else "fail",
                "detail": "channel/feed/public/embed URLs resolve" if not missing_urls else f"missing: {', '.join(missing_urls)}",
            }
        )

        embeds_enabled = _setting_bool("KIS_EMBEDS_ENABLED")
        embed_secret_present = bool(_setting_text("KIS_EMBED_SIGNING_SECRET"))
        checks.extend(
            [
                {
                    "name": "KIS_EMBEDS_ENABLED",
                    "state": "warn" if embeds_enabled else "pass",
                    "detail": "disabled by default" if not embeds_enabled else "enabled; requires signed-token/domain QA before production exposure",
                },
                {
                    "name": "KIS_EMBED_SIGNING_SECRET",
                    "state": "pass" if (not embeds_enabled or embed_secret_present) else "fail",
                    "detail": "presence checked only; value is never printed",
                },
                {
                    "name": "KIS_PUBLIC_WEB_INDEXING_ENABLED",
                    "state": "fail" if _setting_bool("KIS_PUBLIC_WEB_INDEXING_ENABLED") else "pass",
                    "detail": "disabled; public pages should remain noindex until launch evidence is approved"
                    if not _setting_bool("KIS_PUBLIC_WEB_INDEXING_ENABLED")
                    else "public indexing enabled without this command verifying production SEO/privacy evidence",
                },
                {
                    "name": "KIS_PUBLIC_REFERRALS_ENABLED",
                    "state": "warn" if _setting_bool("KIS_PUBLIC_REFERRALS_ENABLED") else "pass",
                    "detail": "disabled by default" if not _setting_bool("KIS_PUBLIC_REFERRALS_ENABLED") else "enabled; verify abuse-safe referral evidence",
                },
            ]
        )

        live_provider = _setting_text("LIVE_STREAM_PROVIDER").lower() or "disabled"
        live_sandbox_enabled = _setting_bool("LIVE_STREAM_PROVIDER_SANDBOX_ENABLED")
        channel_media_live_calls = live_provider_calls_enabled()
        checks.extend(
            [
                {
                    "name": "LIVE_STREAM_PROVIDER",
                    "state": "pass" if live_provider == "disabled" else "warn",
                    "detail": live_provider if live_provider else "disabled",
                },
                {
                    "name": "LIVE_STREAM_PROVIDER_SANDBOX_ENABLED",
                    "state": "warn" if live_sandbox_enabled else "pass",
                    "detail": "disabled by default" if not live_sandbox_enabled else "sandbox enabled; use only with approved staging evidence",
                },
                {
                    "name": "KIS_CHANNEL_MEDIA_LIVE_PROVIDER_CALLS_ENABLED",
                    "state": "fail" if channel_media_live_calls else "pass",
                    "detail": "disabled by default" if not channel_media_live_calls else "live channel media provider calls enabled",
                },
                {
                    "name": "channel_media_provider",
                    "state": "pass",
                    "detail": f"media={configured_media_provider()}, transcode={configured_transcode_provider()}",
                },
            ]
        )

        checks.append(
            {
                "name": "channel_asset_public_serialization",
                "state": "pass" if _asset_serializer_hides_storage_path() else "fail",
                "detail": "asset serializers do not expose raw storage_path",
            }
        )
        checks.append(
            {
                "name": "channel_media_safety_gate",
                "state": "pass" if _media_gate_self_test() else "fail",
                "detail": "quarantined/unsafe assets are blocked before publish or broadcast",
            }
        )

        counts = {
            "channels": None,
            "channel_contents": None,
            "channel_assets": None,
            "subscriptions": None,
            "playlists": None,
            "comments": None,
            "watch_history": None,
            "moderation_records": None,
            "active_embeds": None,
            "legacy_broadcast_items": None,
        }
        count_error = ""
        if options["include_counts"]:
            try:
                counts = {
                    "channels": BroadcastChannel.objects.filter(is_deleted=False).count(),
                    "channel_contents": ChannelContent.objects.filter(is_deleted=False).count(),
                    "channel_assets": ChannelContentAsset.objects.count(),
                    "subscriptions": BroadcastChannelSubscription.objects.count(),
                    "playlists": BroadcastPlaylist.objects.count(),
                    "comments": ChannelContentComment.objects.filter(is_deleted=False).count(),
                    "watch_history": ChannelWatchHistory.objects.count(),
                    "moderation_records": ChannelModerationRecord.objects.count(),
                    "active_embeds": ChannelContentEmbed.objects.filter(is_active=True).count(),
                    "legacy_broadcast_items": BroadcastItem.objects.filter(is_deleted=False).count(),
                }
            except Exception as exc:  # pragma: no cover - environment-dependent diagnostic.
                count_error = exc.__class__.__name__
                checks.append(
                    {
                        "name": "broadcast_channel_database_counts",
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
                "This command does not make live streaming, embed, media, or public-indexing provider calls.",
                "No secrets, raw storage paths, private media paths, or private embed tokens are printed.",
                "Live streaming and public indexing should remain flagged off until staging evidence is attached.",
            ],
        }

        if options["json"]:
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
        else:
            self.stdout.write(f"Broadcast/Channels launch guardrails ready: {result['ready']}")
            for check in checks:
                self.stdout.write(f"- {check['state'].upper()}: {check['name']} - {check['detail']}")
            if options["include_counts"]:
                self.stdout.write(f"Channel/feed counts: {counts}")
            for note in result["notes"]:
                self.stdout.write(f"Note: {note}")

        if failures and options["strict"]:
            raise CommandError(f"Broadcast/Channels launch guardrails failed: {len(failures)} blocker(s).")
