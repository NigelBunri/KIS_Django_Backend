"""
Serializers for accounts app. Advanced validations, nested create/update and read-only protections.
"""
from rest_framework import serializers
from common.media_urls import absolutize_backend_media, normalize_image_payload
from django.db import transaction, IntegrityError
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth import authenticate
from apps.core.phone_utils import to_e164
from django.utils.translation import gettext_lazy as _

import datetime
import re
import phonenumbers
from typing import Any

from .models import (
    User,
    Profile,
    AccountTier,
    Subscription,
    Session,
    Device,
    E2EDeviceKey,
    UsageQuota,
    ApiToken,
    API_TOKEN_DEFAULT_EXPIRES_DAYS,
    Experience,
    Education,
    UserSkill,
    Project,
    Recommendation,
    ProfileFieldVisibility,
    ProfileFieldVisibilityAllowTarget,
    ProfileArticle,
    ProfileArticleAllowTarget,
    ProfilePreferences,
    ProfileLanguage,
    ProfileShowcase,
)
from .tier_presets import get_tier_preset_features


def _normalize_country_code(value: str | None) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return f"+{digits}" if digits else ""


def _normalize_local_phone(value: str | None) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _split_phone_parts(phone_raw: str | None, default_region: str = "CM") -> tuple[str, str]:
    raw = str(phone_raw or "").strip()
    if not raw:
        return "", ""
    try:
        if raw.startswith("+"):
            parsed = phonenumbers.parse(raw, None)
        else:
            parsed = phonenumbers.parse(raw, default_region or "CM")
        if phonenumbers.is_possible_number(parsed):
            country_code = f"+{parsed.country_code}" if parsed.country_code else ""
            national = str(parsed.national_number or "")
            if national:
                return country_code, national
    except Exception:
        pass
    digits = _normalize_local_phone(raw)
    return "", digits


def _compose_phone(country_code: str | None, phone_number: str | None) -> str:
    local_digits = _normalize_local_phone(phone_number)
    if not local_digits:
        return ""
    dial_code = _normalize_country_code(country_code)
    return f"{dial_code}{local_digits}" if dial_code else local_digits


def _normalize_allow_targets(raw_values: Any) -> list[str]:
    if raw_values is None:
        return []
    if not isinstance(raw_values, list):
        raise serializers.ValidationError("allow_user_ids must be a list of phone numbers or user ids.")
    normalized: list[str] = []
    seen = set()
    for value in raw_values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _resolve_allow_target(raw_value: str, country_hint: str = "CM") -> dict[str, Any]:
    value = str(raw_value or "").strip()
    digits = _normalize_local_phone(value)
    normalized_phone = ""

    try:
        normalized_phone = to_e164(value, country_hint)
    except Exception:
        if value.startswith("+"):
            normalized_phone = value

    target_user = User.objects.filter(id=value).first()
    if not target_user and normalized_phone:
        target_user = User.objects.filter(phone=normalized_phone).first()
    if not target_user and digits:
        target_user = User.objects.filter(phone_number=digits).order_by("id").first()

    if target_user:
        phone = str(getattr(target_user, "phone", "") or "").strip()
        phone_number = str(getattr(target_user, "phone_number", "") or "").strip()
        return {
            "target_user": target_user,
            "target_phone": phone or normalized_phone or value,
            "target_phone_number": phone_number or digits,
        }

    return {
        "target_user": None,
        "target_phone": normalized_phone or (value if value.startswith("+") else ""),
        "target_phone_number": digits,
    }

