"""
Accounts views with JWT-based auth.

Changes:
- Register & Login now issue SimpleJWT tokens (access + refresh).
- Logout blacklists refresh (if blacklist app installed), else no-op 204.
- ViewSets authenticate via JWT (explicitly or via global settings).
"""

from typing import Optional, Iterable
import datetime
import os
import re
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.db import transaction, IntegrityError
from django.db.models import Sum, Q
from django.contrib.auth import authenticate
from django.core.cache import cache
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from apps.core.phone_utils import to_e164
from common.media_urls import absolutize_backend_media
from django.utils.translation import gettext_lazy as _
import pyotp
import phonenumbers as _phonenumbers

from rest_framework import viewsets, mixins, filters, status, serializers, permissions, generics
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAdminUser
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError as DRFValidationError
from django_filters.rest_framework import DjangoFilterBackend
from django.contrib.auth import get_user_model

from drf_spectacular.utils import (
    extend_schema, extend_schema_view, OpenApiResponse
)

# SimpleJWT
from rest_framework_simplejwt.authentication import JWTAuthentication
from .jwt_auth import (
    DeviceBoundJWTAuthentication,
    DeviceBoundJWTAuthenticationAllowPhoneLookup,
    revoke_unapproved_secondary_devices,
)
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_api_settings
from rest_framework_simplejwt.tokens import RefreshToken

from .models import (
    User,
    Profile,
    AccountTier,
    Subscription,
    Session,
    Device,
    DeviceQRToken,
    TwoFactor,
    E2EDeviceKey,
    E2EPreKey,
    UsageQuota,
    AuditLog,
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
    UserContact,
    ApiToken,
    UserConnection,
)
from .security_events import log_security_event, record_failed_auth, request_meta

from .serializers import (
    UserSerializer,
    UserCreateSerializer,
    ProfileSerializer,
    ProfileFieldVisibilitySerializer,
    ProfileArticleSerializer,
    ProfilePreferencesSerializer,
    ProfileLanguageSerializer,
    ProfileShowcaseSerializer,
    AccountTierSerializer,
    SubscriptionSerializer,
    SessionSerializer,
    DeviceSessionSerializer,
    E2EEDeviceBundleSerializer,
    ExperienceSerializer,
    EducationSerializer,
    UserSkillSerializer,
    ProjectSerializer,
    RecommendationSerializer,
    LoginSerializer,
    ApiTokenListSerializer,
)
from .feature_gate import require_feature
from .family_accessibility import serialize_family_accessibility_preferences, update_family_accessibility_preferences
from .tiers import ensure_default_account_tiers, get_user_tier_features, public_account_tiers_qs
from apps.partners.models import Partner, PartnerApplication, PartnerJobPost
from apps.partners.serializers import PartnerListSerializer, PartnerApplicationDetailSerializer, PartnerJobPostSerializer
from apps.commerce.models import LoyaltyPoint
from apps.billing.models import WalletAccount, CreditAccount
from apps.billing.services import credits_to_cents

PROFILE_FIELD_ORDER = [
    "avatar",
    "cover",
    "headline",
    "bio",
    "industry",
    "contact_phone",
    "contact_email",
    "experience",
    "education",
    "projects",
    "skills",
    "recommendations",
    "articles",
    "activity",
    "services",
    "highlights",
    "portfolio",
    "case_study",
    "testimonial",
    "certification",
    "intro_video",
]
PROFILE_FIELD_KEYS = set(PROFILE_FIELD_ORDER)
POPULAR_PROFILE_LANGUAGES = (
    "English",
    "Mandarin Chinese",
    "Hindi",
    "Spanish",
    "French",
    "German",
)
POPULAR_PROFILE_LANGUAGE_MAP = {label.lower(): label for label in POPULAR_PROFILE_LANGUAGES}
DEFAULT_PROFILE_LANGUAGE = "English"
KIS_HANDLE_PREFIX = "@kis-"


def _extract_language_label(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return _extract_language_label(
            value.get("label")
            or value.get("name")
            or value.get("language")
            or value.get("language_name")
            or value.get("value")
        )

    text = str(value or "").strip()
    if not text:
        return ""

    match = re.search(r"label\s*[:=]\s*['\"]?([^,'\"\]}]+)['\"]?", text, flags=re.IGNORECASE)
    if match:
        text = str(match.group(1) or "").strip()
    canonical = POPULAR_PROFILE_LANGUAGE_MAP.get(text.lower())
    return canonical or ""


def _normalize_language_values(values: Iterable[object] | None, ensure_default: bool = False) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        label = _extract_language_label(value)
        if not label:
            continue
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(label)

    if ensure_default and not normalized:
        normalized.append(DEFAULT_PROFILE_LANGUAGE)
    return normalized


def _strip_kis_handle_prefix(value: object | None) -> str:
    text = str(value or "").strip()
    if text.lower().startswith(KIS_HANDLE_PREFIX):
        return text[len(KIS_HANDLE_PREFIX):].strip()
    return text


def _extract_kis_handle_visible_name(value: object | None) -> str:
    text = _strip_kis_handle_prefix(value)
    if text.startswith("[") and text.endswith("]") and len(text) > 2:
        text = text[1:-1]
    text = re.sub(r"[_-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_kis_handle_key(value: object | None) -> str:
    visible = _extract_kis_handle_visible_name(value).lower()
    return re.sub(r"[^a-z0-9]+", "", visible)


def _digits_only(value: str | None) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _phone_variants(value: str | None, country_hint: str = "CM") -> set[str]:
    raw = str(value or "").strip()
    if not raw:
        return set()
    variants = {raw}
    digits = _digits_only(raw)
    if digits:
        variants.add(digits)
        variants.add(f"+{digits}")
        if digits.startswith("00") and len(digits) > 2:
            variants.add(digits[2:])
            variants.add(f"+{digits[2:]}")
    try:
        e164 = to_e164(raw, country_hint or "CM")
        variants.add(e164)
        if e164.startswith("+"):
            variants.add(e164[1:])
    except Exception:
        pass
    return {item for item in variants if item}


def _user_phone_variants(user: Optional[User]) -> set[str]:
    if not user:
        return set()
    country_hint = str(getattr(user, "country", "") or "CM").upper() or "CM"
    variants = set()
    variants.update(_phone_variants(getattr(user, "phone", None), country_hint))
    local_number = str(getattr(user, "phone_number", "") or "").strip()
    if local_number:
        variants.update(_phone_variants(local_number, country_hint))
        code = str(getattr(user, "phone_country_code", "") or "").strip()
        if code:
            variants.update(_phone_variants(f"{code}{local_number}", country_hint))
    return variants


def _allow_targets_match_viewer(viewer: Optional[User], allow_targets: Iterable[ProfileFieldVisibilityAllowTarget | ProfileArticleAllowTarget]) -> bool:
    if not viewer:
        return False
    viewer_id = str(viewer.id)
    viewer_phone_variants = _user_phone_variants(viewer)
    viewer_phone_digits = {_digits_only(item) for item in viewer_phone_variants if _digits_only(item)}
    for target in allow_targets:
        target_user_id = str(getattr(target, "target_user_id", "") or "")
        if target_user_id and target_user_id == viewer_id:
            return True
        target_phone = str(getattr(target, "target_phone", "") or "").strip()
        if target_phone and target_phone in viewer_phone_variants:
            return True
        target_phone_digits = _digits_only(getattr(target, "target_phone_number", "") or target_phone)
        if target_phone_digits and target_phone_digits in viewer_phone_digits:
            return True
    return False


def _has_contact_link(owner: User, target: User) -> bool:
    owner_phone_variants = _user_phone_variants(target)
    owner_digits = {_digits_only(item) for item in owner_phone_variants if _digits_only(item)}
    qs = UserContact.objects.filter(user=owner).filter(
        Q(contact_user=target)
        | Q(contact_phone__in=owner_phone_variants)
        | Q(contact_phone_number__in=owner_digits)
    )
    return qs.exists()


def _is_mutual_contact(owner: User, viewer: Optional[User]) -> bool:
    if not viewer:
        return False
    if owner.id == viewer.id:
        return True
    return _has_contact_link(owner, viewer) and _has_contact_link(viewer, owner)


def _can_view_visibility(
    owner: User,
    viewer: Optional[User],
    visibility: str,
    allow_targets: Iterable[ProfileFieldVisibilityAllowTarget | ProfileArticleAllowTarget],
) -> bool:
    viewer_id = str(getattr(viewer, "id", ""))
    owner_id = str(owner.id)
    if viewer_id and viewer_id == owner_id:
        return True
    normalized = str(visibility or "public").strip().lower()
    if normalized == "public":
        return True
    if normalized == "private":
        return False
    if normalized == "contacts":
        return _is_mutual_contact(owner, viewer)
    if normalized == "custom":
        return _allow_targets_match_viewer(viewer, allow_targets)
    return False


def _can_view_field(owner: User, viewer: Optional[User], rule: Optional[ProfileFieldVisibility]) -> bool:
    if not rule:
        return True
    return _can_view_visibility(owner, viewer, rule.visibility, rule.allow_targets.all())


def _resolve_media_url(request, file_field, fallback_url):
    if file_field:
        url = file_field.url
        return absolutize_backend_media(url, request=request)
    return absolutize_backend_media(fallback_url, request=request) or None


def _build_profile_payload(profile: Profile, viewer: Optional[User], request=None) -> dict:
    owner = profile.user
    from apps.verification.constants import VerificationSubjectType
    from apps.verification.services import verification_summary

    owner_verification_summary = verification_summary(VerificationSubjectType.USER, owner.id)
    owner_id_str = str(owner.id)
    viewer_id = str(getattr(viewer, "id", "")) if viewer else ""
    rules = {
        item.field_key: item
        for item in ProfileFieldVisibility.objects.filter(
            user=owner,
            field_key__in=PROFILE_FIELD_KEYS,
        ).prefetch_related("allow_targets")
    }

    def can_view(key: str) -> bool:
        return _can_view_field(owner, viewer, rules.get(key))

    def maybe(value, key: str):
        return value if can_view(key) else None

    def can_view_showcase(entry: dict) -> bool:
        if viewer_id and viewer_id == owner_id_str:
            return True
        visibility = (entry.get("visibility") or "public").lower()
        if visibility == "public":
            return True
        if visibility == "private":
            return False
        if visibility == "contacts":
            return _is_mutual_contact(owner, viewer)
        if visibility == "custom":
            allow_values = [str(v).strip() for v in (entry.get("allow_user_ids") or []) if str(v).strip()]
            if not viewer:
                return False
            viewer_identifier = str(viewer.id)
            viewer_phone_variants = _user_phone_variants(viewer)
            viewer_phone_digits = {_digits_only(item) for item in viewer_phone_variants if _digits_only(item)}
            for value in allow_values:
                if value == viewer_identifier:
                    return True
                if value in viewer_phone_variants:
                    return True
                value_digits = _digits_only(value)
                if value_digits and value_digits in viewer_phone_digits:
                    return True
            return False
        return False

    experiences = []
    if can_view("experience"):
        experiences = ExperienceSerializer(Experience.objects.filter(user=owner), many=True).data

    educations = []
    if can_view("education"):
        educations = EducationSerializer(Education.objects.filter(user=owner), many=True).data

    skills = []
    if can_view("skills"):
        skills = UserSkillSerializer(UserSkill.objects.filter(user=owner), many=True).data

    projects = []
    if can_view("projects"):
        projects = ProjectSerializer(Project.objects.filter(user=owner), many=True).data

    recommendations = []
    if can_view("recommendations"):
        rec_qs = Recommendation.objects.filter(recommended_user=owner, approved=True)
        recommendations = RecommendationSerializer(rec_qs, many=True).data

    articles = []
    if can_view("articles"):
        article_qs = ProfileArticle.objects.filter(user=owner).prefetch_related("allow_targets")
        if not viewer or viewer != owner:
            article_qs = article_qs.filter(status="published")
        allowed_articles = [
            article
            for article in article_qs
            if _can_view_visibility(owner, viewer, article.visibility, article.allow_targets.all())
        ]
        articles = ProfileArticleSerializer(allowed_articles, many=True).data

    activity = []
    if can_view("activity"):
        activity_qs = AuditLog.objects.filter(actor_id=owner.id).order_by("-created_at")[:25]
        activity = [
            {
                "id": str(item.id),
                "action": item.action,
                "meta": item.meta,
                "created_at": item.created_at,
            }
            for item in activity_qs
        ]

    subscription = Subscription.objects.filter(user=owner, status="active").select_related("tier").first()
    tier = subscription.tier if subscription and subscription.tier else AccountTier.objects.filter(name__iexact=owner.tier).first()
    wallet = WalletAccount.objects.filter(user=owner).first()
    wallet_balance_cents = getattr(wallet, "balance_cents", 0)
    credit_account = CreditAccount.objects.filter(user=owner).first()
    credits_balance = getattr(credit_account, "credits", 0)
    credits_value_cents = credits_to_cents(credits_balance)
    points_total = LoyaltyPoint.objects.filter(user=owner).aggregate(total=Sum("points")).get("total") or 0

    preferences = ProfilePreferences.objects.filter(user=owner).first()
    language_rows = _normalize_language_values(
        ProfileLanguage.objects.filter(user=owner).order_by("created_at").values_list("name", flat=True)
    )
    preferences_data = None
    if preferences:
        preferences_data = ProfilePreferencesSerializer(preferences).data
    elif language_rows:
        preferences_data = {
            "services": [],
            "availability": {},
            "skill_badges": [],
            "languages": [],
            "location": {},
            "compensation": {},
            "social_proof": {},
            "ask_tags": [],
            "highlights": [],
        }

    if preferences_data is not None:
        if language_rows:
            preferences_data["languages"] = language_rows
        else:
            preferences_data["languages"] = _normalize_language_values(
                preferences_data.get("languages") or [],
                ensure_default=True,
            )
        if not can_view("services"):
            preferences_data["services"] = []
        if not can_view("highlights"):
            preferences_data["highlights"] = []

    showcases = ProfileShowcase.objects.filter(user=owner).order_by("-created_at")
    showcases_data = ProfileShowcaseSerializer(showcases, many=True, context={"request": request}).data
    filtered_showcases = [
        item
        for item in showcases_data
        if can_view(item.get("type", "")) and can_view_showcase(item)
    ]

    grouped_showcases = {}
    for item in filtered_showcases:
        grouped_showcases.setdefault(item["type"], []).append(item)

    privacy_summary = []
    for key in PROFILE_FIELD_ORDER:
        rule = rules.get(key)
        privacy_summary.append(
            {
                "field_key": key,
                "visibility": rule.visibility if rule else "public",
                "visible": can_view(key),
            }
        )

    account_payload = {
        "tier": AccountTierSerializer(tier).data if tier else None,
        "subscription": SubscriptionSerializer(subscription).data if subscription else None,
        "wallet_balance_cents": wallet_balance_cents,
        "credits": credits_balance,
        "credits_value_cents": credits_value_cents,
        "points": points_total,
    }

    if viewer and viewer != owner:
        account_payload = {
            "tier": AccountTierSerializer(tier).data if tier else None,
            "subscription": SubscriptionSerializer(subscription).data if subscription else None,
        }

    is_self = bool(viewer and str(viewer.id) == owner_id_str)
    return {
        "user": {
            "id": owner.id,
            "display_name": owner.display_name,
            "verification_summary": owner_verification_summary,
            "avatar_url": maybe(
                _resolve_media_url(request, profile.avatar_file, profile.avatar_url),
                "avatar",
            ),
            "phone": maybe(owner.phone, "contact_phone"),
            "phone_country_code": maybe(owner.phone_country_code, "contact_phone"),
            "phone_number": maybe(owner.phone_number, "contact_phone"),
            "email": maybe(owner.email, "contact_email"),
            # Only expose privilege flags to the authenticated user viewing their own profile
            **({"is_superuser": owner.is_superuser, "is_staff": owner.is_staff} if is_self else {}),
        },
        "profile": {
            "id": profile.id,
            "avatar_url": maybe(
                _resolve_media_url(request, profile.avatar_file, profile.avatar_url),
                "avatar",
            ),
            "cover_url": maybe(
                _resolve_media_url(request, profile.cover_file, profile.cover_url),
                "cover",
            ),
            "headline": maybe(profile.headline, "headline"),
            "bio": maybe(profile.bio, "bio"),
            "industry": maybe(profile.industry, "industry"),
            "completion_score": profile.completion_score,
            "visibility": profile.visibility,
            "branding_prefs": profile.branding_prefs,
            "created_at": profile.created_at,
            "updated_at": profile.updated_at,
            "verification_summary": owner_verification_summary,
        },
        "sections": {
            "experiences": experiences,
            "educations": educations,
            "skills": skills,
            "projects": projects,
            "recommendations": recommendations,
            "articles": articles,
            "activity": activity,
            "showcases": grouped_showcases,
        },
        "stats": {
            "experiences": len(experiences),
            "educations": len(educations),
            "skills": len(skills),
            "projects": len(projects),
            "recommendations": len(recommendations),
            "articles": len(articles),
        },
        "preferences": preferences_data,
        "account": account_payload,
        "privacy_summary": privacy_summary,
    }


def _normalize_partner_limit(limit_value):
    if isinstance(limit_value, (int, float)):
        allowed = max(int(limit_value), 0)
        return {"allowed": allowed, "label": str(allowed), "is_unlimited": False}
    if limit_value is None:
        return {"allowed": 0, "label": None, "is_unlimited": False}
    normalized = str(limit_value).strip()
    if not normalized:
        return {"allowed": 0, "label": None, "is_unlimited": False}
    lowered = normalized.lower()
    if lowered in {"unlimited", "infinite", "∞"}:
        return {"allowed": None, "label": normalized, "is_unlimited": True}
    if lowered.isdigit():
        allowed = max(int(lowered), 0)
        return {"allowed": allowed, "label": normalized, "is_unlimited": False}
    if limit_value is True:
        return {"allowed": None, "label": normalized, "is_unlimited": True}
    return {"allowed": None, "label": normalized, "is_unlimited": True}


def _partner_profile_summary(user: User, request) -> dict:
    partners = list(Partner.objects.filter(owner=user).select_related("main_conversation"))
    serializer = PartnerListSerializer(partners, many=True, context={"request": request})
    features = get_user_tier_features(user)
    limit_info = _normalize_partner_limit(features.get("partner_accounts"))
    count = len(partners)
    can_create = limit_info["is_unlimited"] or bool(limit_info["allowed"] and count < limit_info["allowed"])
    return {
        "partner_profiles": serializer.data,
        "partner_profiles_count": count,
        "partner_profiles_limit_value": limit_info["allowed"],
        "partner_profiles_limit_label": limit_info["label"],
        "partner_profiles_is_unlimited": limit_info["is_unlimited"],
        "partner_profiles_can_create": can_create,
    }

# -----------------------------
# Permissions
# -----------------------------
class IsOwnerOrReadOnly(permissions.BasePermission):
    """Allow full access to owners, read-only to others."""
    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        owner = getattr(obj, "user", None) or getattr(obj, "recommender_user", None) or getattr(obj, "owner", None)
        if owner is None:
            return False
        return owner == request.user

# -----------------------------
# JWT helpers
# -----------------------------
class JWTTokensSerializer(serializers.Serializer):
    access = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True)
    token_type = serializers.CharField(default="Bearer", read_only=True)

def issue_tokens_for_user(user: User, device_id: Optional[str] = None) -> dict:
    refresh = RefreshToken.for_user(user)
    if device_id:
        refresh["device_id"] = device_id
        device = Device.objects.filter(user=user, device_id=str(device_id), revoked_at__isnull=True).first()
        if device:
            refresh["token_version"] = device.token_version
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "token_type": "Bearer",
    }


def request_device_id(request) -> str:
    return (
        request.headers.get("X-Device-Id")
        or request.headers.get("X-Device-ID")
        or request.headers.get("X-DeviceId")
        or ""
    )

def password_login_requires_qr(user: User, device_id: str) -> bool:
    """
    Password credentials may only resume an already-active device session.
    Any new device for an account with an active device must be linked by QR
    from the parent device under Profile -> Manage Devices.
    """
    normalized_device_id = str(device_id or "").strip()
    if not normalized_device_id:
        return True

    active_devices = Device.objects.filter(user=user, revoked_at__isnull=True)
    if not active_devices.exists():
        return False

    return not active_devices.filter(device_id=normalized_device_id).exists()


def upsert_device(
    user: User,
    device_id: str,
    platform: Optional[str],
    name: Optional[str],
    request,
) -> Device:
    existing = Device.objects.filter(user=user, device_id=str(device_id)).first()
    token_version = (existing.token_version + 1) if existing and existing.revoked_at else (existing.token_version if existing else 1)

    # Determine whether this device should become the parent device.
    # A device is the parent if it's the very first active (non-revoked) device for this user.
    has_active_parent = Device.objects.filter(
        user=user, is_parent=True, revoked_at__isnull=True
    ).exclude(device_id=str(device_id)).exists()

    defaults = {
        "platform": platform or "unknown",
        "name": name or None,
        "last_seen_at": timezone.now(),
        "last_ip": request.META.get("REMOTE_ADDR") if request else None,
        "user_agent": request.META.get("HTTP_USER_AGENT") if request else None,
        "token_version": token_version,
        "revoked_at": None,
        "revoke_reason": "",
    }
    if not has_active_parent:
        # No existing parent — promote this device.
        defaults["is_parent"] = True

    device, _ = Device.objects.update_or_create(
        user=user,
        device_id=str(device_id),
        defaults=defaults,
    )
    return device


def revoke_device_session(user: User, device: Device, *, reason: str, request=None) -> Device:
    device.token_version = int(device.token_version or 1) + 1
    device.revoked_at = timezone.now()
    device.revoke_reason = reason[:120]
    device.save(update_fields=["token_version", "revoked_at", "revoke_reason", "updated_at"])
    E2EDeviceKey.objects.filter(user=user, device=device).delete()
    E2EPreKey.objects.filter(user=user, device=device).delete()
    log_security_event(
        user,
        "security.device.revoked",
        request=request,
        severity="warning",
        device_id=device.device_id,
        reason=reason,
    )
    return device


def get_or_create_totp(user: User) -> TwoFactor:
    tf, _ = TwoFactor.objects.get_or_create(
        user=user,
        type="totp",
        defaults={"enabled": False, "meta": {}},
    )
    meta = tf.meta or {}
    if not meta.get("secret"):
        meta["secret"] = pyotp.random_base32()
        meta["verified"] = False
        tf.meta = meta
        tf.save(update_fields=["meta", "updated_at"])
    return tf


def verify_totp(user: User, code: str) -> bool:
    tf = TwoFactor.objects.filter(user=user, type="totp", enabled=True).first()
    if not tf:
        return True
    secret = (tf.meta or {}).get("secret")
    if not secret:
        return False
    totp = pyotp.TOTP(secret)
    return bool(code) and totp.verify(code, valid_window=1)

