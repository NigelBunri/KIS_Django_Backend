from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from rest_framework import serializers

from common.media_urls import absolutize_backend_media, normalize_image_payload

from .models import (
    HealthDashboardInstitutionLandingPage,
    HealthDashboardLandingPageAddress,
    HealthDashboardLandingPageContact,
    HealthDashboardLandingPageImage,
    HealthDashboardLandingPageOperatingHour,
    HealthDashboardLandingPageService,
    HealthDashboardLandingPageSocialLink,
)

USD_CENTS_PER_KISC = 10000


def _cents_to_kisc_text(cents_value: Any) -> str:
    try:
        cents = int(cents_value or 0)
    except (TypeError, ValueError):
        cents = 0
    safe = max(0, cents)
    value = (Decimal(safe) / Decimal(USD_CENTS_PER_KISC)).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
    text = format(value, "f").rstrip("0").rstrip(".")
    return text or "0"


def _kisc_to_cents(value: Any) -> int:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise serializers.ValidationError("Invalid KISC amount.")
    if parsed < 0:
        raise serializers.ValidationError("KISC amount cannot be negative.")
    return int((parsed * Decimal(USD_CENTS_PER_KISC)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class HealthDashboardLandingPageAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = HealthDashboardLandingPageAddress
        fields = (
            "line_one",
            "line_two",
            "city",
            "state",
            "postal_code",
            "country",
            "landmark",
        )


class HealthDashboardLandingPageContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = HealthDashboardLandingPageContact
        fields = (
            "primary_phone",
            "secondary_phone",
            "email",
            "website_url",
            "whatsapp_phone",
        )


class HealthDashboardLandingPageServiceSerializer(serializers.ModelSerializer):
    institution_service_uid = serializers.SerializerMethodField()
    price_kisc = serializers.SerializerMethodField()

    class Meta:
        model = HealthDashboardLandingPageService
        fields = (
            "id",
            "institution_service_uid",
            "title",
            "description",
            "price_cents",
            "price_kisc",
            "is_active",
            "sort_order",
        )

    def get_institution_service_uid(self, obj: HealthDashboardLandingPageService) -> str:
        if obj.institution_service:
            return str(obj.institution_service.service_uid or "")
        return ""

    def get_price_kisc(self, obj: HealthDashboardLandingPageService) -> str:
        return _cents_to_kisc_text(getattr(obj, "price_cents", 0))


class HealthDashboardLandingPageImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = HealthDashboardLandingPageImage
        fields = ("id", "image_url", "caption", "sort_order")

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        payload["image_url"] = absolutize_backend_media(payload.get("image_url"), request=self.context.get("request"))
        return payload


class HealthDashboardLandingPageSocialLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = HealthDashboardLandingPageSocialLink
        fields = ("id", "platform", "url", "sort_order")


class HealthDashboardLandingPageOperatingHourSerializer(serializers.ModelSerializer):
    class Meta:
        model = HealthDashboardLandingPageOperatingHour
        fields = ("id", "day_key", "opens_at", "closes_at", "is_closed", "sort_order")


class HealthDashboardLandingPageSerializer(serializers.ModelSerializer):
    address = HealthDashboardLandingPageAddressSerializer(required=False, allow_null=True)
    contact = HealthDashboardLandingPageContactSerializer(required=False, allow_null=True)
    services = HealthDashboardLandingPageServiceSerializer(many=True, read_only=True)
    images = HealthDashboardLandingPageImageSerializer(many=True, read_only=True)
    social_links = HealthDashboardLandingPageSocialLinkSerializer(many=True, read_only=True)
    operating_hours = HealthDashboardLandingPageOperatingHourSerializer(many=True, read_only=True)
    institution_id = serializers.CharField(source="dashboard.institution_uid", read_only=True)
    institution_name = serializers.CharField(source="dashboard.name", read_only=True)

    class Meta:
        model = HealthDashboardInstitutionLandingPage
        fields = (
            "institution_id",
            "institution_name",
            "title",
            "description",
            "hero_headline",
            "hero_subheadline",
            "hero_cta_label",
            "hero_cta_url",
            "logo_url",
            "background_image_url",
            "background_color_key",
            "is_published",
            "address",
            "contact",
            "services",
            "images",
            "social_links",
            "operating_hours",
            "created_at",
            "updated_at",
        )

    def to_representation(self, instance: HealthDashboardInstitutionLandingPage) -> dict[str, Any]:
        payload = super().to_representation(instance)
        payload["institutionId"] = payload.get("institution_id", "")
        payload["institutionName"] = payload.get("institution_name", "")
        payload["heroHeadline"] = payload.get("hero_headline", "")
        payload["heroSubheadline"] = payload.get("hero_subheadline", "")
        payload["heroCtaLabel"] = payload.get("hero_cta_label", "")
        payload["heroCtaUrl"] = payload.get("hero_cta_url", "")
        payload["logo_url"] = absolutize_backend_media(payload.get("logo_url"), request=self.context.get("request"))
        payload["background_image_url"] = absolutize_backend_media(
            payload.get("background_image_url"),
            request=self.context.get("request"),
        )
        payload["logoUrl"] = payload.get("logo_url", "")
        payload["backgroundImageUrl"] = payload.get("background_image_url", "")
        payload["backgroundColorKey"] = payload.get("background_color_key", "")
        payload["isPublished"] = payload.get("is_published", False)
        payload["socialLinks"] = payload.get("social_links", [])
        payload["operatingHours"] = payload.get("operating_hours", [])
        return payload


class _AddressInputSerializer(serializers.Serializer):
    line_one = serializers.CharField(required=False, allow_blank=True, max_length=255)
    line_two = serializers.CharField(required=False, allow_blank=True, max_length=255)
    city = serializers.CharField(required=False, allow_blank=True, max_length=120)
    state = serializers.CharField(required=False, allow_blank=True, max_length=120)
    postal_code = serializers.CharField(required=False, allow_blank=True, max_length=32)
    country = serializers.CharField(required=False, allow_blank=True, max_length=120)
    landmark = serializers.CharField(required=False, allow_blank=True, max_length=255)


class _ContactInputSerializer(serializers.Serializer):
    primary_phone = serializers.CharField(required=False, allow_blank=True, max_length=64)
    secondary_phone = serializers.CharField(required=False, allow_blank=True, max_length=64)
    email = serializers.EmailField(required=False, allow_blank=True)
    website_url = serializers.CharField(required=False, allow_blank=True)
    whatsapp_phone = serializers.CharField(required=False, allow_blank=True, max_length=64)


class _ServiceInputSerializer(serializers.Serializer):
    institution_service_uid = serializers.CharField(required=False, allow_blank=True, max_length=128)
    title = serializers.CharField(required=False, allow_blank=True, max_length=255)
    description = serializers.CharField(required=False, allow_blank=True)
    price_cents = serializers.IntegerField(required=False, min_value=0)
    price_kisc = serializers.DecimalField(required=False, min_value=0, max_digits=20, decimal_places=5)
    is_active = serializers.BooleanField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        title = str(attrs.get("title") or "").strip()
        service_uid = str(attrs.get("institution_service_uid") or "").strip()
        if not title and not service_uid:
            raise serializers.ValidationError("Each service needs a title or an institution_service_uid.")
        if "price_kisc" in attrs and "price_cents" not in attrs:
            attrs["price_cents"] = _kisc_to_cents(attrs.get("price_kisc"))
        return attrs


class _ImageInputSerializer(serializers.Serializer):
    image_url = serializers.CharField()
    caption = serializers.CharField(required=False, allow_blank=True, max_length=255)


class _SocialInputSerializer(serializers.Serializer):
    platform = serializers.CharField(required=False, allow_blank=True, max_length=64)
    url = serializers.CharField()


class _OperatingHourInputSerializer(serializers.Serializer):
    day_key = serializers.CharField(max_length=16)
    opens_at = serializers.CharField(required=False, allow_blank=True, max_length=16)
    closes_at = serializers.CharField(required=False, allow_blank=True, max_length=16)
    is_closed = serializers.BooleanField(required=False)


class HealthDashboardLandingPageUpsertSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True, max_length=255)
    description = serializers.CharField(required=False, allow_blank=True)
    hero_headline = serializers.CharField(required=False, allow_blank=True, max_length=255)
    hero_subheadline = serializers.CharField(required=False, allow_blank=True)
    hero_cta_label = serializers.CharField(required=False, allow_blank=True, max_length=120)
    hero_cta_url = serializers.CharField(required=False, allow_blank=True)
    logo_url = serializers.CharField(required=False, allow_blank=True)
    background_image_url = serializers.CharField(required=False, allow_blank=True)
    background_color_key = serializers.CharField(required=False, allow_blank=True, max_length=64)
    is_published = serializers.BooleanField(required=False)

    address = _AddressInputSerializer(required=False)
    contact = _ContactInputSerializer(required=False)
    services = _ServiceInputSerializer(many=True, required=False)
    images = _ImageInputSerializer(many=True, required=False)
    social_links = _SocialInputSerializer(many=True, required=False)
    operating_hours = _OperatingHourInputSerializer(many=True, required=False)

    def to_internal_value(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise serializers.ValidationError("Landing page payload must be an object.")

        payload = dict(data)

        def _copy_alias(target: dict[str, Any], key: str, alias: str):
            if key not in target and alias in target:
                target[key] = target[alias]

        # Root aliases
        _copy_alias(payload, "hero_headline", "heroHeadline")
        _copy_alias(payload, "hero_subheadline", "heroSubheadline")
        _copy_alias(payload, "hero_cta_label", "heroCtaLabel")
        _copy_alias(payload, "hero_cta_url", "heroCtaUrl")
        _copy_alias(payload, "logo_url", "logoUrl")
        _copy_alias(payload, "background_image_url", "backgroundImageUrl")
        _copy_alias(payload, "background_color_key", "backgroundColorKey")
        _copy_alias(payload, "is_published", "isPublished")
        _copy_alias(payload, "social_links", "socialLinks")
        _copy_alias(payload, "operating_hours", "operatingHours")

        if "logo_url" in payload:
            payload["logo_url"] = normalize_image_payload(payload.get("logo_url"))
        if "background_image_url" in payload:
            payload["background_image_url"] = normalize_image_payload(payload.get("background_image_url"))

        address = payload.get("address")
        if isinstance(address, dict):
            address_payload = dict(address)
            _copy_alias(address_payload, "line_one", "lineOne")
            _copy_alias(address_payload, "line_two", "lineTwo")
            _copy_alias(address_payload, "postal_code", "postalCode")
            payload["address"] = address_payload

        contact = payload.get("contact")
        if isinstance(contact, dict):
            contact_payload = dict(contact)
            _copy_alias(contact_payload, "primary_phone", "primaryPhone")
            _copy_alias(contact_payload, "secondary_phone", "secondaryPhone")
            _copy_alias(contact_payload, "website_url", "websiteUrl")
            _copy_alias(contact_payload, "whatsapp_phone", "whatsappPhone")
            payload["contact"] = contact_payload

        service_rows = payload.get("services")
        if isinstance(service_rows, list):
            normalized_services = []
            for row in service_rows:
                if not isinstance(row, dict):
                    continue
                service_payload = dict(row)
                _copy_alias(service_payload, "institution_service_uid", "institutionServiceUid")
                _copy_alias(service_payload, "institution_service_uid", "service_uid")
                _copy_alias(service_payload, "title", "name")
                _copy_alias(service_payload, "price_cents", "priceCents")
                _copy_alias(service_payload, "price_kisc", "priceKisc")
                _copy_alias(service_payload, "is_active", "isActive")
                normalized_services.append(service_payload)
            payload["services"] = normalized_services

        image_rows = payload.get("images")
        if isinstance(image_rows, list):
            normalized_images = []
            for row in image_rows:
                if not isinstance(row, dict):
                    continue
                image_payload = dict(row)
                _copy_alias(image_payload, "image_url", "imageUrl")
                if "image_url" in image_payload:
                    image_payload["image_url"] = normalize_image_payload(image_payload.get("image_url"))
                normalized_images.append(image_payload)
            payload["images"] = normalized_images

        social_rows = payload.get("social_links")
        if isinstance(social_rows, list):
            normalized_social = []
            for row in social_rows:
                if not isinstance(row, dict):
                    continue
                social_payload = dict(row)
                normalized_social.append(social_payload)
            payload["social_links"] = normalized_social

        hour_rows = payload.get("operating_hours")
        if isinstance(hour_rows, list):
            normalized_hours = []
            for row in hour_rows:
                if not isinstance(row, dict):
                    continue
                hour_payload = dict(row)
                _copy_alias(hour_payload, "day_key", "dayKey")
                _copy_alias(hour_payload, "opens_at", "opensAt")
                _copy_alias(hour_payload, "closes_at", "closesAt")
                _copy_alias(hour_payload, "is_closed", "isClosed")
                normalized_hours.append(hour_payload)
            payload["operating_hours"] = normalized_hours

        return super().to_internal_value(payload)