# -------------------------------------------------------------------
# Profile serializer
# -------------------------------------------------------------------
class ProfileSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True)
    user = serializers.PrimaryKeyRelatedField(read_only=True)
    avatar_url = serializers.SerializerMethodField()
    cover_url = serializers.SerializerMethodField()
    avatar_file = serializers.ImageField(write_only=True, required=False, allow_null=True)
    cover_file = serializers.ImageField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = Profile
        fields = [
            "id",
            "user",
            "avatar_url",
            "cover_url",
            "avatar_file",
            "cover_file",
            "headline",
            "bio",
            "industry",
            "completion_score",
            "visibility",
            "branding_prefs",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "user", "completion_score", "created_at", "updated_at")

    def get_avatar_url(self, obj: Profile) -> str | None:
        if obj.avatar_file:
            request = self.context.get("request")
            url = obj.avatar_file.url
            return request.build_absolute_uri(url) if request else url
        return absolutize_backend_media(obj.avatar_url, request=self.context.get("request")) or None

    def get_cover_url(self, obj: Profile) -> str | None:
        if obj.cover_file:
            request = self.context.get("request")
            url = obj.cover_file.url
            return request.build_absolute_uri(url) if request else url
        return absolutize_backend_media(obj.cover_url, request=self.context.get("request")) or None

    def validate_avatar_url(self, value):
        return normalize_image_payload(value)

    def validate_cover_url(self, value):
        return normalize_image_payload(value)

    def update(self, instance, validated_data):
        result = super().update(instance, validated_data)
        if any(k in validated_data for k in ("avatar_url", "avatar_file", "headline", "bio")):
            try:
                instance.update_completion()
            except Exception:
                pass
        return result


class ProfileFieldVisibilitySerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True)
    user = serializers.PrimaryKeyRelatedField(read_only=True)
    allow_user_ids = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )

    class Meta:
        model = ProfileFieldVisibility
        fields = [
            "id",
            "user",
            "field_key",
            "visibility",
            "allow_user_ids",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "user", "created_at", "updated_at")

    def validate_allow_user_ids(self, value):
        return _normalize_allow_targets(value)

    def get_allow_user_ids(self, obj: ProfileFieldVisibility) -> list[str]:
        values: list[str] = []
        for target in obj.allow_targets.select_related("target_user").all():
            if target.target_user_id:
                values.append(str(target.target_user_id))
                continue
            if target.target_phone:
                values.append(str(target.target_phone))
                continue
            if target.target_phone_number:
                values.append(str(target.target_phone_number))
        return values

    def _sync_allow_targets(self, instance: ProfileFieldVisibility, values: list[str]) -> None:
        instance.allow_targets.all().delete()
        if not values:
            return
        request = self.context.get("request")
        country_hint = str(getattr(getattr(request, "user", None), "country", "") or "CM").upper()
        created = set()
        for raw in values:
            resolved = _resolve_allow_target(raw, country_hint=country_hint)
            dedupe_key = (
                str(getattr(resolved.get("target_user"), "id", "")),
                str(resolved.get("target_phone", "")),
                str(resolved.get("target_phone_number", "")),
            )
            if dedupe_key in created:
                continue
            created.add(dedupe_key)
            ProfileFieldVisibilityAllowTarget.objects.create(
                rule=instance,
                target_user=resolved.get("target_user"),
                target_phone=str(resolved.get("target_phone", "") or ""),
                target_phone_number=str(resolved.get("target_phone_number", "") or ""),
            )

    def create(self, validated_data):
        allow_values = validated_data.pop("allow_user_ids", [])
        instance = super().create(validated_data)
        self._sync_allow_targets(instance, allow_values)
        return instance

    def update(self, instance, validated_data):
        allow_values = validated_data.pop("allow_user_ids", None)
        instance = super().update(instance, validated_data)
        if allow_values is not None:
            self._sync_allow_targets(instance, allow_values)
        return instance

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        payload["allow_user_ids"] = self.get_allow_user_ids(instance)
        return payload


class ProfileArticleSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True)
    user = serializers.PrimaryKeyRelatedField(read_only=True)
    allow_user_ids = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )

    class Meta:
        model = ProfileArticle
        fields = [
            "id",
            "user",
            "title",
            "summary",
            "body",
            "cover_url",
            "tags",
            "status",
            "visibility",
            "allow_user_ids",
            "published_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "user", "published_at", "created_at", "updated_at")

    def validate_allow_user_ids(self, value):
        return _normalize_allow_targets(value)

    def get_allow_user_ids(self, obj: ProfileArticle) -> list[str]:
        values: list[str] = []
        for target in obj.allow_targets.select_related("target_user").all():
            if target.target_user_id:
                values.append(str(target.target_user_id))
                continue
            if target.target_phone:
                values.append(str(target.target_phone))
                continue
            if target.target_phone_number:
                values.append(str(target.target_phone_number))
        return values

    def _sync_allow_targets(self, instance: ProfileArticle, values: list[str]) -> None:
        instance.allow_targets.all().delete()
        if not values:
            return
        request = self.context.get("request")
        country_hint = str(getattr(getattr(request, "user", None), "country", "") or "CM").upper()
        created = set()
        for raw in values:
            resolved = _resolve_allow_target(raw, country_hint=country_hint)
            dedupe_key = (
                str(getattr(resolved.get("target_user"), "id", "")),
                str(resolved.get("target_phone", "")),
                str(resolved.get("target_phone_number", "")),
            )
            if dedupe_key in created:
                continue
            created.add(dedupe_key)
            ProfileArticleAllowTarget.objects.create(
                article=instance,
                target_user=resolved.get("target_user"),
                target_phone=str(resolved.get("target_phone", "") or ""),
                target_phone_number=str(resolved.get("target_phone_number", "") or ""),
            )

    def create(self, validated_data):
        allow_values = validated_data.pop("allow_user_ids", [])
        if self.context.get("request"):
            validated_data["user"] = self.context["request"].user
        if validated_data.get("status") == "published" and not validated_data.get("published_at"):
            validated_data["published_at"] = timezone.now()
        instance = super().create(validated_data)
        self._sync_allow_targets(instance, allow_values)
        return instance

    def update(self, instance, validated_data):
        allow_values = validated_data.pop("allow_user_ids", None)
        if validated_data.get("status") == "published" and not instance.published_at:
            validated_data["published_at"] = timezone.now()
        instance = super().update(instance, validated_data)
        if allow_values is not None:
            self._sync_allow_targets(instance, allow_values)
        return instance

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        payload["allow_user_ids"] = self.get_allow_user_ids(instance)
        return payload


class ProfilePreferencesSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True)
    user = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = ProfilePreferences
        fields = [
            "id",
            "user",
            "services",
            "availability",
            "skill_badges",
            "languages",
            "location",
            "compensation",
            "social_proof",
            "ask_tags",
            "highlights",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "user", "created_at", "updated_at")


class ProfileLanguageSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True)
    user = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = ProfileLanguage
        fields = [
            "id",
            "user",
            "name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "user", "created_at", "updated_at")

    def validate_name(self, value):
        normalized = str(value or "").strip()
        if not normalized:
            raise serializers.ValidationError("Language name is required.")
        return normalized


class ProfileShowcaseSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True)
    user = serializers.PrimaryKeyRelatedField(read_only=True)
    file_url = serializers.SerializerMethodField()
    file = serializers.FileField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = ProfileShowcase
        fields = [
            "id",
            "user",
            "type",
            "title",
            "summary",
            "payload",
            "file",
            "file_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "user", "file_url", "created_at", "updated_at")

    def get_file_url(self, obj: ProfileShowcase) -> str | None:
        if not obj.file:
            return None
        request = self.context.get("request")
        url = obj.file.url
        return request.build_absolute_uri(url) if request else url

    def create(self, validated_data):
        if self.context.get("request"):
            validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


# -------------------------------------------------------------------
# User serializers
# -------------------------------------------------------------------
class UserSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer(read_only=True)

    class Meta:
        model = User
        exclude = ("password", "is_superuser", "is_staff", "user_permissions", "groups")
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
            "trust_score",
            "last_login_at",
            "last_password_change_at",
            "email_verified",
        )

    def validate(self, attrs):
        country = str(attrs.get("country") or getattr(self.instance, "country", None) or "CM").upper()

        incoming_phone = attrs.get("phone")
        incoming_code = attrs.get("phone_country_code")
        incoming_number = attrs.get("phone_number")

        if incoming_phone is not None and incoming_phone != "":
            parsed_code, parsed_number = _split_phone_parts(str(incoming_phone), default_region=country)
            if parsed_number:
                attrs["phone_country_code"] = attrs.get("phone_country_code") or parsed_code or None
                attrs["phone_number"] = attrs.get("phone_number") or parsed_number

        if incoming_code is not None or incoming_number is not None:
            code = attrs.get(
                "phone_country_code",
                getattr(self.instance, "phone_country_code", None),
            )
            number = attrs.get(
                "phone_number",
                getattr(self.instance, "phone_number", None),
            )
            normalized_code = _normalize_country_code(code)
            normalized_number = _normalize_local_phone(number)

            if normalized_number:
                attrs["phone_country_code"] = normalized_code or None
                attrs["phone_number"] = normalized_number
                attrs["phone"] = _compose_phone(normalized_code, normalized_number)
            else:
                attrs["phone_country_code"] = None
                attrs["phone_number"] = None

        return attrs


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True, trim_whitespace=False)
    password2 = serializers.CharField(write_only=True, required=True, trim_whitespace=False)
    display_name = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    phone_country_code = serializers.CharField(required=False, allow_blank=True)
    phone_number = serializers.CharField(required=False, allow_blank=True)
    country = serializers.CharField(required=True, allow_blank=False)

    class Meta:
        model = User
        fields = (
            "password",
            "password2",
            "display_name",
            "phone",
            "phone_country_code",
            "phone_number",
            "country",
        )

    def validate(self, attrs):
        # Passwords match
        if attrs.get("password") != attrs.get("password2"):
            raise serializers.ValidationError({"password": "Password fields didn't match."})

        # Country required (keep free-form unless you enforce ISO codes)
        country = attrs.get("country", "").strip()
        if not country:
            raise serializers.ValidationError({"country": "Country is required."})

        phone_raw = str(attrs.get("phone") or "").strip()
        normalized_code = _normalize_country_code(attrs.get("phone_country_code"))
        normalized_number = _normalize_local_phone(attrs.get("phone_number"))

        if phone_raw:
            if not (phone_raw.startswith("+") or phone_raw[0].isdigit()):
                raise serializers.ValidationError({"phone": "Invalid phone format. Use digits, optional leading '+'."})
            parsed_code, parsed_number = _split_phone_parts(phone_raw, default_region=country.upper())
            if parsed_number and not normalized_number:
                normalized_number = parsed_number
            if parsed_code and not normalized_code:
                normalized_code = parsed_code

        if not normalized_number:
            fallback_digits = _normalize_local_phone(phone_raw)
            if fallback_digits:
                normalized_number = fallback_digits

        if not normalized_number:
            raise serializers.ValidationError({"phone_number": "Phone number is required."})

        composed_phone = _compose_phone(normalized_code, normalized_number)
        if not composed_phone:
            raise serializers.ValidationError({"phone": "Phone number is required."})

        attrs["phone_country_code"] = normalized_code or None
        attrs["phone_number"] = normalized_number
        attrs["phone"] = composed_phone

        existing = User.objects.filter(phone_number=normalized_number)
        if existing.exists():
            raise serializers.ValidationError({"phone_number": "Phone number already in use."})

        # Built-in password validators
        from django.contrib.auth.password_validation import validate_password
        try:
            validate_password(attrs.get("password"))
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)})

        return attrs

    def create(self, validated_data):
        # Clean payload
        validated_data.pop("password2", None)
        raw_password = validated_data.pop("password")
        raw_phone = str(validated_data.get("phone") or "").strip()
        default_region = str(validated_data.get("country") or "CM").upper()

        if not validated_data.get("phone_number"):
            parsed_code, parsed_number = _split_phone_parts(raw_phone, default_region=default_region)
            if parsed_number:
                validated_data["phone_country_code"] = validated_data.get("phone_country_code") or parsed_code or None
                validated_data["phone_number"] = parsed_number

        try:
            user = User.objects.create_user(password=raw_password, **validated_data)
        except DjangoValidationError as exc:
            # Surface model/manager level validation clearly
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages)
        except IntegrityError:
            # Likely a unique conflict on phone (or username if you later add it)
            raise serializers.ValidationError({"phone": "A user with this phone already exists."})

        return user