# -----------------------------
# Auth endpoints: Register/Login/Logout (JWT)
# -----------------------------
@extend_schema_view(
    create=extend_schema(
        summary="Register a new account (returns JWT)",
        description="Create user and return access/refresh JWT tokens plus user payload.",
        request=UserCreateSerializer,
        responses={201: OpenApiResponse(response=UserSerializer)},
        tags=["Auth", "Users"],
    )
)
class RegisterView(mixins.CreateModelMixin, viewsets.GenericViewSet):
    queryset = User.objects.all()
    serializer_class = UserCreateSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "register"

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        device_id = (request.data.get("device_id") or "").strip()
        device_platform = (request.data.get("device_platform") or "").strip()
        device_name = (request.data.get("device_name") or "").strip()
        if not device_id:
            return Response({"detail": "Device id is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                user = serializer.save()

                # Ensure a starting quota; avoids duplication with serializer (serializer no longer creates it)
                UsageQuota.objects.get_or_create(
                    user=user,
                    defaults={"quotas_json": {}, "last_reset_at": timezone.now()},
                )

                AuditLog.log(actor=user, action="user.register", meta={"phone": user.phone, "country": user.country})
        except DRFValidationError:
            # Already well-formed for client
            raise
        except IntegrityError:
            # Fallback if something unique trips at DB-level unexpectedly
            raise DRFValidationError({"detail": "Duplicate or invalid data."})

        upsert_device(user, device_id, device_platform or None, device_name or None, request)
        # Send welcome email (non-blocking)
        try:
            if getattr(user, "email", None):
                from apps.notifications.email_service import send_welcome_email
                send_welcome_email(to_email=user.email)
        except Exception:
            pass

        # Do NOT issue tokens yet — the account must be phone-verified first.
        # Tokens are issued by OtpVerifyView after successful code verification.
        phone_verified = bool(
            (user.verification or {}).get("phone", {}).get("verified")
        )
        return Response(
            {
                "success": True,
                "pending_verification": not phone_verified,
                "phone_verified": phone_verified,
                "user": {
                    "id": user.id,
                    "phone": getattr(user, "phone", None),
                    "status": getattr(user, "status", "pending"),
                    "is_active": user.is_active,
                    "phone_verified": phone_verified,
                },
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    summary="Login (email + password) -> returns JWT",
    request=LoginSerializer,
    responses={200: JWTTokensSerializer},
    tags=["Auth"]
)
class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        try:
            serializer.is_valid(raise_exception=True)
        except DRFValidationError as exc:
            login_identifier = None
            if hasattr(request, "data"):
                login_identifier = (
                    request.data.get("phone")
                    or request.data.get("phone_number")
                    or request.data.get("email")
                )
            record_failed_auth(request, identifier=login_identifier)
            # DRF wraps each dict value in a list — flatten single-item lists so
            # mobile clients can do `error_code === "phone_not_verified"` directly.
            raw = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
            flat: dict = {}
            for k, v in raw.items():
                if isinstance(v, list) and len(v) == 1:
                    flat[k] = str(v[0])
                elif isinstance(v, list):
                    flat[k] = [str(i) for i in v]
                else:
                    flat[k] = str(v) if v is not None else v
            return Response(flat, status=exc.status_code)
        user = serializer.validated_data["user"]
        otp_code = (serializer.validated_data.get("otp_code") or "").strip()
        if TwoFactor.objects.filter(user=user, type="totp", enabled=True).exists():
            if not otp_code:
                log_security_event(
                    user,
                    "security.auth.otp_required",
                    request=request,
                    severity="info",
                    device_id=serializer.validated_data.get("device_id"),
                )
                return Response(
                    {"detail": "OTP required", "two_factor_required": True},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            if not verify_totp(user, otp_code):
                record_failed_auth(
                    request,
                    identifier=user.phone or user.email,
                    reason="invalid_totp",
                )
                return Response(
                    {"detail": "Invalid OTP", "two_factor_required": True},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
        device_id = serializer.validated_data.get("device_id")
        device_platform = serializer.validated_data.get("device_platform") or None
        device_name = serializer.validated_data.get("device_name") or None
        revoke_unapproved_secondary_devices(user)
        if password_login_requires_qr(user, device_id):
            log_security_event(
                user,
                "security.auth.secondary_device_qr_required",
                request=request,
                severity="warning",
                device_id=device_id,
                device_platform=device_platform,
            )
            return Response(
                {
                    "detail": "This account is already active on another device. Use the primary device to link this device by QR code.",
                    "error_code": "secondary_device_qr_required",
                    "secondary_device_required": True,
                    "qr_login_required": True,
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        upsert_device(user, device_id, device_platform, device_name, request)
        tokens = issue_tokens_for_user(user, device_id=device_id)  # should return {access, refresh} or similar

        log_security_event(
            user,
            "security.auth.login_success",
            request=request,
            device_id=device_id,
            device_platform=device_platform,
        )
        AuditLog.log(actor=user, action="user.login", meta=request_meta(request, device_id=device_id))

        return Response(
            {
                "access": tokens.get("access"),
                "refresh": tokens.get("refresh"),
                "user": {
                    "id": user.id,
                    "phone": serializer.validated_data["phone_e164"],
                    "phone_country_code": getattr(user, "phone_country_code", None),
                    "phone_number": getattr(user, "phone_number", None),
                    "status": getattr(user, "status", "active"),
                    "is_active": user.is_active,
                    "device_id": device_id,
                    "two_factor_enabled": TwoFactor.objects.filter(user=user, type="totp", enabled=True).exists(),
                },
            },
            status=status.HTTP_200_OK,
        )
    

class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(required=False)


class TwoFactorCodeSerializer(serializers.Serializer):
    code = serializers.CharField(write_only=True)


class SignedPreKeySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    key = serializers.CharField()
    signature = serializers.CharField()


class PreKeySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    key = serializers.CharField()


class E2EERegisterSerializer(serializers.Serializer):
    device_id = serializers.CharField()
    identity_key = serializers.CharField()
    signed_prekey = SignedPreKeySerializer()
    prekeys = PreKeySerializer(many=True, required=False)
    registration_id = serializers.IntegerField(required=False)

@extend_schema(
    summary="Logout (JWT)",
    description=(
        "If token blacklist is enabled, pass a refresh token to revoke it. "
        "Otherwise this endpoint simply returns 204 and clients should discard tokens."
    ),
    request=LogoutSerializer,
    tags=["Auth"],
)
class LogoutView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        data = LogoutSerializer(data=request.data or {})
        data.is_valid(raise_exception=False)
        refresh = data.validated_data.get("refresh")
        if refresh:
            try:
                token = RefreshToken(refresh)
                # Blacklist only works if 'rest_framework_simplejwt.token_blacklist' is installed
                token.blacklist()  # will no-op / raise if blacklist not configured
            except Exception:
                pass
        current_device_id = request_device_id(request)
        if current_device_id:
            device = Device.objects.filter(user=request.user, device_id=str(current_device_id), revoked_at__isnull=True).first()
            if device:
                revoke_device_session(request.user, device, reason="logout", request=request)
        AuditLog.log(actor=request.user, action="user.logout", meta=request_meta(request, device_id=current_device_id))
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    summary="Refresh JWT with device-session checks",
    request=serializers.Serializer,
    responses={200: JWTTokensSerializer},
    tags=["Auth"],
)
class DeviceBoundTokenRefreshView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "login"

    def post(self, request):
        refresh_value = (request.data or {}).get("refresh")
        if not refresh_value:
            return Response({"detail": "Refresh token is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            refresh = RefreshToken(refresh_value)
        except TokenError:
            record_failed_auth(request, reason="invalid_refresh")
            return Response({"detail": "Token is invalid or expired."}, status=status.HTTP_401_UNAUTHORIZED)

        user_id = refresh.get(jwt_api_settings.USER_ID_CLAIM)
        device_id = refresh.get("device_id")
        token_version = refresh.get("token_version")
        try:
            user = User.objects.get(**{jwt_api_settings.USER_ID_FIELD: user_id})
        except User.DoesNotExist:
            record_failed_auth(request, reason="refresh_user_missing")
            return Response({"detail": "Token is invalid or expired."}, status=status.HTTP_401_UNAUTHORIZED)

        device = Device.objects.filter(user=user, device_id=str(device_id or "")).first()
        if not device or device.revoked_at:
            record_failed_auth(request, identifier=user.phone or user.email, reason="refresh_device_revoked")
            return Response({"detail": "Device session revoked."}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            token_version_matches = int(token_version) == int(device.token_version)
        except (TypeError, ValueError):
            token_version_matches = False
        if not token_version_matches:
            record_failed_auth(request, identifier=user.phone or user.email, reason="refresh_token_version_mismatch")
            return Response({"detail": "Device session expired."}, status=status.HTTP_401_UNAUTHORIZED)

        Device.objects.filter(pk=device.pk).update(last_seen_at=timezone.now())
        access = refresh.access_token
        log_security_event(
            user,
            "security.auth.refresh_success",
            request=request,
            device_id=device.device_id,
        )
        return Response({"access": str(access), "refresh": str(refresh), "token_type": "Bearer"}, status=status.HTTP_200_OK)


@extend_schema(
    summary="Start TOTP setup",
    description="Create or return a TOTP secret and provisioning URI.",
    tags=["Auth"],
)
class TwoFactorSetupView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        tf = get_or_create_totp(request.user)
        if tf.enabled:
            return Response({"enabled": True}, status=status.HTTP_200_OK)

        secret = (tf.meta or {}).get("secret")
        issuer = os.environ.get("TOTP_ISSUER", "KIS")
        label = request.user.phone or request.user.email or str(request.user.id)
        uri = pyotp.totp.TOTP(secret).provisioning_uri(name=label, issuer_name=issuer)

        return Response(
            {"enabled": False, "secret": secret, "provisioning_uri": uri},
            status=status.HTTP_200_OK,
        )


@extend_schema(
    summary="Enable TOTP",
    request=TwoFactorCodeSerializer,
    tags=["Auth"],
)
class TwoFactorEnableView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        data = TwoFactorCodeSerializer(data=request.data or {})
        data.is_valid(raise_exception=True)
        code = data.validated_data["code"]

        tf = get_or_create_totp(request.user)
        secret = (tf.meta or {}).get("secret")
        if not secret:
            return Response({"detail": "Missing TOTP secret"}, status=status.HTTP_400_BAD_REQUEST)

        totp = pyotp.TOTP(secret)
        if not totp.verify(code, valid_window=1):
            return Response({"detail": "Invalid OTP"}, status=status.HTTP_400_BAD_REQUEST)

        tf.enabled = True
        meta = tf.meta or {}
        meta["verified"] = True
        meta["enabled_at"] = timezone.now().isoformat()
        tf.meta = meta
        tf.save(update_fields=["enabled", "meta", "updated_at"])
        AuditLog.log(actor=request.user, action="2fa.enabled", meta={"type": "totp"})
        return Response({"enabled": True}, status=status.HTTP_200_OK)


@extend_schema(
    summary="Disable TOTP",
    request=TwoFactorCodeSerializer,
    tags=["Auth"],
)
class TwoFactorDisableView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        data = TwoFactorCodeSerializer(data=request.data or {})
        data.is_valid(raise_exception=True)
        code = data.validated_data["code"]

        tf = TwoFactor.objects.filter(user=request.user, type="totp").first()
        if not tf or not tf.enabled:
            return Response({"enabled": False}, status=status.HTTP_200_OK)

        secret = (tf.meta or {}).get("secret")
        if not secret:
            return Response({"detail": "Missing TOTP secret"}, status=status.HTTP_400_BAD_REQUEST)

        totp = pyotp.TOTP(secret)
        if not totp.verify(code, valid_window=1):
            return Response({"detail": "Invalid OTP"}, status=status.HTTP_400_BAD_REQUEST)

        tf.enabled = False
        tf.meta = {"disabled_at": timezone.now().isoformat()}
        tf.save(update_fields=["enabled", "meta", "updated_at"])
        AuditLog.log(actor=request.user, action="2fa.disabled", meta={"type": "totp"})
        return Response({"enabled": False}, status=status.HTTP_200_OK)


@extend_schema(summary="Get 2FA status for the authenticated user", tags=["Auth"])
class TwoFactorStatusView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        tf = TwoFactor.objects.filter(user=request.user, type="totp").first()
        return Response({"enabled": bool(tf and tf.enabled)}, status=status.HTTP_200_OK)


@extend_schema(summary="Get OTP status for the authenticated user", tags=["Auth"])
class OTPStatusView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        from apps.otp.models import PhoneOTP
        phone = getattr(request.user, "phone", None)
        has_pending = False
        if phone:
            recent = PhoneOTP.objects.filter(phone=phone).order_by("-created_at").first()
            has_pending = bool(recent and recent.expires_at > timezone.now())
        return Response({"has_pending": has_pending}, status=status.HTTP_200_OK)


@extend_schema(
    summary="Register E2EE keys for this device",
    request=E2EERegisterSerializer,
    tags=["Auth"],
)
class E2EERegisterKeysView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        data = E2EERegisterSerializer(data=request.data or {})
        data.is_valid(raise_exception=True)

        device_id = data.validated_data["device_id"]
        header_device_id = (
            request.headers.get("X-Device-Id")
            or request.headers.get("X-Device-ID")
            or request.headers.get("X-DeviceId")
        )
        if header_device_id and str(header_device_id) != str(device_id):
            return Response({"detail": "Device mismatch"}, status=status.HTTP_400_BAD_REQUEST)

        signed = data.validated_data["signed_prekey"]
        registration_id = data.validated_data.get("registration_id")
        prekeys = data.validated_data.get("prekeys") or []

        with transaction.atomic():
            device, _ = Device.objects.get_or_create(
                user=request.user,
                device_id=str(device_id),
                defaults={
                    "platform": "unknown",
                    "last_seen_at": timezone.now(),
                },
            )
            # Serialize all key replacement work for this device. This prevents
            # concurrent app startup calls from creating duplicate bundles.
            device = Device.objects.select_for_update().get(pk=device.pk)

            device_keys = E2EDeviceKey.objects.filter(
                user=request.user,
                device=device,
            ).order_by("-updated_at", "-created_at", "-id")
            key_record = device_keys.first()
            if key_record:
                device_keys.exclude(pk=key_record.pk).delete()
                key_record.identity_key = data.validated_data["identity_key"]
                key_record.signed_prekey_id = signed["id"]
                key_record.signed_prekey = signed["key"]
                key_record.signed_prekey_signature = signed["signature"]
                key_record.registration_id = registration_id
                key_record.save(
                    update_fields=[
                        "identity_key",
                        "signed_prekey_id",
                        "signed_prekey",
                        "signed_prekey_signature",
                        "registration_id",
                        "updated_at",
                    ]
                )
            else:
                E2EDeviceKey.objects.create(
                    user=request.user,
                    device=device,
                    identity_key=data.validated_data["identity_key"],
                    signed_prekey_id=signed["id"],
                    signed_prekey=signed["key"],
                    signed_prekey_signature=signed["signature"],
                    registration_id=registration_id,
                )

            if prekeys:
                E2EPreKey.objects.filter(user=request.user, device=device).delete()
                E2EPreKey.objects.bulk_create(
                    [
                        E2EPreKey(
                            user=request.user,
                            device=device,
                            prekey_id=item["id"],
                            prekey=item["key"],
                        )
                        for item in prekeys
                    ]
                )

        AuditLog.log(actor=request.user, action="e2ee.keys.register", meta={"device_id": device_id})
        return Response({"ok": True}, status=status.HTTP_200_OK)


@extend_schema(
    summary="Fetch E2EE bundle for a user/device",
    tags=["Auth"],
)
class E2EEFetchBundleView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (IsAuthenticated,)

    def get(self, request, user_id: str):
        target = get_object_or_404(User, id=user_id)
        device_id = request.query_params.get("device_id")

        device_qs = Device.objects.filter(user=target, revoked_at__isnull=True)
        if device_id:
            device_qs = device_qs.filter(device_id=str(device_id))
        device = device_qs.order_by("-last_seen_at").first()
        if not device:
            return Response({"detail": "No device keys"}, status=status.HTTP_404_NOT_FOUND)

        key = E2EDeviceKey.objects.filter(user=target, device=device).first()
        if not key:
            return Response({"detail": "No keys registered"}, status=status.HTTP_404_NOT_FOUND)

        prekey = None
        with transaction.atomic():
            candidate = (
                E2EPreKey.objects.select_for_update()
                .filter(user=target, device=device, consumed_at__isnull=True)
                .order_by("created_at")
                .first()
            )
            if candidate:
                candidate.consumed_at = timezone.now()
                candidate.save(update_fields=["consumed_at", "updated_at"])
                prekey = {"id": candidate.prekey_id, "key": candidate.prekey}

        return Response(
            {
                "user_id": str(target.id),
                "device_id": device.device_id,
                "identity_key": key.identity_key,
                "signed_prekey": {
                    "id": key.signed_prekey_id,
                    "key": key.signed_prekey,
                    "signature": key.signed_prekey_signature,
                },
                "one_time_prekey": prekey,
                "registration_id": key.registration_id,
            },
            status=status.HTTP_200_OK,
        )


def _serialize_e2ee_bundle_for_device(target: User, device: Device) -> Optional[dict]:
    if getattr(device, "revoked_at", None):
        return None
    key = E2EDeviceKey.objects.filter(user=target, device=device).first()
    if not key:
        return None

    prekey = None
    with transaction.atomic():
        candidate = (
            E2EPreKey.objects.select_for_update()
            .filter(user=target, device=device, consumed_at__isnull=True)
            .order_by("created_at")
            .first()
        )
        if candidate:
            candidate.consumed_at = timezone.now()
            candidate.save(update_fields=["consumed_at", "updated_at"])
            prekey = {"id": candidate.prekey_id, "key": candidate.prekey}

    return {
        "user_id": str(target.id),
        "device_id": device.device_id,
        "identity_key": key.identity_key,
        "signed_prekey": {
            "id": key.signed_prekey_id,
            "key": key.signed_prekey,
            "signature": key.signed_prekey_signature,
        },
        "one_time_prekey": prekey,
        "registration_id": key.registration_id,
        "last_seen_at": device.last_seen_at,
    }


@extend_schema(
    summary="Fetch E2EE bundles for all active devices of a user",
    responses={200: E2EEDeviceBundleSerializer(many=True)},
    tags=["Auth"],
)
class E2EEFetchDeviceBundlesView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (IsAuthenticated,)

    def get(self, request, user_id: str):
        target = get_object_or_404(User, id=user_id)
        devices = Device.objects.filter(user=target, revoked_at__isnull=True).order_by("-last_seen_at")
        bundles = []
        for device in devices:
            bundle = _serialize_e2ee_bundle_for_device(target, device)
            if bundle:
                bundles.append(bundle)

        if not bundles:
            return Response({"detail": "No device keys"}, status=status.HTTP_404_NOT_FOUND)

        serializer = E2EEDeviceBundleSerializer(bundles, many=True)
        return Response({"user_id": str(target.id), "devices": serializer.data}, status=status.HTTP_200_OK)


@extend_schema(
    summary="List active devices for the authenticated user",
    responses={200: DeviceSessionSerializer(many=True)},
    tags=["Auth"],
)
class DeviceSessionsView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        devices = (
            Device.objects.filter(user=request.user, revoked_at__isnull=True)
            .order_by("-last_seen_at", "-created_at")
        )
        serializer = DeviceSessionSerializer(devices, many=True, context={"request": request})
        return Response({"devices": serializer.data}, status=status.HTTP_200_OK)


@extend_schema(
    summary="Revoke one device for the authenticated user",
    tags=["Auth"],
)
class DeviceSessionDetailView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (IsAuthenticated,)

    def delete(self, request, device_id: str):
        current_device_id = request_device_id(request)
        if str(device_id) == str(current_device_id):
            return Response({"detail": "Use logout to end the current device session."}, status=status.HTTP_400_BAD_REQUEST)

        device = get_object_or_404(Device, user=request.user, device_id=str(device_id), revoked_at__isnull=True)
        revoke_device_session(request.user, device, reason="user_device_revoke", request=request)
        AuditLog.log(actor=request.user, action="device.revoked", meta=request_meta(request, device_id=device_id))
        return Response(status=status.HTTP_204_NO_CONTENT)

# -----------------------------
# Core viewsets with Swagger docs (JWT-protected)
# -----------------------------
# Option A (explicit): set JWTAuthentication on each viewset
JWT_AUTH = (DeviceBoundJWTAuthentication,)
IS_AUTH_OR_RO = (permissions.IsAuthenticatedOrReadOnly,)

@extend_schema_view(
    list=extend_schema(summary="List users"),
    retrieve=extend_schema(summary="Retrieve user"),
    me=extend_schema(summary="Get current authenticated user"),
    recalc_trust=extend_schema(summary="Recalculate user's trust score"),
    resolve_handle=extend_schema(summary="Resolve @KIS handle to user"),
)
class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.select_related("profile").all()
    serializer_class = UserSerializer
    authentication_classes = JWT_AUTH
    permission_classes = IS_AUTH_OR_RO
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["tier", "status"]
    search_fields = ["email", "display_name", "username"]
    ordering_fields = ["created_at", "trust_score"]

    @action(
        detail=False,
        methods=["get"],
        permission_classes=[AllowAny],
        authentication_classes=(DeviceBoundJWTAuthenticationAllowPhoneLookup,),
    )
    def me(self, request):
        phone = (request.query_params.get("phone") or "").strip()
        if not request.user.is_authenticated and phone:
            # Phone-based lookup is only permitted when the caller supplied an
            # Authorization header (even if the token was stale or expired).
            # This prevents anonymous enumeration of whether a phone is registered.
            has_auth_attempt = bool(
                request.headers.get("Authorization")
                or request.headers.get("authorization")
            )
            if not has_auth_attempt:
                return Response(
                    {"detail": "Authentication credentials were not provided."},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            country_hint = str(request.query_params.get("country") or "CM").strip().upper() or "CM"
            candidates = _phone_variants(phone, country_hint)
            digit_candidates = [_digits_only(value) for value in candidates]
            digit_candidates = [value for value in digit_candidates if value]
            candidate = (
                User.objects.select_related("profile")
                .filter(Q(phone__in=candidates) | Q(phone_number__in=digit_candidates))
                .order_by("id")
                .first()
            )
            if not candidate:
                return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
            serializer = self.get_serializer(candidate)
            return Response(serializer.data)
        if not request.user.is_authenticated:
            return Response({"detail": "Authentication credentials were not provided."}, status=status.HTTP_401_UNAUTHORIZED)
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated], authentication_classes=JWT_AUTH)
    def recalc_trust(self, request, pk=None):
        user = self.get_object()
        score = user.recalc_trust_score()
        return Response({"trust_score": score})

    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser], authentication_classes=JWT_AUTH, url_path="suspend")
    def suspend(self, request, pk=None):
        target = self.get_object()
        reason = str(request.data.get("reason") or "Admin suspension")[:255]
        target.status = "suspended"
        target.save(update_fields=["status"])
        try:
            from apps.moderation.models import AuditLog
            AuditLog.objects.create(
                actor_id=request.user.id,
                action="admin.user.suspend",
                target_type="USER",
                target_id=target.id,
                metadata={"reason": reason},
                ip_address=request.META.get("REMOTE_ADDR"),
            )
        except Exception:
            pass
        return Response({"detail": "User suspended", "status": target.status})

    @action(
        detail=False,
        methods=["get"],
        url_path="resolve-handle",
        permission_classes=[IsAuthenticated],
        authentication_classes=JWT_AUTH,
    )
    def resolve_handle(self, request):
        raw_handle = str(
            request.query_params.get("handle")
            or request.query_params.get("value")
            or ""
        ).strip()
        if not raw_handle:
            return Response({"detail": "handle is required."}, status=status.HTTP_400_BAD_REQUEST)

        normalized_key = _normalize_kis_handle_key(raw_handle)
        if not normalized_key:
            return Response({"detail": "Invalid handle format."}, status=status.HTTP_400_BAD_REQUEST)

        visible_name = _extract_kis_handle_visible_name(raw_handle)
        query = User.objects.select_related("profile").exclude(display_name__isnull=True).exclude(display_name__exact="")
        if visible_name:
            query = query.filter(display_name__icontains=visible_name[:120])

        matched_user = None
        for candidate in query.order_by("created_at")[:200]:
            if _normalize_kis_handle_key(getattr(candidate, "display_name", "")) == normalized_key:
                matched_user = candidate
                break

        if matched_user is None:
            # Last fallback: bounded scan for legacy names that don't pass the icontains pre-filter.
            fallback_query = (
                User.objects.select_related("profile")
                .exclude(display_name__isnull=True)
                .exclude(display_name__exact="")
                .order_by("created_at")[:200]
            )
            for candidate in fallback_query:
                if _normalize_kis_handle_key(getattr(candidate, "display_name", "")) == normalized_key:
                    matched_user = candidate
                    break

        if matched_user is None:
            return Response({"detail": "No user matched this handle."}, status=status.HTTP_404_NOT_FOUND)

        profile = getattr(matched_user, "profile", None)
        avatar_url = ""
        if profile is not None:
            avatar_url = str(getattr(profile, "avatar_url", "") or "").strip()
            if not avatar_url and getattr(profile, "avatar_file", None):
                try:
                    avatar_url = str(profile.avatar_file.url or "").strip()
                except Exception:
                    avatar_url = ""

        return Response(
            {
                "handle": raw_handle,
                "normalized_key": normalized_key,
                "user": {
                    "id": str(matched_user.id),
                    "display_name": str(
                        getattr(matched_user, "display_name", "")
                        or getattr(matched_user, "username", "")
                        or getattr(matched_user, "phone", "")
                        or "KIS user"
                    ).strip(),
                    "profile_id": str(getattr(profile, "id", "") or "").strip() or None,
                    "avatar_url": avatar_url or None,
                },
            },
            status=status.HTTP_200_OK,
        )

    @action(
        detail=False,
        methods=["get"],
        url_path="check-status",
        permission_classes=[AllowAny],
        authentication_classes=[],
    )
    def check_status(self, request):
        phone = (request.query_params.get("phone") or "").strip()
        user = request.user if getattr(request.user, "is_authenticated", False) else None
        if not user and phone:
            user = User.objects.filter(phone=phone).first()

        if not user:
            return Response({"success": False, "message": "user not found"}, status=404)

        return Response(
            {
                "success": True,
                "user": {
                    "id": user.id,
                    "phone": user.phone,
                    "status": user.status,
                    "is_active": user.is_active,
                    "verification": user.verification,
                },
            },
            status=200,
        )
@extend_schema_view(
    list=extend_schema(summary="List profiles"),
    retrieve=extend_schema(summary="Retrieve profile"),
)
class ProfileViewSet(viewsets.ModelViewSet):
    queryset = Profile.objects.select_related("user").all()
    serializer_class = ProfileSerializer
    authentication_classes = JWT_AUTH
    permission_classes = IS_AUTH_OR_RO
    parser_classes = (MultiPartParser, FormParser, JSONParser)
    filter_backends = [filters.SearchFilter]
    search_fields = ["headline", "bio", "industry"]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated], authentication_classes=JWT_AUTH)
    def me(self, request):
        ensure_default_account_tiers()
        profile, _ = Profile.objects.select_related("user").get_or_create(user=request.user)
        payload = _build_profile_payload(profile, request.user, request=request)
        payload["privacy"] = ProfileFieldVisibilitySerializer(
            ProfileFieldVisibility.objects.filter(user=request.user), many=True
        ).data
        payload["tiers"] = AccountTierSerializer(public_account_tiers_qs(), many=True).data
        payload.update(_partner_profile_summary(request.user, request))

        # --- connection stats (own profile: count only, degree is always None) ---
        connection_count = UserConnection.objects.filter(
            Q(from_user=request.user) | Q(to_user=request.user),
            status=UserConnection.STATUS_ACCEPTED,
        ).count()
        payload["connection_count"] = connection_count
        payload["connection_degree"] = None
        return Response(payload)

    @action(detail=True, methods=["get"], permission_classes=[permissions.AllowAny], authentication_classes=JWT_AUTH)
    def view(self, request, pk=None):
        profile = self.get_object()
        viewer = request.user if getattr(request.user, "is_authenticated", False) else None
        payload = _build_profile_payload(profile, viewer, request=request)

        # --- connection stats ---
        connection_count = UserConnection.objects.filter(
            Q(from_user=profile.user) | Q(to_user=profile.user),
            status=UserConnection.STATUS_ACCEPTED,
        ).count()

        degree = None
        me = request.user
        if me.is_authenticated and me != profile.user:
            is_first = UserConnection.objects.filter(
                Q(from_user=me, to_user=profile.user) | Q(from_user=profile.user, to_user=me),
                status=UserConnection.STATUS_ACCEPTED,
            ).exists()
            if is_first:
                degree = 1
            else:
                my_ids = set(
                    UserConnection.objects.filter(
                        Q(from_user=me) | Q(to_user=me),
                        status=UserConnection.STATUS_ACCEPTED,
                    ).values_list("from_user_id", flat=True)
                ) | set(
                    UserConnection.objects.filter(
                        Q(from_user=me) | Q(to_user=me),
                        status=UserConnection.STATUS_ACCEPTED,
                    ).values_list("to_user_id", flat=True)
                )
                my_ids.discard(me.id)
                their_ids = set(
                    UserConnection.objects.filter(
                        Q(from_user=profile.user) | Q(to_user=profile.user),
                        status=UserConnection.STATUS_ACCEPTED,
                    ).values_list("from_user_id", flat=True)
                ) | set(
                    UserConnection.objects.filter(
                        Q(from_user=profile.user) | Q(to_user=profile.user),
                        status=UserConnection.STATUS_ACCEPTED,
                    ).values_list("to_user_id", flat=True)
                )
                their_ids.discard(profile.user.id)
                degree = 2 if (my_ids & their_ids) else 3

        payload["connection_count"] = connection_count
        payload["connection_degree"] = degree
        return Response(payload)

    @action(detail=True, methods=["post"], url_path="endorse-skill", permission_classes=[IsAuthenticated], authentication_classes=JWT_AUTH)
    def endorse_skill(self, request, pk=None):
        profile_obj = self.get_object()
        if profile_obj.user == request.user:
            return Response({"detail": "Cannot endorse your own skill."}, status=400)
        skill_id = request.data.get("skill_id")
        if not skill_id:
            return Response({"detail": "skill_id required."}, status=400)
        skill = UserSkill.objects.filter(user=profile_obj.user, id=skill_id).first()
        if not skill:
            return Response({"detail": "Skill not found."}, status=404)
        skill.endorsements = (skill.endorsements or 0) + 1
        skill.save(update_fields=["endorsements"])
        return Response({"endorsements": skill.endorsements})

    @action(detail=False, methods=["post"], url_path="open-to-work", permission_classes=[IsAuthenticated], authentication_classes=JWT_AUTH)
    def set_open_to_work(self, request):
        profile_obj = Profile.objects.get(user=request.user)
        profile_obj.open_to_work = bool(request.data.get("open_to_work", False))
        profile_obj.save(update_fields=["open_to_work"])
        return Response({"open_to_work": profile_obj.open_to_work})

    @action(detail=False, methods=["get"], url_path="discover", permission_classes=[IsAuthenticated], authentication_classes=JWT_AUTH)
    def discover(self, request):
        qs = Profile.objects.select_related("user").filter(visibility="public")
        open_to_work = request.query_params.get("open_to_work", "").strip()
        industry = request.query_params.get("industry", "").strip()
        skills = request.query_params.get("skills", "").strip()
        search = request.query_params.get("search", "").strip()
        if open_to_work in ("true", "1"):
            qs = qs.filter(open_to_work=True)
        if industry:
            qs = qs.filter(industry__icontains=industry)
        if search:
            qs = qs.filter(
                Q(headline__icontains=search) |
                Q(bio__icontains=search) |
                Q(user__display_name__icontains=search)
            )
        if skills:
            for skill_name in [s.strip() for s in skills.split(",") if s.strip()]:
                qs = qs.filter(user__user_skills__description__icontains=skill_name)
        qs = qs[:50]
        data = []
        for p in qs:
            data.append({
                "id": str(p.user_id),
                "profile_id": str(p.id),
                "display_name": p.user.display_name or p.user.username,
                "headline": p.headline or "",
                "bio": (p.bio or "")[:200],
                "industry": p.industry or "",
                "avatar_url": p.avatar_url or "",
                "open_to_work": p.open_to_work,
                "completion_score": p.completion_score,
            })
        return Response(data)


@extend_schema_view(
    list=extend_schema(summary="List profile privacy rules"),
    retrieve=extend_schema(summary="Retrieve profile privacy rule"),
    create=extend_schema(summary="Create profile privacy rule"),
    update=extend_schema(summary="Update profile privacy rule"),
    partial_update=extend_schema(summary="Partially update profile privacy rule"),
    destroy=extend_schema(summary="Delete profile privacy rule"),
)
class ProfileFieldVisibilityViewSet(viewsets.ModelViewSet):
    queryset = ProfileFieldVisibility.objects.all()
    serializer_class = ProfileFieldVisibilitySerializer
    authentication_classes = JWT_AUTH
    permission_classes = (IsAuthenticated, IsOwnerOrReadOnly)

    def get_queryset(self):
        if getattr(self.request.user, "is_authenticated", False):
            return ProfileFieldVisibility.objects.filter(user=self.request.user).prefetch_related("allow_targets")
        return ProfileFieldVisibility.objects.none()

    def perform_create(self, serializer):
        require_feature(self.request.user, "privacy_custom")
        serializer.save(user=self.request.user)

    def perform_update(self, serializer):
        require_feature(self.request.user, "privacy_custom")
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        require_feature(self.request.user, "privacy_custom")
        super().perform_destroy(instance)


@extend_schema_view(
    list=extend_schema(summary="List profile articles"),
    retrieve=extend_schema(summary="Retrieve profile article"),
    create=extend_schema(summary="Create profile article"),
    update=extend_schema(summary="Update profile article"),
    partial_update=extend_schema(summary="Partially update profile article"),
    destroy=extend_schema(summary="Delete profile article"),
)
class ProfileArticleViewSet(viewsets.ModelViewSet):
    queryset = ProfileArticle.objects.all()
    serializer_class = ProfileArticleSerializer
    authentication_classes = JWT_AUTH
    permission_classes = (permissions.IsAuthenticatedOrReadOnly,)
    filter_backends = [filters.SearchFilter]
    search_fields = ["title", "summary", "body"]

    def get_queryset(self):
        user = getattr(self.request.user, "is_authenticated", False) and self.request.user or None
        qs = ProfileArticle.objects.all().prefetch_related("allow_targets")
        if not user:
            return qs.filter(status="published", visibility="public")
        if user.is_staff or user.is_superuser:
            return qs
        if self.action in ("retrieve",):
            return qs.filter(status="published", visibility="public") | qs.filter(user=user)
        return qs.filter(user=user)

    def perform_create(self, serializer):
        require_feature(self.request.user, "profile_articles")
        serializer.save(user=self.request.user)

    def perform_update(self, serializer):
        require_feature(self.request.user, "profile_articles")
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        require_feature(self.request.user, "profile_articles")
        super().perform_destroy(instance)


@extend_schema_view(
    list=extend_schema(summary="List profile preferences"),
    retrieve=extend_schema(summary="Retrieve profile preferences"),
    create=extend_schema(summary="Create profile preferences"),
    update=extend_schema(summary="Update profile preferences"),
    partial_update=extend_schema(summary="Partially update profile preferences"),
)
class ProfilePreferencesViewSet(viewsets.ModelViewSet):
    queryset = ProfilePreferences.objects.all()
    serializer_class = ProfilePreferencesSerializer
    authentication_classes = JWT_AUTH
    permission_classes = (IsAuthenticated, IsOwnerOrReadOnly)

    def get_queryset(self):
        if getattr(self.request.user, "is_authenticated", False):
            return ProfilePreferences.objects.filter(user=self.request.user)
        return ProfilePreferences.objects.none()

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated], authentication_classes=JWT_AUTH)
    def me(self, request):
        pref = ProfilePreferences.objects.filter(user=request.user).first()
        if not pref:
            pref = ProfilePreferences.objects.create(user=request.user)
        serializer = self.get_serializer(pref)
        return Response(serializer.data)


class FamilyAccessibilityPreferencesView(APIView):
    authentication_classes = JWT_AUTH
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        return Response(serialize_family_accessibility_preferences(request.user), status=status.HTTP_200_OK)

    def patch(self, request):
        if not isinstance(request.data, dict):
            raise DRFValidationError({"detail": "Preference updates must be an object."})
        payload = request.data.get("preferences") if isinstance(request.data.get("preferences"), dict) else request.data
        return Response(update_family_accessibility_preferences(request.user, payload), status=status.HTTP_200_OK)


@extend_schema_view(
    list=extend_schema(summary="List profile languages"),
    retrieve=extend_schema(summary="Retrieve profile language"),
    create=extend_schema(summary="Create profile language"),
    update=extend_schema(summary="Update profile language"),
    partial_update=extend_schema(summary="Partially update profile language"),
    destroy=extend_schema(summary="Delete profile language"),
)
class ProfileLanguageViewSet(viewsets.ModelViewSet):
    queryset = ProfileLanguage.objects.all()
    serializer_class = ProfileLanguageSerializer
    authentication_classes = JWT_AUTH
    permission_classes = (IsAuthenticated, IsOwnerOrReadOnly)
    filter_backends = [filters.SearchFilter]
    search_fields = ["name"]

    def get_queryset(self):
        if getattr(self.request.user, "is_authenticated", False):
            return ProfileLanguage.objects.filter(user=self.request.user).order_by("created_at")
        return ProfileLanguage.objects.none()

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated], authentication_classes=JWT_AUTH)
    def sync(self, request):
        raw_languages = request.data.get("languages")
        if raw_languages is None:
            raw_languages = []
        if not isinstance(raw_languages, list):
            return Response({"detail": "languages must be a list."}, status=status.HTTP_400_BAD_REQUEST)

        normalized = _normalize_language_values(raw_languages, ensure_default=True)

        with transaction.atomic():
            ProfileLanguage.objects.filter(user=request.user).delete()
            ProfileLanguage.objects.bulk_create(
                [ProfileLanguage(user=request.user, name=label) for label in normalized]
            )

        saved = ProfileLanguage.objects.filter(user=request.user).order_by("created_at")
        serialized = self.get_serializer(saved, many=True).data
        return Response(
            {
                "languages": [entry["name"] for entry in serialized],
                "results": serialized,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    list=extend_schema(summary="List profile showcases"),
    retrieve=extend_schema(summary="Retrieve profile showcase"),
    create=extend_schema(summary="Create profile showcase"),
    update=extend_schema(summary="Update profile showcase"),
    partial_update=extend_schema(summary="Partially update profile showcase"),
    destroy=extend_schema(summary="Delete profile showcase"),
)
class ProfileShowcaseViewSet(viewsets.ModelViewSet):
    queryset = ProfileShowcase.objects.all()
    serializer_class = ProfileShowcaseSerializer
    authentication_classes = JWT_AUTH
    permission_classes = (IsAuthenticated, IsOwnerOrReadOnly)
    parser_classes = (MultiPartParser, FormParser, JSONParser)
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["type"]

    def get_queryset(self):
        if getattr(self.request.user, "is_authenticated", False):
            return ProfileShowcase.objects.filter(user=self.request.user)
        return ProfileShowcase.objects.none()

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class ApiTokenViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    serializer_class = ApiTokenListSerializer
    queryset = ApiToken.objects.none()
    authentication_classes = JWT_AUTH
    permission_classes = (IsAuthenticated,)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ApiToken.objects.none()
        require_feature(self.request.user, "api_access")
        return ApiToken.objects.filter(user=self.request.user, is_deleted=False).order_by("-created_at")

    def create(self, request, *args, **kwargs):
        require_feature(request.user, "api_access")
        serializer = ApiTokenCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token, plain = request.user.create_api_token(
            name=serializer.validated_data.get("name"),
            scopes=serializer.validated_data.get("scopes"),
            expires_in_days=serializer.validated_data["expires_in_days"],
        )
        data = self.get_serializer(token).data
        data["plain_token"] = plain
        return Response(data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        require_feature(request.user, "api_access")
        instance = self.get_object()
        instance.is_deleted = True
        instance.save(update_fields=["is_deleted", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

@extend_schema_view(
    list=extend_schema(summary="List account tiers"),
    retrieve=extend_schema(summary="Retrieve account tier"),
)
class AccountTierViewSet(viewsets.ModelViewSet):
    serializer_class = AccountTierSerializer
    authentication_classes = JWT_AUTH
    permission_classes = IS_AUTH_OR_RO
    filter_backends = [filters.SearchFilter]
    search_fields = ["name"]

    def get_queryset(self):
        ensure_default_account_tiers()
        return public_account_tiers_qs()

@extend_schema_view(
    list=extend_schema(summary="List subscriptions"),
    retrieve=extend_schema(summary="Retrieve subscription"),
)
class SubscriptionViewSet(viewsets.ModelViewSet):
    queryset = Subscription.objects.select_related("user", "tier").all()
    serializer_class = SubscriptionSerializer
    authentication_classes = JWT_AUTH
    permission_classes = IS_AUTH_OR_RO
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["status", "tier"]

@extend_schema_view(
    list=extend_schema(summary="List sessions"),
    retrieve=extend_schema(summary="Retrieve session"),
)
class SessionViewSet(viewsets.ModelViewSet):
    queryset = Session.objects.all()
    serializer_class = SessionSerializer
    authentication_classes = JWT_AUTH
    permission_classes = IS_AUTH_OR_RO
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    ordering_fields = ["expires_at"]

@extend_schema_view(
    list=extend_schema(summary="List experiences"),
    retrieve=extend_schema(summary="Retrieve experience"),
    create=extend_schema(summary="Create experience"),
    update=extend_schema(summary="Update experience"),
    partial_update=extend_schema(summary="Partially update experience"),
    destroy=extend_schema(summary="Delete experience"),
)
class ExperienceViewSet(viewsets.ModelViewSet):
    queryset = ExperienceSerializer.Meta.model.objects.all()
    serializer_class = ExperienceSerializer
    authentication_classes = JWT_AUTH
    permission_classes = (IsOwnerOrReadOnly,)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

@extend_schema_view(
    list=extend_schema(summary="List educations"),
    retrieve=extend_schema(summary="Retrieve education"),
)
class EducationViewSet(viewsets.ModelViewSet):
    queryset = EducationSerializer.Meta.model.objects.all()
    serializer_class = EducationSerializer
    authentication_classes = JWT_AUTH
    permission_classes = (IsOwnerOrReadOnly,)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

@extend_schema_view(
    list=extend_schema(summary="List user skills"),
    retrieve=extend_schema(summary="Retrieve user skill"),
)
class UserSkillViewSet(viewsets.ModelViewSet):
    queryset = UserSkillSerializer.Meta.model.objects.all()
    serializer_class = UserSkillSerializer
    authentication_classes = JWT_AUTH
    permission_classes = (IsOwnerOrReadOnly,)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

@extend_schema_view(
    list=extend_schema(summary="List projects"),
    retrieve=extend_schema(summary="Retrieve project"),
)
class ProjectViewSet(viewsets.ModelViewSet):
    queryset = ProjectSerializer.Meta.model.objects.all()
    serializer_class = ProjectSerializer
    authentication_classes = JWT_AUTH
    permission_classes = (IsOwnerOrReadOnly,)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

@extend_schema_view(
    list=extend_schema(summary="List recommendations"),
    retrieve=extend_schema(summary="Retrieve recommendation"),
    create=extend_schema(summary="Create recommendation"),
    update=extend_schema(summary="Update recommendation"),
    partial_update=extend_schema(summary="Partially update recommendation"),
    destroy=extend_schema(summary="Delete recommendation"),
)
class RecommendationViewSet(viewsets.ModelViewSet):
    queryset = RecommendationSerializer.Meta.model.objects.all()
    serializer_class = RecommendationSerializer
    authentication_classes = JWT_AUTH

    def get_permissions(self):
        if self.action in ["update", "partial_update", "destroy"]:
            return [IsAuthenticated(), IsOwnerOrReadOnly()]
        return [permissions.IsAuthenticatedOrReadOnly()]

    def perform_create(self, serializer):
        serializer.save(recommender_user=self.request.user)

class CheckContact(APIView):
    """
    GET /api/v1/contacts/check?phone=+237676139884

    Headers:
      Authorization: Bearer <access_token>

    Response (example if user exists):
      {
        "registered": true,
        "userId": 7,
        "user_id": 7,
        "chatId": null
      }

    If no user with that phone:
      {
        "registered": false
      }
    """
    authentication_classes = JWT_AUTH
    permission_classes = [IsAuthenticated]

    @staticmethod
    def _normalized_variants(phone: str, country_hint: Optional[str] = None):
        raw = (phone or "").strip()
        if not raw:
            return []

        region = str(country_hint or "CM").strip().upper() or "CM"
        digits = "".join(ch for ch in raw if ch.isdigit())
        variants = [raw]

        if digits:
            variants.append(digits)
            variants.append(f"+{digits}")
            if digits.startswith("00") and len(digits) > 2:
                intl_digits = digits[2:]
                variants.append(intl_digits)
                variants.append(f"+{intl_digits}")
            if digits.startswith("0") and len(digits) > 1:
                trimmed = digits[1:]
                variants.append(trimmed)
                variants.append(f"+{trimmed}")

        for candidate in (raw, digits):
            if not candidate:
                continue
            try:
                e164 = to_e164(candidate, region)
            except Exception:
                continue
            variants.append(e164)
            variants.append(e164[1:] if e164.startswith("+") else e164)
            # Also add the national (local) number so we can match users whose
            # phone field was stored without a country code prefix (e.g. "676139884"
            # vs the input "+237676139884"). Without this, digit_to_uid keyed by
            # phone_number (local-only) would never be hit for an international input.
            try:
                parsed_num = _phonenumbers.parse(e164, None)
                national = str(parsed_num.national_number)
                if national:
                    variants.append(national)
            except Exception:
                pass

        seen = set()
        ordered = []
        for value in variants:
            normalized = str(value or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
        return ordered

    @staticmethod
    def _digits_only(value: str | None) -> str:
        return "".join(ch for ch in str(value or "") if ch.isdigit())

    @classmethod
    def _resolve_by_digits_fallback(cls, phone: str, exclude_user_id: Optional[str] = None):
        input_digits = cls._digits_only(phone)
        if not input_digits:
            return None

        qs = User.objects.exclude(phone__isnull=True).exclude(phone="")
        if exclude_user_id:
            qs = qs.exclude(id=exclude_user_id)

        matches = []
        for user in qs.only("id", "phone").order_by("id"):
            user_digits = cls._digits_only(getattr(user, "phone", ""))
            if not user_digits:
                continue
            if user_digits == input_digits or user_digits.endswith(input_digits) or input_digits.endswith(user_digits):
                matches.append(user)
                if len(matches) > 1:
                    # Ambiguous fallback match: require stricter phone format.
                    return None
        return matches[0] if matches else None

    @classmethod
    def _canonical_contact_phone(cls, candidates: list[str], fallback: str) -> str:
        for value in candidates:
            candidate = str(value or "").strip()
            if candidate.startswith("+"):
                return candidate
        for value in candidates:
            candidate = str(value or "").strip()
            if candidate:
                return candidate
        return str(fallback or "").strip()

    @classmethod
    def _record_contact_lookup(
        cls,
        owner: User,
        contact_user: Optional[User],
        phone: str,
        candidates: list[str],
    ) -> None:
        if not owner:
            return
        if contact_user and str(contact_user.id) == str(owner.id):
            return

        canonical_phone = cls._canonical_contact_phone(candidates, phone)
        canonical_digits = cls._digits_only(canonical_phone) or cls._digits_only(phone)

        if contact_user:
            canonical_phone = str(contact_user.phone or canonical_phone or phone or "").strip()
            canonical_digits = str(contact_user.phone_number or canonical_digits or "").strip()

        defaults = {
            "contact_user": contact_user,
            "contact_phone": canonical_phone or str(phone or "").strip(),
            "contact_phone_number": canonical_digits,
            "contact_country_code": str(getattr(contact_user, "phone_country_code", "") or "").strip() if contact_user else "",
            "contact_display_name": str(getattr(contact_user, "display_name", "") or "").strip() if contact_user else "",
        }

        existing = None
        if contact_user:
            existing = UserContact.objects.filter(user=owner, contact_user=contact_user).order_by("-updated_at").first()
        if not existing:
            existing = UserContact.objects.filter(
                user=owner,
            ).filter(
                Q(contact_phone__in=candidates)
                | Q(contact_phone_number=canonical_digits)
            ).order_by("-updated_at").first()

        if existing:
            for key, value in defaults.items():
                setattr(existing, key, value)
            existing.save(update_fields=[
                "contact_user",
                "contact_phone",
                "contact_phone_number",
                "contact_country_code",
                "contact_display_name",
                "updated_at",
            ])
            return

        UserContact.objects.create(
            user=owner,
            **defaults,
        )

    def get(self, request, *args, **kwargs):
        phone = request.query_params.get("phone")
        country_hint = request.query_params.get("country") or getattr(request.user, "country", None)

        if not phone:
            return Response({"detail": "phone is required"}, status=400)

        candidates = self._normalized_variants(phone, country_hint)
        if not candidates:
            return Response({"detail": "phone is required"}, status=400)

        digit_candidates = [self._digits_only(value) for value in candidates]
        digit_candidates = [value for value in digit_candidates if value]
        base_qs = User.objects.filter(
            Q(phone__in=candidates) |
            Q(phone_number__in=digit_candidates)
        )
        preferred_qs = base_qs.exclude(id=request.user.id)

        user = None
        for candidate in candidates:
            user = preferred_qs.filter(phone=candidate).order_by("id").first()
            if user:
                break

        if not user:
            for candidate in candidates:
                user = base_qs.filter(phone=candidate).order_by("id").first()
                if user:
                    break

        if not user:
            user = self._resolve_by_digits_fallback(phone, exclude_user_id=str(request.user.id))

        if not user:
            user = self._resolve_by_digits_fallback(phone, exclude_user_id=None)

        if not user:
            self._record_contact_lookup(request.user, None, phone, candidates)
            return Response({"registered": False})

        self._record_contact_lookup(request.user, user, phone, candidates)
        chat_id = None
        profile_id = str(getattr(getattr(user, "profile", None), "id", "") or "")
        return Response({
            "registered": True,
            "userId": user.id,
            "user_id": user.id,
            "profileId": profile_id or None,
            "profile_id": profile_id or None,
            "display_name": user.display_name or user.username or user.phone,
            "phone": user.phone,
            "chatId": chat_id,
        })

    def post(self, request, *args, **kwargs):
        """
        POST /api/v1/users/check-contacts/
        Body: { "phones": ["+237676...", ...] }  max 500 per request

        Returns: { "results": { "+237676...": { "registered": bool, "user_id": int|null } } }

        Single bulk DB query (both phone fields are indexed) — does NOT run
        the full-table-scan digits fallback used by the single-phone GET path.
        """
        phones_raw = request.data.get("phones")
        if not isinstance(phones_raw, list) or not phones_raw:
            return Response({"detail": "phones must be a non-empty list"}, status=400)

        # Normalise and cap input
        phones: list[str] = [str(p).strip() for p in phones_raw[:500] if p and str(p).strip()]
        if not phones:
            return Response({"detail": "No valid phones provided"}, status=400)

        country_hint: str = str(getattr(request.user, "country", None) or "CM").strip().upper() or "CM"

        # Build variant map and collect all variants in one pass
        phone_to_variants: dict[str, list[str]] = {}
        all_phone_variants: set[str] = set()
        all_digit_variants: set[str] = set()

        for phone in phones:
            variants = self._normalized_variants(phone, country_hint)
            phone_to_variants[phone] = variants
            for v in variants:
                all_phone_variants.add(v)
                d = self._digits_only(v)
                if d:
                    all_digit_variants.add(d)

        # Single bulk DB query — O(index scan), not O(table scan)
        matched_users = (
            User.objects
            .filter(Q(phone__in=all_phone_variants) | Q(phone_number__in=all_digit_variants))
            .exclude(id=request.user.id)
            .only("id", "phone", "phone_number")
        )

        # Build in-memory lookup maps from the query results
        phone_to_uid: dict[str, int] = {}
        digit_to_uid: dict[str, int] = {}
        for u in matched_users:
            if u.phone:
                phone_to_uid[str(u.phone)] = u.id
            if u.phone_number:
                digit_to_uid[str(u.phone_number)] = u.id

        # Resolve each input phone against the lookup maps
        results: dict[str, dict] = {}
        for phone in phones:
            variants = phone_to_variants.get(phone, [])
            user_id: int | None = None

            for v in variants:
                uid = phone_to_uid.get(v)
                if uid:
                    user_id = uid
                    break

            if user_id is None:
                for v in variants:
                    d = self._digits_only(v)
                    uid = digit_to_uid.get(d) if d else None
                    if uid:
                        user_id = uid
                        break

            results[phone] = {"registered": user_id is not None, "user_id": user_id}

        return Response({"results": results})


# ---------------------------------------------------------------------------
# ConnectionSerializer
# ---------------------------------------------------------------------------
class ConnectionSerializer(serializers.ModelSerializer):
    from_user_id = serializers.UUIDField(source="from_user.id", read_only=True)
    from_user_name = serializers.CharField(source="from_user.display_name", read_only=True)
    from_user_avatar = serializers.SerializerMethodField()
    to_user_id = serializers.UUIDField(source="to_user.id", read_only=True)
    to_user_name = serializers.CharField(source="to_user.display_name", read_only=True)
    to_user_avatar = serializers.SerializerMethodField()

    class Meta:
        model = UserConnection
        fields = [
            "id", "from_user_id", "from_user_name", "from_user_avatar",
            "to_user_id", "to_user_name", "to_user_avatar",
            "status", "note", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_from_user_avatar(self, obj):
        p = getattr(obj.from_user, "profile", None)
        return getattr(p, "avatar_url", "") or ""

    def get_to_user_avatar(self, obj):
        p = getattr(obj.to_user, "profile", None)
        return getattr(p, "avatar_url", "") or ""


# ---------------------------------------------------------------------------
# ConnectionViewSet
# ---------------------------------------------------------------------------
class ConnectionViewSet(viewsets.ModelViewSet):
    """Send, accept/reject, list, delete connections."""
    permission_classes = [IsAuthenticated]
    authentication_classes = JWT_AUTH
    http_method_names = ["get", "post", "patch", "delete"]

    def get_queryset(self):
        user = self.request.user
        return UserConnection.objects.filter(
            Q(from_user=user) | Q(to_user=user)
        ).select_related("from_user", "to_user").order_by("-created_at")

    def get_serializer_class(self):
        return ConnectionSerializer

    def create(self, request, *args, **kwargs):
        target_id = request.data.get("user_id")
        if not target_id:
            return Response({"detail": "user_id required."}, status=400)
        UserModel = get_user_model()
        try:
            target = UserModel.objects.get(id=target_id)
        except (UserModel.DoesNotExist, ValueError):
            return Response({"detail": "User not found."}, status=404)
        if target == request.user:
            return Response({"detail": "Cannot connect to yourself."}, status=400)
        existing = UserConnection.objects.filter(
            Q(from_user=request.user, to_user=target) |
            Q(from_user=target, to_user=request.user)
        ).first()
        if existing:
            return Response(ConnectionSerializer(existing).data, status=200)
        conn = UserConnection.objects.create(
            from_user=request.user, to_user=target,
            note=request.data.get("note", ""),
        )
        return Response(ConnectionSerializer(conn).data, status=201)

    def partial_update(self, request, pk=None, **kwargs):
        conn = get_object_or_404(UserConnection, pk=pk, to_user=request.user)
        new_status = request.data.get("status")
        if new_status not in (UserConnection.STATUS_ACCEPTED, UserConnection.STATUS_REJECTED, UserConnection.STATUS_BLOCKED):
            return Response({"detail": "Invalid status."}, status=400)
        conn.status = new_status
        conn.save(update_fields=["status", "updated_at"])
        return Response(ConnectionSerializer(conn).data)

    @action(detail=False, methods=["get"], url_path="people-you-may-know")
    def people_you_may_know(self, request):
        """Suggest users with the same industry or mutual connections."""
        user = request.user
        profile = getattr(user, "profile", None)
        industry = getattr(profile, "industry", "") or ""
        connected_ids = set(
            UserConnection.objects.filter(
                Q(from_user=user) | Q(to_user=user),
                status=UserConnection.STATUS_ACCEPTED,
            ).values_list("from_user_id", flat=True)
        ) | set(
            UserConnection.objects.filter(
                Q(from_user=user) | Q(to_user=user),
                status=UserConnection.STATUS_ACCEPTED,
            ).values_list("to_user_id", flat=True)
        )
        connected_ids.add(user.id)
        UserModel = get_user_model()
        if industry:
            suggestions = UserModel.objects.exclude(id__in=connected_ids).filter(
                profile__industry=industry
            ).select_related("profile")[:20]
        else:
            suggestions = UserModel.objects.exclude(id__in=connected_ids).select_related("profile")[:20]
        data = []
        for u in suggestions:
            p = getattr(u, "profile", None)
            data.append({
                "id": str(u.id),
                "display_name": u.display_name or u.username,
                "headline": getattr(p, "headline", ""),
                "avatar_url": getattr(p, "avatar_url", ""),
                "industry": getattr(p, "industry", ""),
            })
        return Response(data)


# ---------------------------------------------------------------------------
# MyApplicationsView
# ---------------------------------------------------------------------------
class MyApplicationsView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = JWT_AUTH
    serializer_class = PartnerApplicationDetailSerializer

    def get_queryset(self):
        return PartnerApplication.objects.filter(
            user=self.request.user
        ).select_related("partner", "job_post").order_by("-created_at")


# ---------------------------------------------------------------------------
# GlobalJobBoardView
# ---------------------------------------------------------------------------
class GlobalJobBoardView(generics.ListAPIView):
    permission_classes = IS_AUTH_OR_RO
    authentication_classes = JWT_AUTH
    serializer_class = PartnerJobPostSerializer

    def get_queryset(self):
        qs = PartnerJobPost.objects.filter(is_active=True).select_related("partner").order_by("-created_at")
        search = self.request.query_params.get("search", "").strip()
        job_type = self.request.query_params.get("job_type", "").strip()
        is_remote = self.request.query_params.get("is_remote", "").strip()
        partner_id = self.request.query_params.get("partner_id", "").strip()
        skills = self.request.query_params.get("skills", "").strip()
        if search:
            qs = qs.filter(
                Q(title__icontains=search) |
                Q(description__icontains=search) |
                Q(location__icontains=search)
            )
        if job_type:
            qs = qs.filter(job_type=job_type)
        if is_remote in ("true", "1"):
            qs = qs.filter(is_remote=True)
        if partner_id:
            qs = qs.filter(partner_id=partner_id)
        if skills:
            for s in [x.strip() for x in skills.split(",") if x.strip()]:
                qs = qs.filter(tags__icontains=s)
        return qs


# ===========================================================================
# Multi-device QR Login Views
# ===========================================================================

IS_AUTH = (IsAuthenticated,)
_QR_JWT_AUTH = (DeviceBoundJWTAuthentication,)


def _send_push_to_device(user: User, device: Device, title: str, body: str) -> None:
    """Best-effort push notification to a specific device. Swallows all errors."""
    try:
        from apps.notifications.models import NotificationDeviceToken, Notification
        from apps.notifications.firebase import send_push
        token_obj = NotificationDeviceToken.objects.filter(
            user=user, device_id=str(device.device_id)
        ).first()
        if token_obj and token_obj.token:
            notif = Notification(
                user=user,
                title=title,
                body=body,
                notification_type="device.linked",
                channel="PUSH",
            )
            send_push(token_obj.token, notif)
    except Exception:
        pass


def _send_recovery_email(user: User, recovery_code: str) -> None:
    """Best-effort recovery email. Swallows all errors."""
    try:
        from apps.notifications.email_service import send_templated_email
        email = getattr(user, "email", None)
        if email:
            send_templated_email(
                to_email=email,
                subject="KIS Account Recovery — your parent device recovery code",
                template_name="device_recovery",
                context={"recovery_code": recovery_code, "expires_minutes": 15},
            )
    except Exception:
        pass


@extend_schema(
    summary="Generate a QR login token for the parent device",
    tags=["Auth", "Devices"],
)
class DeviceQRGenerateView(APIView):
    """
    GET auth/devices/qr/
    Authenticated by the PARENT device. Returns a short-lived token encoded
    into a QR code client-side. The secondary device scans this QR and posts
    the token to /auth/devices/qr-login/.
    """
    authentication_classes = _QR_JWT_AUTH
    permission_classes = IS_AUTH

    def get(self, request):
        device_id = request_device_id(request)
        if not device_id:
            return Response({"detail": "X-Device-Id header is required."}, status=status.HTTP_400_BAD_REQUEST)

        device = Device.objects.filter(
            user=request.user,
            device_id=str(device_id),
            is_parent=True,
            revoked_at__isnull=True,
        ).first()
        if not device:
            return Response(
                {"detail": "Only the parent device may generate a QR login token."},
                status=status.HTTP_403_FORBIDDEN,
            )

        qr_token, token_plain = DeviceQRToken.generate_for_device(request.user, device)
        AuditLog.log(
            actor=request.user,
            action="device.qr_generated",
            meta={"parent_device_id": device_id},
        )
        return Response(
            {
                "qr_payload": token_plain,
                "expires_at": qr_token.expires_at.isoformat(),
                "nonce": qr_token.nonce,
            },
            status=status.HTTP_200_OK,
        )


class _QRLoginSerializer(serializers.Serializer):
    token = serializers.CharField()
    device_id = serializers.CharField()
    device_name = serializers.CharField(required=False, allow_blank=True, default="")
    platform = serializers.CharField(required=False, allow_blank=True, default="unknown")


@extend_schema(
    summary="Login via QR code scan (secondary device)",
    tags=["Auth", "Devices"],
)
class DeviceQRLoginView(APIView):
    """
    POST auth/devices/qr-login/
    No auth required. The secondary device posts the token_plain it read
    from the QR code. On success, returns JWT tokens for the associated user.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        ser = _QRLoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        token_plain = data["token"]
        device_id = data["device_id"].strip()
        device_name = (data.get("device_name") or "").strip() or None
        platform = (data.get("platform") or "unknown").strip()

        qr_token = DeviceQRToken.consume(token_plain)
        if not qr_token:
            return Response({"detail": "Invalid or expired QR token."}, status=status.HTTP_401_UNAUTHORIZED)

        user = qr_token.user
        if qr_token.parent_device and str(device_id) == str(qr_token.parent_device.device_id):
            return Response(
                {"detail": "The primary device cannot consume its own secondary-device QR code."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        now = timezone.now()

        with transaction.atomic():
            existing = Device.objects.filter(user=user, device_id=str(device_id)).first()
            token_version = (
                (existing.token_version + 1) if existing and existing.revoked_at
                else (existing.token_version if existing else 1)
            )
            device, _ = Device.objects.update_or_create(
                user=user,
                device_id=str(device_id),
                defaults={
                    "platform": platform,
                    "name": device_name,
                    "last_seen_at": now,
                    "last_ip": request.META.get("REMOTE_ADDR"),
                    "user_agent": request.META.get("HTTP_USER_AGENT"),
                    "token_version": token_version,
                    "revoked_at": None,
                    "revoke_reason": "",
                    "is_parent": False,
                    "linked_via_qr": True,
                    "trusted_until": now + datetime.timedelta(days=30),
                    "parent_device": qr_token.parent_device,
                },
            )
            # Record which device consumed this QR token.
            qr_token.used_by_device = device
            qr_token.save(update_fields=["used_by_device"])

        tokens = issue_tokens_for_user(user, device_id=device_id)

        AuditLog.log(
            actor=user,
            action="device.qr_login",
            meta={
                "linked_device_id": device_id,
                "parent_device_id": str(qr_token.parent_device.device_id) if qr_token.parent_device else None,
            },
        )

        # Notify the parent device.
        if qr_token.parent_device:
            _send_push_to_device(
                user,
                qr_token.parent_device,
                title="New device linked",
                body=f"New device linked: {device_name or device_id}",
            )

        return Response(
            {
                "access": tokens["access"],
                "refresh": tokens["refresh"],
                "token_type": "Bearer",
                "user": {
                    "id": user.id,
                    "phone": getattr(user, "phone", None),
                    "status": getattr(user, "status", "active"),
                    "is_active": user.is_active,
                    "device_id": device_id,
                },
            },
            status=status.HTTP_200_OK,
        )


class _TransferParentSerializer(serializers.Serializer):
    target_device_id = serializers.CharField()


@extend_schema(
    summary="Transfer parent device role to another device",
    tags=["Auth", "Devices"],
)
class TransferParentDeviceView(APIView):
    """
    POST auth/devices/transfer-parent/
    The currently authenticated PARENT device can transfer the parent role
    to another active device owned by the same user.
    """
    authentication_classes = _QR_JWT_AUTH
    permission_classes = IS_AUTH

    def post(self, request):
        requesting_device_id = request_device_id(request)
        if not requesting_device_id:
            return Response({"detail": "X-Device-Id header is required."}, status=status.HTTP_400_BAD_REQUEST)

        current_parent = Device.objects.filter(
            user=request.user,
            device_id=str(requesting_device_id),
            is_parent=True,
            revoked_at__isnull=True,
        ).first()
        if not current_parent:
            return Response(
                {"detail": "Only the current parent device may initiate a parent transfer."},
                status=status.HTTP_403_FORBIDDEN,
            )

        ser = _TransferParentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        target_device_id = ser.validated_data["target_device_id"].strip()

        if str(target_device_id) == str(requesting_device_id):
            return Response({"detail": "Target device is already the parent."}, status=status.HTTP_400_BAD_REQUEST)

        target_device = Device.objects.filter(
            user=request.user,
            device_id=str(target_device_id),
            revoked_at__isnull=True,
        ).first()
        if not target_device:
            return Response(
                {"detail": "Target device not found or has been revoked."},
                status=status.HTTP_404_NOT_FOUND,
            )

        with transaction.atomic():
            current_parent.is_parent = False
            current_parent.save(update_fields=["is_parent", "updated_at"])

            target_device.is_parent = True
            target_device.save(update_fields=["is_parent", "updated_at"])

        AuditLog.log(
            actor=request.user,
            action="device.parent_transfer",
            meta={
                "from_device_id": str(requesting_device_id),
                "to_device_id": str(target_device_id),
            },
        )
        return Response(
            {"detail": "Parent device transferred successfully.", "new_parent_device_id": target_device_id},
            status=status.HTTP_200_OK,
        )


@extend_schema(
    summary="Revoke all secondary (non-parent) devices",
    tags=["Auth", "Devices"],
)
class RevokeAllSecondaryView(APIView):
    """
    DELETE auth/devices/revoke-all-secondary/
    The authenticated PARENT device revokes all other active devices for the user.
    """
    authentication_classes = _QR_JWT_AUTH
    permission_classes = IS_AUTH

    def delete(self, request):
        requesting_device_id = request_device_id(request)
        if not requesting_device_id:
            return Response({"detail": "X-Device-Id header is required."}, status=status.HTTP_400_BAD_REQUEST)

        is_parent = Device.objects.filter(
            user=request.user,
            device_id=str(requesting_device_id),
            is_parent=True,
            revoked_at__isnull=True,
        ).exists()
        if not is_parent:
            return Response(
                {"detail": "Only the parent device may revoke all secondary devices."},
                status=status.HTTP_403_FORBIDDEN,
            )

        now = timezone.now()
        updated = Device.objects.filter(
            user=request.user,
            is_parent=False,
            revoked_at__isnull=True,
        ).update(
            revoked_at=now,
            revoke_reason="parent_revoked_all",
        )

        AuditLog.log(
            actor=request.user,
            action="device.revoke_all_secondary",
            meta={"parent_device_id": requesting_device_id, "revoked_count": updated},
        )
        return Response({"revoked_count": updated}, status=status.HTTP_200_OK)


class _DeviceRenameSerializer(serializers.Serializer):
    nickname = serializers.CharField(max_length=100, allow_blank=True)


@extend_schema(
    summary="Rename a device (set nickname)",
    tags=["Auth", "Devices"],
)
class DeviceRenameView(APIView):
    """
    PATCH auth/devices/<device_id>/rename/
    Any authenticated user may rename any of their own devices.
    """
    authentication_classes = _QR_JWT_AUTH
    permission_classes = IS_AUTH

    def patch(self, request, device_id: str):
        ser = _DeviceRenameSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        device = get_object_or_404(
            Device,
            user=request.user,
            device_id=str(device_id),
            revoked_at__isnull=True,
        )
        device.nickname = ser.validated_data["nickname"]
        device.save(update_fields=["nickname", "updated_at"])

        AuditLog.log(
            actor=request.user,
            action="device.renamed",
            meta={"device_id": device_id, "nickname": device.nickname},
        )
        return Response({"device_id": device_id, "nickname": device.nickname}, status=status.HTTP_200_OK)


# ---- Parent Device Recovery ------------------------------------------------

_RECOVERY_CACHE_PREFIX = "device_recovery:"
_RECOVERY_TTL_SECONDS = 15 * 60  # 15 minutes


class _ParentRecoveryInitSerializer(serializers.Serializer):
    phone = serializers.CharField(required=False, allow_blank=True)
    email = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs.get("phone") and not attrs.get("email"):
            raise serializers.ValidationError("Provide either phone or email.")
        return attrs


@extend_schema(
    summary="Initiate parent device recovery (step 1)",
    tags=["Auth", "Devices"],
)
class ParentRecoveryInitView(APIView):
    """
    POST auth/recovery/initiate/
    No auth required. Sends a recovery code email if the account exists.
    Does NOT reveal whether the account exists to prevent enumeration.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        ser = _ParentRecoveryInitSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        phone = (ser.validated_data.get("phone") or "").strip()
        email = (ser.validated_data.get("email") or "").strip()

        user = None
        if email:
            user = User.objects.filter(email__iexact=email, is_active=True).first()
        if not user and phone:
            from apps.accounts.views import _phone_variants
            candidates = _phone_variants(phone)
            digit_candidates = [_digits_only(v) for v in candidates if _digits_only(v)]
            user = User.objects.filter(
                Q(phone__in=candidates) | Q(phone_number__in=digit_candidates),
                is_active=True,
            ).first()

        # Always return the same message regardless of whether user exists.
        if user:
            import secrets as _secrets
            recovery_code = _secrets.token_urlsafe(24)
            cache_key = f"{_RECOVERY_CACHE_PREFIX}{recovery_code}"
            cache.set(cache_key, str(user.id), timeout=_RECOVERY_TTL_SECONDS)
            _send_recovery_email(user, recovery_code)
            AuditLog.log(
                actor=user,
                action="device.parent_recovery_initiated",
                meta={"identifier": email or phone},
            )

        return Response(
            {"message": "Recovery email sent if account exists."},
            status=status.HTTP_200_OK,
        )


class _ParentRecoveryConfirmSerializer(serializers.Serializer):
    recovery_token = serializers.CharField()
    device_id = serializers.CharField()
    device_name = serializers.CharField(required=False, allow_blank=True, default="")
    platform = serializers.CharField(required=False, allow_blank=True, default="unknown")


@extend_schema(
    summary="Confirm parent device recovery (step 2)",
    tags=["Auth", "Devices"],
)
class ParentRecoveryConfirmView(APIView):
    """
    POST auth/recovery/confirm/
    No auth required. Validates the recovery token, revokes the old parent,
    and registers the new device as the parent.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        ser = _ParentRecoveryConfirmSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        recovery_token = data["recovery_token"].strip()
        device_id = data["device_id"].strip()
        device_name = (data.get("device_name") or "").strip() or None
        platform = (data.get("platform") or "unknown").strip()

        cache_key = f"{_RECOVERY_CACHE_PREFIX}{recovery_token}"
        user_id = cache.get(cache_key)
        if not user_id:
            return Response({"detail": "Invalid or expired recovery token."}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            user = User.objects.get(id=user_id, is_active=True)
        except User.DoesNotExist:
            return Response({"detail": "Invalid or expired recovery token."}, status=status.HTTP_401_UNAUTHORIZED)

        # Consume the token so it cannot be reused.
        cache.delete(cache_key)

        now = timezone.now()
        with transaction.atomic():
            # Revoke the old parent(s).
            Device.objects.filter(
                user=user, is_parent=True, revoked_at__isnull=True
            ).update(
                revoked_at=now,
                revoke_reason="parent_recovery",
                is_parent=False,
            )

            existing = Device.objects.filter(user=user, device_id=str(device_id)).first()
            token_version = (
                (existing.token_version + 1) if existing and existing.revoked_at
                else (existing.token_version if existing else 1)
            )
            device, _ = Device.objects.update_or_create(
                user=user,
                device_id=str(device_id),
                defaults={
                    "platform": platform,
                    "name": device_name,
                    "last_seen_at": now,
                    "last_ip": request.META.get("REMOTE_ADDR"),
                    "user_agent": request.META.get("HTTP_USER_AGENT"),
                    "token_version": token_version,
                    "revoked_at": None,
                    "revoke_reason": "",
                    "is_parent": True,
                    "linked_via_qr": False,
                    "trusted_until": now + datetime.timedelta(days=30),
                    "parent_device": None,
                },
            )

        tokens = issue_tokens_for_user(user, device_id=device_id)

        AuditLog.log(
            actor=user,
            action="device.parent_recovery",
            meta={"new_parent_device_id": device_id},
        )

        return Response(
            {
                "access": tokens["access"],
                "refresh": tokens["refresh"],
                "token_type": "Bearer",
                "user": {
                    "id": user.id,
                    "phone": getattr(user, "phone", None),
                    "status": getattr(user, "status", "active"),
                    "is_active": user.is_active,
                    "device_id": device_id,
                },
            },
            status=status.HTTP_200_OK,
        )
