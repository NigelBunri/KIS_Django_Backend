from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.db import transaction
from common.media_urls import absolutize_backend_media
from apps.broadcasts.media_utils import build_media_url
from . import models
from .media import resolve_testimony_media

User = get_user_model()


def _resolve_testimony_media_url(value: str, request=None) -> str:
    # Same private-object-key vs. already-absolute-URL split as
    # apps.broadcasts.serializers._resolve_education_media_display_url —
    # resource_url here is always our own upload's object key (never a
    # client-pasted link, unlike education materials' link kind), but the
    # resolution logic is identical.
    text = str(value or "").strip()
    if not text:
        return ""
    if not text.startswith("private/"):
        return absolutize_backend_media(text, request)
    try:
        return build_media_url(request, text)
    except Exception:
        return absolutize_backend_media(text, request)


class AuthorSerializer(serializers.ModelSerializer):
    avatar_url = serializers.SerializerMethodField()
    headline   = serializers.SerializerMethodField()

    class Meta:
        model  = User
        fields = ["id", "display_name", "avatar_url", "headline"]

    def get_avatar_url(self, obj):
        p = getattr(obj, "profile", None)
        return getattr(p, "avatar_url", "") or ""

    def get_headline(self, obj):
        p = getattr(obj, "profile", None)
        return getattr(p, "headline", "") or ""


class UserSeasonSerializer(serializers.ModelSerializer):
    user = AuthorSerializer(read_only=True)
    reach_count = serializers.SerializerMethodField()

    class Meta:
        model  = models.UserSeason
        fields = ["id", "user", "category", "title", "description", "visibility",
                  "is_active", "reach_count", "created_at", "resolved_at"]
        read_only_fields = ["id", "user", "reach_count", "created_at"]

    def get_reach_count(self, obj):
        return obj.reaches.count()


class UserTestimonySerializer(serializers.ModelSerializer):
    user = AuthorSerializer(read_only=True)
    # Client posts {"media_id": "<uploadId>"} after the usual initiate ->
    # S3 PUT -> confirm handshake (context "testimony_media") — never a raw
    # storage key or URL. write_only + not a model field: resolved into
    # resource_url/resource_name/resource_mime_type/media_kind in
    # create()/update() below, mirroring how education materials attach.
    resource_attachment = serializers.DictField(write_only=True, required=False)
    safe_resource_url = serializers.SerializerMethodField()

    class Meta:
        model  = models.UserTestimony
        fields = ["id", "user", "category", "title", "story", "is_available",
                  "endorsement_count", "media_kind", "resource_name",
                  "resource_mime_type", "safe_resource_url", "resource_attachment",
                  "created_at"]
        read_only_fields = ["id", "user", "endorsement_count", "media_kind",
                             "resource_name", "resource_mime_type", "created_at"]

    def get_safe_resource_url(self, obj):
        return _resolve_testimony_media_url(obj.resource_url, self.context.get("request"))

    def _apply_attachment(self, instance, attachment):
        if not attachment:
            return
        media_id = attachment.get("media_id") or attachment.get("mediaId")
        request = self.context.get("request")
        intent = resolve_testimony_media(user=request.user, media_id=media_id)
        instance.resource_url = intent.object_key
        instance.resource_name = str(attachment.get("name") or intent.original_filename or "")[:255]
        instance.resource_mime_type = intent.content_type or ""
        instance.media_kind = "video" if (intent.content_type or "").startswith("video/") else "file"
        instance.save(update_fields=["resource_url", "resource_name", "resource_mime_type", "media_kind", "updated_at"])
        from apps.media.services import lifecycle

        lifecycle.sync_attachment(intent=intent, target_type="testimony.UserTestimony", target_id=str(instance.id))

    @transaction.atomic
    def create(self, validated_data):
        # Atomic: _apply_attachment can raise (bad/foreign/reused media_id)
        # after the row above is already inserted — without this, a
        # rejected attachment left an orphaned text-only testimony behind
        # instead of failing the request cleanly.
        attachment = validated_data.pop("resource_attachment", None)
        instance = super().create(validated_data)
        self._apply_attachment(instance, attachment)
        return instance

    @transaction.atomic
    def update(self, instance, validated_data):
        attachment = validated_data.pop("resource_attachment", None)
        instance = super().update(instance, validated_data)
        self._apply_attachment(instance, attachment)
        return instance


class TestimonyReachSerializer(serializers.ModelSerializer):
    from_user = AuthorSerializer(read_only=True)
    to_user   = AuthorSerializer(read_only=True)
    season    = UserSeasonSerializer(read_only=True)
    testimony = UserTestimonySerializer(read_only=True)

    class Meta:
        model  = models.TestimonyReach
        fields = ["id", "from_user", "to_user", "season", "testimony",
                  "message", "status", "created_at"]
        read_only_fields = ["id", "from_user", "to_user", "season", "testimony", "created_at"]