# -------------------------------------------------------------------
# ApiToken serializers
# -------------------------------------------------------------------
class ApiTokenListSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiToken
        fields = [
            "id",
            "name",
            "scopes",
            "created_at",
            "expires_at",
            "last_used_at",
            "last_used_ip",
        ]
        read_only_fields = fields

class ApiTokenCreateSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True)
    scopes = serializers.ListField(child=serializers.CharField(), required=False)
    expires_in_days = serializers.IntegerField(
        default=API_TOKEN_DEFAULT_EXPIRES_DAYS,
        min_value=1,
    )


# -------------------------------------------------------------------
# Account tier, subscription, session serializers
# -------------------------------------------------------------------
class AccountTierSerializer(serializers.ModelSerializer):
    feature_list = serializers.SerializerMethodField()
    feature_tagline = serializers.SerializerMethodField()
    feature_badge = serializers.SerializerMethodField()
    feature_highlight = serializers.SerializerMethodField()
    tier_segment = serializers.SerializerMethodField()
    tier_rank = serializers.SerializerMethodField()

    def _tier_key(self, obj: AccountTier) -> str:
        return (obj.name or "").strip().lower()

    def _tier_rank(self, obj: AccountTier) -> int:
        key = self._tier_key(obj)
        if "partner pro" in key:
            return 5
        if "partner" in key:
            return 4
        if "business pro" in key:
            return 3
        if "business" in key:
            return 2
        if "pro" in key:
            return 1
        return 0

    def _tier_segment(self, obj: AccountTier) -> str:
        key = self._tier_key(obj)
        if "partner" in key:
            return "partner"
        if "business" in key:
            return "business"
        return "personal"

    def _from_features_json(self, obj: AccountTier) -> dict[str, Any] | None:
        features = obj.features_json or {}
        feature_list = features.get("feature_list") or features.get("features")
        if isinstance(feature_list, list):
            return {
                "feature_list": [str(x) for x in feature_list],
                "feature_tagline": features.get("feature_tagline"),
                "feature_badge": features.get("feature_badge"),
                "feature_highlight": features.get("feature_highlight"),
            }
        return None

    def _default_features(self, obj: AccountTier) -> dict:
        preset_features = get_tier_preset_features(obj.name)
        if isinstance(preset_features.get("feature_list"), list):
            return {
                "feature_tagline": preset_features.get("feature_tagline"),
                "feature_badge": preset_features.get("feature_badge"),
                "feature_highlight": preset_features.get("feature_highlight"),
                "feature_list": [str(item) for item in (preset_features.get("feature_list") or [])],
            }
        key = self._tier_key(obj)
        if "partner pro" in key:
            return {
                "feature_tagline": "Global partner networks & enterprise ops",
                "feature_badge": "Partner Pro",
                "feature_highlight": "Unlimited partner orgs + automation ops",
                "feature_list": [
                    "Unlimited partner organizations",
                    "Enterprise automation & API/webhooks",
                    "Dedicated revenue & giving ops hub",
                    "Advanced analytics & forecasting",
                    "Priority compliance & governance controls",
                    "Global roles & permission teams",
                    "Concierge onboarding & migration",
                ],
            }
        if "partner" in key:
            return {
                "feature_tagline": "Organizations, ministries & enterprises",
                "feature_badge": "Partner",
                "feature_highlight": "Multi-account orgs + revenue tools",
                "feature_list": [
                    "Verified organization profile",
                    "Multiple admins & roles",
                    "Live streaming + events",
                    "Donations & revenue tools",
                    "Advanced analytics dashboard",
                    "Community & group management at scale",
                    "Priority support",
                    "Partner webhooks & automations",
                ],
            }
        if "business pro" in key:
            return {
                "feature_tagline": "High-impact teams and creators",
                "feature_badge": "Most popular",
                "feature_highlight": "Advanced analytics + team workflows",
                "feature_list": [
                    "Unlimited communities & groups",
                    "Team collaboration & admin roles",
                    "Advanced insights & reporting",
                    "Campaign scheduler & post boosting",
                    "CRM-lite lead capture",
                    "Branding controls & verification",
                    "Priority moderation tools",
                    "Faster support response",
                ],
            }
        if "business" in key:
            return {
                "feature_tagline": "Teams, growth & visibility",
                "feature_highlight": "KIS Business broadcast + storefront",
                "feature_list": [
                    "KIS Business broadcast channel",
                    "Business profile + CTA buttons",
                    "Multiple admins for business page",
                    "Business insights & audience metrics",
                    "Basic catalog for services/products",
                    "Promo codes + offers",
                    "Auto-reply & business hours",
                    "Featured discovery boost",
                ],
            }
        if "pro" in key:
            return {
                "feature_tagline": "Creators and power users",
                "feature_highlight": "Enhanced profile + higher limits",
                "feature_list": [
                    "More communities & groups",
                    "Enhanced profile visibility",
                    "Higher media limits",
                    "Advanced messaging tools",
                    "Priority search ranking",
                    "Extended support",
                    "Status & story enhancements",
                    "Custom themes",
                ],
            }
        return {
            "feature_tagline": "Start free, upgrade anytime",
            "feature_highlight": "Everything you need to begin",
            "feature_list": [
                "Direct messaging",
                "Core community access",
                "Standard profile",
                "Basic storage",
                "Search & discovery",
                "Standard support",
                "Status updates",
                "Basic groups",
            ],
        }

    def get_feature_list(self, obj: AccountTier) -> list[str]:
        override = self._from_features_json(obj)
        if override and override.get("feature_list"):
            return override["feature_list"]
        return self._default_features(obj)["feature_list"]

    def get_feature_tagline(self, obj: AccountTier) -> str | None:
        override = self._from_features_json(obj)
        if override and override.get("feature_tagline"):
            return override["feature_tagline"]
        return self._default_features(obj).get("feature_tagline")

    def get_feature_badge(self, obj: AccountTier) -> str | None:
        override = self._from_features_json(obj)
        if override and override.get("feature_badge"):
            return override["feature_badge"]
        return self._default_features(obj).get("feature_badge")

    def get_feature_highlight(self, obj: AccountTier) -> str | None:
        override = self._from_features_json(obj)
        if override and override.get("feature_highlight"):
            return override["feature_highlight"]
        return self._default_features(obj).get("feature_highlight")

    def get_tier_segment(self, obj: AccountTier) -> str:
        return self._tier_segment(obj)

    def get_tier_rank(self, obj: AccountTier) -> int:
        return self._tier_rank(obj)

    class Meta:
        model = AccountTier
        fields = (
            "id",
            "name",
            "price_cents",
            "features_json",
            "feature_list",
            "feature_tagline",
            "feature_badge",
            "feature_highlight",
            "tier_segment",
            "tier_rank",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class SubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subscription
        ref_name = "AccountsSubscription"
        fields = "__all__"
        read_only_fields = ("id", "created_at", "updated_at")

    def validate(self, attrs):
        if not attrs.get("user"):
            raise serializers.ValidationError({"user": "Subscription must be associated with a user."})
        if not attrs.get("tier"):
            raise serializers.ValidationError({"tier": "Subscription must reference an AccountTier."})
        return attrs


class SessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Session
        fields = "__all__"
        read_only_fields = ("id", "created_at", "updated_at")

    def create(self, validated_data):
        if not validated_data.get("expires_at"):
            validated_data["expires_at"] = timezone.now() + datetime.timedelta(days=30)
        return super().create(validated_data)


class DeviceSessionSerializer(serializers.ModelSerializer):
    has_e2ee_keys = serializers.SerializerMethodField()
    current = serializers.SerializerMethodField()

    class Meta:
        model = Device
        fields = (
            "id",
            "device_id",
            "platform",
            "name",
            "user_agent",
            "last_ip",
            "last_seen_at",
            "revoked_at",
            "revoke_reason",
            "has_e2ee_keys",
            "current",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_has_e2ee_keys(self, obj):
        return E2EDeviceKey.objects.filter(user=obj.user, device=obj).exists()

    def get_current(self, obj):
        request = self.context.get("request")
        if not request:
            return False
        header_device_id = (
            request.headers.get("X-Device-Id")
            or request.headers.get("X-Device-ID")
            or request.headers.get("X-DeviceId")
        )
        return bool(header_device_id and str(header_device_id) == str(obj.device_id))


class E2EEDeviceBundleSerializer(serializers.Serializer):
    user_id = serializers.CharField()
    device_id = serializers.CharField()
    identity_key = serializers.CharField()
    signed_prekey = serializers.DictField()
    one_time_prekey = serializers.DictField(allow_null=True)
    registration_id = serializers.IntegerField(allow_null=True)
    last_seen_at = serializers.DateTimeField(allow_null=True)


# -------------------------------------------------------------------
# Experience / Education / Skill / Project / Recommendation serializers
# -------------------------------------------------------------------
class ExperienceSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True)
    user = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Experience
        fields = [
            "id", "user", "title", "description",
            "start_date", "end_date", "currently_working",
            "created_at", "updated_at",
        ]
        read_only_fields = ("id", "user", "created_at", "updated_at")

    def validate(self, attrs):
        if attrs.get("start_date") and attrs.get("end_date") and attrs["end_date"] < attrs["start_date"]:
            raise serializers.ValidationError({"end_date": "end_date cannot be before start_date"})
        return attrs

    def create(self, validated_data):
        if self.context.get("request"):
            validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class EducationSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True)
    user = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Education
        fields = [
            "id", "user", "school", "description",
            "start_date", "end_date", "currently_studying",
            "created_at", "updated_at",
        ]
        read_only_fields = ("id", "user", "created_at", "updated_at")

    def validate(self, attrs):
        if attrs.get("start_date") and attrs.get("end_date") and attrs["end_date"] < attrs["start_date"]:
            raise serializers.ValidationError({"end_date": "end_date cannot be before start_date"})
        return attrs

    def create(self, validated_data):
        if self.context.get("request"):
            validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class UserSkillSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True)
    user = serializers.PrimaryKeyRelatedField(read_only=True)
    endorsements = serializers.IntegerField(min_value=0, required=False)

    class Meta:
        model = UserSkill
        fields = [
            "id", "user", "skill_id", "verified",
            "endorsements", "description", "created_at", "updated_at",
        ]
        read_only_fields = ("id", "user", "created_at", "updated_at")

    def create(self, validated_data):
        if self.context.get("request"):
            validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class ProjectSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True)
    user = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Project
        fields = [
            "id", "user", "name", "description",
            "start_date", "end_date", "project_url", "technologies",
            "created_at", "updated_at",
        ]
        read_only_fields = ("id", "user", "created_at", "updated_at")

    def validate(self, attrs):
        if attrs.get("start_date") and attrs.get("end_date") and attrs["end_date"] < attrs["start_date"]:
            raise serializers.ValidationError({"end_date": "end_date cannot be before start_date"})
        return attrs

    def create(self, validated_data):
        if self.context.get("request"):
            validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class RecommendationSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True)
    recommended_user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    recommender_user = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Recommendation
        fields = [
            "id", "recommended_user", "recommender_user",
            "content", "created_at", "updated_at", "approved",
        ]
        read_only_fields = ("id", "recommender_user", "created_at", "updated_at")

    def create(self, validated_data):
        if self.context.get("request"):
            validated_data["recommender_user"] = self.context["request"].user
        return super().create(validated_data)

