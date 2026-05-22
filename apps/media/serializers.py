# media/serializers.py
from django.core import signing
from rest_framework import serializers
from .models import (
    MediaAsset, MediaVariant, ProcessingJob, Provenance, Watermark, AccessPolicy, MediaMetrics, MediaSafetyScan
)


def _payload_visibility(payload) -> str:
    if not isinstance(payload, dict):
        return ""
    value = payload.get("visibility") or payload.get("access") or payload.get("privacy")
    return str(value or "").strip().lower()


def _asset_is_private(asset: MediaAsset) -> bool:
    for payload in (asset.storage or {}, asset.metadata or {}, asset.security or {}):
        visibility = _payload_visibility(payload)
        if visibility in {"private", "restricted", "owner", "authenticated", "tenant"}:
            return True
        if payload.get("private") is True or payload.get("public") is False:
            return True
    return False


class MediaVariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = MediaVariant
        fields = "__all__"


class MediaMetricsSerializer(serializers.ModelSerializer):
    class Meta:
        model = MediaMetrics
        fields = "__all__"


class MediaAssetSerializer(serializers.ModelSerializer):
    variants = MediaVariantSerializer(many=True, read_only=True)
    metrics = MediaMetricsSerializer(read_only=True)
    private = serializers.SerializerMethodField()
    display_url = serializers.SerializerMethodField()
    public_url = serializers.SerializerMethodField()

    class Meta:
        model = MediaAsset
        fields = (
            "id",
            "owner",
            "type",
            "bucket_key",
            "canonical_url",
            "display_url",
            "public_url",
            "private",
            "mime_type",
            "bytes",
            "dims",
            "checksum",
            "status",
            "security",
            "provenance",
            "labels",
            "storage",
            "metadata",
            "variants",
            "metrics",
        )

    def get_private(self, obj):
        return _asset_is_private(obj)

    def get_public_url(self, obj):
        return obj.canonical_url or ""

    def get_display_url(self, obj):
        if obj.status != "ready":
            return ""
        if not _asset_is_private(obj):
            return obj.canonical_url or ""
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return ""
        if not (user.is_staff or obj.owner_id == user.id):
            return ""
        token = signing.TimestampSigner(salt="kis-media-download").sign(str(obj.id))
        path = f"/api/v1/assets/{obj.id}/download/?token={token}"
        return request.build_absolute_uri(path) if request else path

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not user.is_staff:
            data.pop("bucket_key", None)
        return data


class ProcessingJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcessingJob
        fields = "__all__"


class ProvenanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Provenance
        fields = "__all__"


class AccessPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = AccessPolicy
        fields = "__all__"


class MediaSafetyScanSerializer(serializers.ModelSerializer):
    class Meta:
        model = MediaSafetyScan
        fields = (
            "id",
            "asset",
            "upload_id",
            "context",
            "original_name",
            "mime_type",
            "bytes",
            "provider",
            "status",
            "quarantine",
            "requires_review",
            "policy_version",
            "reason",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields
