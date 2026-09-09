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
                  "created_at", "expires_at", "expired_at"]
        read_only_fields = ["id", "user", "endorsement_count", "media_kind",
                             "resource_name", "resource_mime_type", "created_at",
                             "expires_at", "expired_at"]

    def get_safe_resource_url(self, obj):
        return _resolve_testimony_media_url(obj.resource_url, self.context.get("request"))

    def _resolve_attachment_intent(self, attachment):
        # Deliberately called BEFORE create()/update() open their own
        # transaction.atomic() block below - resolve_testimony_media() may
        # run a real explicit-content scan and write a MediaSafetyScan
        # audit row + mark_failed() on a rejection. Calling it from inside
        # that later atomic block would roll those writes back the instant
        # it raises (rejecting a blocked attachment), silently destroying
        # the only audit trail of what got blocked and why - exactly the
        # hazard apps.media.upload_intent.confirm_upload_intent's own
        # handler-call site already guards against for the same reason.
        if not attachment:
            return None
        media_id = attachment.get("media_id") or attachment.get("mediaId")
        request = self.context.get("request")
        return resolve_testimony_media(user=request.user, media_id=media_id)

    def _bind_attachment(self, instance, intent, attachment):
        if intent is None:
            return
        instance.resource_url = intent.object_key
        instance.resource_name = str(attachment.get("name") or intent.original_filename or "")[:255]
        instance.resource_mime_type = intent.content_type or ""
        instance.media_kind = "video" if (intent.content_type or "").startswith("video/") else "file"
        instance.save(update_fields=["resource_url", "resource_name", "resource_mime_type", "media_kind", "updated_at"])
        from apps.media.services import lifecycle

        lifecycle.sync_attachment(intent=intent, target_type="testimony.UserTestimony", target_id=str(instance.id))

    def create(self, validated_data):
        attachment = validated_data.pop("resource_attachment", None)
        intent = self._resolve_attachment_intent(attachment)
        # Atomic from here down only: binding a resolved-valid intent can
        # still fail for unrelated DB reasons after the row is inserted,
        # and a rejected attachment must not leave an orphaned text-only
        # testimony behind - but by this point the attachment has already
        # passed (or been rejected with its audit trail intact by) content
        # safety, outside this transaction.
        with transaction.atomic():
            instance = super().create(validated_data)
            self._bind_attachment(instance, intent, attachment)
        return instance

    def update(self, instance, validated_data):
        attachment = validated_data.pop("resource_attachment", None)
        intent = self._resolve_attachment_intent(attachment)
        with transaction.atomic():
            instance = super().update(instance, validated_data)
            self._bind_attachment(instance, intent, attachment)
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
