from rest_framework import serializers

from apps.websites.embeds import validate_embed
from apps.websites.forms import validate_field_schema
from apps.websites.kis_video import KIS_VIDEO_SOURCES
from apps.websites.models import KIS_CONTENT_TARGET_TYPES, SECTION_TYPES, SECTION_VARIANTS, Website, WebsitePage


class WebsitePageSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebsitePage
        fields = [
            "id", "website", "slug", "title", "is_home", "sort_order",
            "sections", "seo", "status", "published_at", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "website", "is_home", "published_at", "created_at", "updated_at"]

    def validate_sections(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("sections must be a list.")
        for entry in value:
            if not isinstance(entry, dict) or "type" not in entry:
                raise serializers.ValidationError("Each section must be an object with a 'type'.")
            if entry["type"] not in SECTION_TYPES:
                raise serializers.ValidationError(f"Unknown section type: {entry['type']!r}.")
            if entry["type"] == "kis_content":
                data = entry.get("data") or {}
                if data.get("target_type") not in KIS_CONTENT_TARGET_TYPES:
                    raise serializers.ValidationError(
                        f"kis_content section has an invalid target_type: {data.get('target_type')!r}."
                    )
            if entry["type"] == "form":
                fields = (entry.get("data") or {}).get("fields") or []
                validate_field_schema(fields)
            if entry["type"] == "embed":
                validate_embed(entry.get("data") or {})
            if entry["type"] == "kis_video":
                data = entry.get("data") or {}
                if data.get("source") not in KIS_VIDEO_SOURCES:
                    raise serializers.ValidationError(
                        f"kis_video section has an invalid source: {data.get('source')!r}."
                    )
                if not data.get("target_id"):
                    raise serializers.ValidationError("kis_video section requires a target_id.")
            variant = entry.get("variant")
            if variant is not None:
                allowed_variants = SECTION_VARIANTS.get(entry["type"])
                if not allowed_variants:
                    raise serializers.ValidationError(
                        f"Section type {entry['type']!r} does not support a variant."
                    )
                if variant not in allowed_variants:
                    raise serializers.ValidationError(
                        f"Unknown variant {variant!r} for section type {entry['type']!r}."
                    )
            responsive = entry.get("responsive")
            if responsive is not None:
                if not isinstance(responsive, dict):
                    raise serializers.ValidationError("responsive must be an object.")
                hidden_on = responsive.get("hidden_on") or []
                if not isinstance(hidden_on, list) or any(v not in ("mobile", "desktop") for v in hidden_on):
                    raise serializers.ValidationError(
                        "responsive.hidden_on may only contain 'mobile' and/or 'desktop'."
                    )
        return value


class WebsiteSerializer(serializers.ModelSerializer):
    pages = WebsitePageSerializer(many=True, read_only=True)

    class Meta:
        model = Website
        fields = [
            "id", "owner_type", "owner_id", "slug", "name", "branding", "default_seo",
            "status", "published_at", "unpublished_at", "seeded_from_legacy", "pages",
            "custom_domain", "custom_domain_status", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "owner_type", "owner_id", "status", "published_at", "unpublished_at",
            "seeded_from_legacy", "pages", "custom_domain", "custom_domain_status",
            "created_at", "updated_at",
        ]
