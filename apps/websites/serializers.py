from rest_framework import serializers

from apps.websites.embeds import validate_embed
from apps.websites.forms import validate_field_schema
from apps.websites.models import KIS_CONTENT_TARGET_TYPES, SECTION_TYPES, Website, WebsitePage


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