class LoginSerializer(serializers.Serializer):
    phone = serializers.CharField(write_only=True, required=False, allow_blank=True)
    phone_country_code = serializers.CharField(write_only=True, required=False, allow_blank=True)
    phone_number = serializers.CharField(write_only=True, required=False, allow_blank=True)
    password = serializers.CharField(write_only=True)
    country = serializers.CharField(write_only=True, default="CM", required=False)
    device_id = serializers.CharField(write_only=True, required=False, allow_blank=True)
    device_platform = serializers.CharField(write_only=True, required=False, allow_blank=True)
    device_name = serializers.CharField(write_only=True, required=False, allow_blank=True)
    otp_code = serializers.CharField(write_only=True, required=False, allow_blank=True)

    def validate(self, attrs):
        phone_raw = (attrs.get("phone") or "").strip()
        phone_country_code = _normalize_country_code(attrs.get("phone_country_code"))
        phone_number = _normalize_local_phone(attrs.get("phone_number"))
        password = attrs.get("password") or ""
        country = (attrs.get("country") or "CM").upper()

        # Prefer passing a Django HttpRequest to auth backends
        req = self.context.get("request")
        if hasattr(req, "_request"):
            req = req._request

        if not phone_raw and phone_number:
            phone_raw = _compose_phone(phone_country_code, phone_number) or phone_number

        if not phone_raw or not password:
            raise serializers.ValidationError({"detail": _("Phone and password are required.")})

        device_id = (attrs.get("device_id") or "").strip()
        if not device_id:
            req_headers = getattr(req, "headers", {}) if req else {}
            for header_key in ("X-Device-Id", "X-Device-ID", "X-DeviceId"):
                header_value = req_headers.get(header_key) or (req.META.get(header_key.replace("-", "_").upper()) if req else None)
                if header_value:
                    device_id = str(header_value).strip()
                    break
        if not device_id:
            device_id = "unknown-device"

        login_candidates = []
        seen = set()

        def add_candidate(value: str | None):
            normalized = str(value or "").strip()
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            login_candidates.append(normalized)

        add_candidate(phone_raw)
        add_candidate(phone_number)

        digits_only = ''.join(ch for ch in phone_raw if ch.isdigit())
        add_candidate(digits_only)
        if phone_country_code and phone_number:
            add_candidate(_compose_phone(phone_country_code, phone_number))

        phone_e164 = None
        try:
            phone_e164 = to_e164(phone_raw, default_region=country)
        except Exception:
            phone_e164 = None
        add_candidate(phone_e164)

        user = None
        for candidate in login_candidates:
            user = authenticate(request=req, username=candidate, password=password)
            if user is not None:
                break

        if user is None and phone_number:
            if phone_country_code:
                user = User.objects.filter(phone_number=phone_number, phone_country_code=phone_country_code).first()
            if user is None:
                user = User.objects.filter(phone_number=phone_number).order_by("id").first()
            if user and not user.check_password(password):
                user = None

        if user is None:
            raise serializers.ValidationError({"detail": _("Invalid credentials.")})
        if not user.is_active:
            raise serializers.ValidationError({"detail": _("User account is disabled.")})

        if not phone_e164:
            try:
                base_phone = user.phone or _compose_phone(user.phone_country_code, user.phone_number) or phone_raw
                phone_e164 = to_e164(base_phone, default_region=(user.country or country))
            except Exception:
                phone_e164 = user.phone or _compose_phone(user.phone_country_code, user.phone_number) or phone_raw

        attrs["user"] = user
        attrs["phone_e164"] = phone_e164
        attrs["device_id"] = device_id
        return attrs
