# media/serializers.py
from rest_framework import serializers
from .models import (
    MediaAsset, MediaVariant, ProcessingJob, Provenance, Watermark, AccessPolicy, MediaMetrics, MediaSafetyScan
)

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

    class Meta:
        model = MediaAsset
        fields = ("id", "owner", "type", "bucket_key", "canonical_url", "mime_type",
                  "bytes", "dims", "checksum", "status", "security", "provenance",
                  "labels", "storage", "metadata", "variants", "metrics")

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
