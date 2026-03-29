import logging
import os
import re
import subprocess
import urllib.request
from urllib.parse import unquote
import uuid
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import models, transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.channels.models import Channel
from apps.chat.models import (
    BaseConversationRole,
    Conversation,
    ConversationMember,
    ConversationSettings,
    ConversationType,
    ConversationSendPolicy,
    ConversationJoinPolicy as ChatConversationJoinPolicy,
)
from apps.communities.models import (
    CommunityMembership,
    CommunityPost,
    CommunityPostStatus,
    CommunityRole,
)
from apps.accounts.models import Profile, User
from apps.accounts.tiers import get_user_tier_features, normalize_limit_value
from apps.commerce.constants import KIS_COIN_CODE
from apps.commerce.models import Product, ShopService, ShopTeamMember, ServiceBooking
from apps.commerce.serializers import ServiceBookingSerializer
from apps.partners.models import Partner, PartnerPost, PartnerMembership, PartnerMembershipStatus
from apps.broadcasts.models import (
    BroadcastFeature,
    BroadcastFeatureFlag,
    BroadcastFeedProfile,
    BroadcastHealthProfile,
    BroadcastHealthInstitution,
    BroadcastHealthInstitutionMember,
    BroadcastHealthInstitutionService,
    BroadcastItem,
    BroadcastMarketProfile,
    BroadcastReaction,
    BroadcastSourceType,
    BroadcastVideo,
    BroadcastLesson,
    LessonEnrollment,
    LessonEnrollmentStatus,
    EducationProfile,
    EducationProfileCourse,
    EducationProfileModule,
    EducationProfileRole,
    EducationProfileRoleAssignment,
    EducationProfileType,
    Medium,
    Service,
    ServiceMediumMap,
)
from apps.broadcasts.services import cleanup_expired_broadcast_items
from apps.broadcasts.health_engine_policy import (
    filter_booking_engine_keys,
    filter_service_medium_pairs,
    is_blocked_service_medium_name,
    is_removed_health_medium_name,
    should_drop_service_after_medium_cleanup,
)
from apps.broadcasts.serializers import (
    BroadcastFeatureSerializer,
    BroadcastFeatureStatusSerializer,
    BroadcastVideoSerializer,
    BroadcastLessonSerializer,
    LessonEnrollmentSerializer,
    EducationProfileSerializer,
    MediumSerializer,
    ServiceSerializer,
)
from apps.broadcasts.media_utils import (
    THUMBNAIL_SUBDIRECTORY,
    build_media_url,
    ensure_local_thumbnail,
)
from apps.billing.services import (
    cents_to_credits,
    cents_to_usd,
    cents_to_usd_compact,
    get_credit_account,
    get_wallet_account,
    record_ledger,
)
from apps.feed_personalization import get_affinity_profile, log_feed_interaction, rank_feed_items
from common.rich_text import build_plain_text_document

logger = logging.getLogger(__name__)


def _get_blocked_service_medium_ids() -> set[str]:
    return {
        str(row.id)
        for row in Medium.objects.only("id", "name")
        if is_blocked_service_medium_name(getattr(row, "name", ""))
    }


def _parse_dt(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        safe = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(safe)
        except ValueError:
            return datetime.min
    return datetime.min


def _to_bool(value: object | None, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _to_cents(cents_value: object | None) -> int:
    try:
        cents = int(round(float(cents_value or 0)))
    except (TypeError, ValueError):
        cents = 0
    return max(0, cents)


def _fetch_channel_messages(
    conversation_ids: list[str],
    since: datetime,
    limit: int,
    message_ids: list[str] | None = None,
    conversation_id: str | None = None,
) -> list[dict]:
    base = getattr(settings, "NEST_INTERNAL_URL", "").rstrip("/")
    token = getattr(settings, "NEST_INTERNAL_TOKEN", "")
    if not base or not token or not conversation_ids:
        return []

    url = f"{base}/internal/broadcasts/channel-messages"
    payload = {
        "conversationIds": [str(cid) for cid in conversation_ids],
        "since": since.isoformat(),
        "limit": limit,
    }
    if message_ids:
        payload["messageIds"] = [str(mid) for mid in message_ids if mid]
    if conversation_id:
        payload["conversationId"] = str(conversation_id)
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Internal-Auth": token,
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8") or "{}"
    except Exception as exc:
        logger.warning("[broadcasts] Unable to fetch channel messages: %s", exc)
        return []

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []

    return (
        parsed.get("messages")
        or parsed.get("data")
        or parsed.get("results")
        or []
    )


def _can_manage_partner(partner: Partner, user) -> bool:
    if not partner:
        return False
    if partner.owner_id == getattr(user, "id", None):
        return True
    if not partner.main_conversation_id:
        return False
    member = ConversationMember.objects.filter(
        conversation_id=partner.main_conversation_id,
        user=user,
        left_at__isnull=True,
    ).first()
    if not member:
        return False
    return member.base_role in {BaseConversationRole.OWNER, BaseConversationRole.ADMIN}


class EducationCourseBroadcastView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        metadata = request.data.get("metadata") or {}
        title = (metadata.get("title") or request.data.get("title") or "").strip()
        if not title:
            raise ValidationError({"title": "Title is required."})

        course_id = str(metadata.get("id") or request.data.get("course_id") or uuid.uuid4())
        partner_id = metadata.get("partner_id") or metadata.get("partner")
        partner = None
        if partner_id:
            partner = Partner.objects.filter(id=partner_id).first()
            if not partner:
                raise ValidationError({"partner_id": "Partner not found."})
            if not _can_manage_partner(partner, request.user):
                raise PermissionDenied("Not allowed to broadcast this course.")

        profile_id = request.data.get("profile_id") or metadata.get("education_profile_id")
        education_profile = None
        if profile_id:
            education_profile = _get_education_profile_or_404(request.user, profile_id)

        expires_at = timezone.now() + timedelta(days=10)
        payload = {
            "title": title,
            "summary": metadata.get("summary") or request.data.get("summary") or "",
            "cover_image": metadata.get("cover_image") or metadata.get("cover_url") or "",
            "price_amount": metadata.get("price_amount"),
            "price_currency": metadata.get("price_currency"),
            "partner_id": str(partner.id) if partner else metadata.get("partner_id"),
            "partner_name": metadata.get("partner_name") or (partner.name if partner else None),
            "source": metadata.get("source")
            or ("partner_course" if partner else "education_profile"),
        }

        if education_profile:
            payload.update(
                {
                    "education_profile_id": str(education_profile.id),
                    "education_profile_name": education_profile.name,
                    "education_profile_type": education_profile.profile_type,
                }
            )

        BroadcastItem.objects.update_or_create(
            source_type=BroadcastSourceType.EDUCATION_COURSE,
            source_id=course_id,
            defaults={
                "broadcasted_by": request.user,
                "broadcasted_at": timezone.now(),
                "expires_at": expires_at,
                "is_deleted": False,
                "metadata": payload,
            },
        )
        return Response({"broadcasted": True}, status=status.HTTP_200_OK)


class EducationProfileBroadcastView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, profile_id: str):
        profile = _get_education_profile_or_404(request.user, profile_id)
        expires_at = timezone.now() + timedelta(days=10)
        courses = list(
            profile.courses.all().values("id", "title", "summary")[:10]
        )
        modules = list(
            profile.modules.all().values("id", "title", "summary")[:10]
        )
        metadata = {
            "profile_id": str(profile.id),
            "profile_name": profile.name,
            "profile_type": profile.profile_type,
            "course_count": len(courses),
            "module_count": len(modules),
            "courses": [
                {
                    "id": str(item.get("id")),
                    "title": item.get("title"),
                    "summary": item.get("summary"),
                }
                for item in courses
            ],
            "modules": [
                {
                    "id": str(item.get("id")),
                    "title": item.get("title"),
                    "summary": item.get("summary"),
                }
                for item in modules
            ],
        }

        BroadcastItem.objects.update_or_create(
            source_type=BroadcastSourceType.EDUCATION_PROFILE,
            source_id=str(profile.id),
            defaults={
                "broadcasted_by": request.user,
                "broadcasted_at": timezone.now(),
                "expires_at": expires_at,
                "is_deleted": False,
                "metadata": metadata,
            },
        )

        return Response({"broadcasted": True}, status=status.HTTP_200_OK)


class EducationProfilePermissionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"permissions": EDUCATION_PROFILE_ROLE_PERMISSIONS}, status=status.HTTP_200_OK)


SHORT_VIDEO_MAX_SECONDS = 4 * 60 - 1
LONG_VIDEO_MIN_SECONDS = 4 * 60
MEDIA_SUBDIRECTORY = "broadcast_videos"
TRANSCRIPT_REMOVAL_WORDS = {
    "um",
    "uh",
    "like",
    "you know",
    "so",
    "basically",
    "literally",
    "okay",
    "right",
}


def _safe_numeric_value(segment, keys, fallback=0):
    for key in keys:
        value = segment.get(key)
        if value is None:
            continue
        try:
            number = float(value)
        except (ValueError, TypeError):
            continue
        return max(0, int(round(number)))
    return fallback


def _sanitize_transcript_segments(raw_segments: list[dict]) -> list[dict]:
    cleaned_segments: list[dict] = []
    if not isinstance(raw_segments, list):
        return cleaned_segments
    for entry in raw_segments:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        text = re.sub(r"\s+", " ", text)
        for word in TRANSCRIPT_REMOVAL_WORDS:
            text = re.sub(rf"\b{re.escape(word)}\b", "", text, flags=re.IGNORECASE)
        text = text.strip()
        if not text:
            continue
        start = _safe_numeric_value(entry, ["start_seconds", "start", "start_time"], fallback=0)
        end = _safe_numeric_value(entry, ["end_seconds", "end", "end_time"], fallback=start)
        if end < start:
            end = start
        cleaned_segments.append({"text": text, "start_seconds": start, "end_seconds": end})
    return cleaned_segments


def _probe_video_duration(path: str) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(result.stdout.strip())
    except Exception:  # pragma: no cover
        return 0.0


def _collect_profile_broadcast_stats(profile_ids: list[str]) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for pid in profile_ids:
        stats[pid] = {"broadcast_count": 0, "last_broadcast_at": None, "last_broadcast_title": None}
    if not profile_ids:
        return stats

    items = BroadcastItem.objects.filter(
        source_type=BroadcastSourceType.EDUCATION_COURSE,
        is_deleted=False,
        metadata__education_profile_id__in=profile_ids,
    ).order_by("-broadcasted_at")
    for item in items:
        metadata = item.metadata or {}
        pid = str(metadata.get("education_profile_id") or "")
        if not pid or pid not in stats:
            continue
        entry = stats[pid]
        entry["broadcast_count"] += 1
        if not entry["last_broadcast_at"] or item.broadcasted_at and item.broadcasted_at > entry["last_broadcast_at"]:
            entry["last_broadcast_at"] = item.broadcasted_at
            entry["last_broadcast_title"] = str(metadata.get("title") or metadata.get("summary") or "")
    return stats


def _serialize_education_profiles(user):
    qs = (
        EducationProfile.objects.filter(user=user)
        .prefetch_related(
            "courses",
            "modules",
            "roles__assignments__user",
        )
        .order_by("-is_default", "-updated_at")
    )
    serializer = EducationProfileSerializer(qs, many=True)
    payload = serializer.data
    profile_ids = [str(entry.get("id")) for entry in payload if entry.get("id")]
    stats = _collect_profile_broadcast_stats(profile_ids)
    for entry in payload:
        pid = str(entry.get("id"))
        entry["analytics"] = stats.get(pid, {"broadcast_count": 0, "last_broadcast_at": None, "last_broadcast_title": None})
    return payload


def _build_education_summary(serialized_profile: dict[str, Any]) -> dict[str, Any]:
    if not serialized_profile:
        return {}
    return {
        "profile_name": serialized_profile.get("name"),
        "description": serialized_profile.get("description"),
        "profile_type": serialized_profile.get("profile_type"),
        "course_count": len(serialized_profile.get("courses", [])),
        "module_count": len(serialized_profile.get("modules", [])),
        "role_count": len(serialized_profile.get("roles", [])),
        "courses": serialized_profile.get("courses", []),
        "modules": serialized_profile.get("modules", []),
        "roles": serialized_profile.get("roles", []),
        "updated_at": serialized_profile.get("updated_at"),
        "created_at": serialized_profile.get("created_at"),
    }


def _serialize_health_institution_from_row(row: BroadcastHealthInstitution) -> dict:
    metadata = dict(row.metadata) if isinstance(row.metadata, dict) else {}
    blocked_medium_ids = _get_blocked_service_medium_ids()

    owner_contact = {
        "name": row.owner_name or "",
        "phone": row.owner_phone or "",
        "email": row.owner_email or "",
        "userId": str(row.owner_user_id or ""),
        "user_id": str(row.owner_user_id or ""),
    }

    members = []
    for member_row in row.member_rows.all().order_by("created_at"):
        member = {
            "id": member_row.member_uid or str(member_row.id),
            "name": member_row.name or "Worker",
            "role": member_row.role or "staff",
            "phone": member_row.phone or "",
            "email": member_row.email or "",
            "userId": str(member_row.user_id or ""),
            "user_id": str(member_row.user_id or ""),
        }
        if isinstance(member_row.metadata, dict):
            for key, value in member_row.metadata.items():
                if key not in member:
                    member[key] = _safe_json_value(value)
        members.append(member)

    services = []
    for service_row in row.service_rows.all().order_by("created_at"):
        medium_pairs, had_mediums, removed_any = filter_service_medium_pairs(
            service_row.medium_ids,
            service_row.medium_names,
            blocked_medium_ids=blocked_medium_ids,
        )
        if should_drop_service_after_medium_cleanup(
            had_mediums=had_mediums,
            removed_any=removed_any,
            remaining_pairs=medium_pairs,
        ):
            continue
        service = {
            "id": service_row.service_uid or str(service_row.id),
            "name": service_row.name,
            "description": service_row.description or "",
            "active": bool(service_row.active),
        }
        if service_row.base_price_cents is not None:
            service["basePriceCents"] = int(service_row.base_price_cents)
        medium_ids = [medium_id for medium_id, _medium_name in medium_pairs if medium_id]
        if medium_ids:
            service["medium_ids"] = medium_ids
            service["mediumIds"] = medium_ids
        medium_names = [medium_name for _medium_id, medium_name in medium_pairs if medium_name]
        if medium_names:
            service["medium_names"] = medium_names
            service["mediumNames"] = medium_names
        if isinstance(service_row.metadata, dict):
            for key, value in service_row.metadata.items():
                if key not in service:
                    service[key] = _safe_json_value(value)
        services.append(service)

    payload = dict(metadata)
    payload.update({
        "id": row.institution_uid,
        "institution_id": row.institution_uid,
        "institutionId": row.institution_uid,
        "type": row.institution_type,
        "name": row.name,
        "owner_contact": owner_contact,
        "ownerContact": owner_contact,
        "members_target_count": row.members_target_count,
        "membersTargetCount": row.members_target_count,
        "membership_open": bool(row.membership_open),
        "membershipOpen": bool(row.membership_open),
        "membership_discount_pct": int(row.membership_discount_pct),
        "membershipDiscountPct": int(row.membership_discount_pct),
        "membership_settings": {
            "open": bool(row.membership_open),
            "discountPercent": int(row.membership_discount_pct),
        },
        "membershipSettings": {
            "open": bool(row.membership_open),
            "discountPercent": int(row.membership_discount_pct),
        },
        "members": members,
        "employees": [
            {
                "id": _ensure_entry_id(member.get("id"), "worker"),
                "name": str(member.get("name") or "Worker"),
                "role": str(member.get("role") or "staff"),
                "phone": str(member.get("phone") or ""),
                "email": str(member.get("email") or ""),
                "user_id": str(member.get("userId") or member.get("user_id") or ""),
            }
            for member in members
        ],
        "services": services,
    })

    return _sanitize_institution(payload) or payload


def _build_health_institutions_from_tables(health_profile: BroadcastHealthProfile | None) -> list[dict]:
    if not health_profile:
        return []

    rows = (
        health_profile.institution_rows
        .all()
        .prefetch_related("member_rows", "service_rows")
        .order_by("created_at")
    )
    institutions: list[dict] = []
    for row in rows:
        parsed = _serialize_health_institution_from_row(row)
        if parsed:
            institutions.append(parsed)
    return _ensure_institution_data(institutions)


def _sync_health_profile_tables_from_payload(health_profile: BroadcastHealthProfile, health_payload: dict[str, Any]) -> list[dict]:
    institutions = _ensure_institution_data((health_payload or {}).get("institutions") or [])
    blocked_medium_ids = _get_blocked_service_medium_ids()
    existing_rows = {
        str(row.institution_uid): row
        for row in health_profile.institution_rows.all().prefetch_related("member_rows", "service_rows")
    }
    keep_institution_ids: set[str] = set()

    with transaction.atomic():
        for institution in institutions:
            institution_uid = str(
                institution.get("id")
                or institution.get("institution_id")
                or institution.get("institutionId")
                or ""
            ).strip()
            if not institution_uid:
                institution_uid = _ensure_entry_id(None, "inst")
            if institution_uid in keep_institution_ids:
                continue
            keep_institution_ids.add(institution_uid)

            owner_contact = institution.get("owner_contact") if isinstance(institution.get("owner_contact"), dict) else {}
            if not owner_contact and isinstance(institution.get("ownerContact"), dict):
                owner_contact = institution.get("ownerContact")

            owner_user_id = str(owner_contact.get("userId") or owner_contact.get("user_id") or "").strip()
            owner_user = User.objects.filter(id=owner_user_id).first() if owner_user_id else None
            membership = _sanitize_membership_settings(institution)

            institution_metadata = dict(institution)
            for key in (
                "id",
                "institution_id",
                "institutionId",
                "type",
                "name",
                "employees",
                "members",
                "services",
                "owner_contact",
                "ownerContact",
                "members_target_count",
                "membersTargetCount",
                "membership_open",
                "membershipOpen",
                "membership_discount_pct",
                "membershipDiscountPct",
                "membership_settings",
                "membershipSettings",
            ):
                institution_metadata.pop(key, None)

            defaults = {
                "institution_type": _normalize_health_institution_type(institution.get("type")),
                "name": str(institution.get("name") or "Health Institution").strip() or "Health Institution",
                "owner_user": owner_user,
                "owner_name": str(owner_contact.get("name") or "").strip(),
                "owner_phone": str(owner_contact.get("phone") or "").strip(),
                "owner_email": str(owner_contact.get("email") or "").strip(),
                "members_target_count": _parse_positive_int(
                    institution.get("members_target_count") or institution.get("membersTargetCount"),
                    1,
                ),
                "membership_open": bool(membership.get("open")),
                "membership_discount_pct": int(membership.get("discountPercent") or 10),
                "metadata": _safe_json_value(institution_metadata),
            }

            institution_row = existing_rows.get(institution_uid)
            if institution_row:
                for field, value in defaults.items():
                    setattr(institution_row, field, value)
                institution_row.save()
            else:
                institution_row = BroadcastHealthInstitution.objects.create(
                    health_profile=health_profile,
                    institution_uid=institution_uid,
                    **defaults,
                )

            raw_members = institution.get("members") if isinstance(institution.get("members"), list) else []
            raw_employees = institution.get("employees") if isinstance(institution.get("employees"), list) else []
            normalized_members = _sanitize_members(
                raw_members,
                raw_employees,
                str(institution.get("name") or "Institution"),
                owner_contact,
            )
            existing_members = {
                str(member.member_uid): member
                for member in institution_row.member_rows.all()
            }
            keep_member_ids: set[str] = set()
            for member in normalized_members:
                member_uid = _ensure_entry_id(member.get("id"), "worker")
                if member_uid in keep_member_ids:
                    continue
                keep_member_ids.add(member_uid)
                member_user_id = str(member.get("userId") or member.get("user_id") or "").strip()
                member_user = User.objects.filter(id=member_user_id).first() if member_user_id else None
                member_metadata = dict(member)
                for key in ("id", "name", "role", "phone", "email", "userId", "user_id"):
                    member_metadata.pop(key, None)
                member_defaults = {
                    "name": str(member.get("name") or "Worker"),
                    "role": _normalize_member_role(member.get("role")),
                    "phone": str(member.get("phone") or ""),
                    "email": str(member.get("email") or ""),
                    "user": member_user,
                    "metadata": _safe_json_value(member_metadata),
                }
                member_row = existing_members.get(member_uid)
                if member_row:
                    for field, value in member_defaults.items():
                        setattr(member_row, field, value)
                    member_row.save()
                else:
                    BroadcastHealthInstitutionMember.objects.create(
                        institution=institution_row,
                        member_uid=member_uid,
                        **member_defaults,
                    )
            institution_row.member_rows.exclude(member_uid__in=keep_member_ids).delete()

            normalized_services = _sanitize_services(
                institution.get("services"),
                blocked_medium_ids=blocked_medium_ids,
            )
            existing_services = {
                str(service.service_uid): service
                for service in institution_row.service_rows.all()
            }
            keep_service_ids: set[str] = set()
            for index, service in enumerate(normalized_services):
                service_uid = str(service.get("id") or service.get("service_id") or "").strip()
                if not service_uid:
                    service_uid = f"service_{index + 1}"
                if service_uid in keep_service_ids:
                    continue
                keep_service_ids.add(service_uid)

                service_metadata = dict(service)
                for key in (
                    "id",
                    "service_id",
                    "name",
                    "description",
                    "active",
                    "basePriceCents",
                    "medium_ids",
                    "mediumIds",
                    "medium_names",
                    "mediumNames",
                ):
                    service_metadata.pop(key, None)

                base_price_cents = service.get("basePriceCents")
                parsed_cents = None
                if base_price_cents is not None:
                    try:
                        parsed_cents = int(float(base_price_cents))
                    except (TypeError, ValueError):
                        parsed_cents = None

                medium_ids = [str(item or "").strip() for item in (service.get("medium_ids") or service.get("mediumIds") or []) if str(item or "").strip()]
                medium_names = [str(item or "").strip() for item in (service.get("medium_names") or service.get("mediumNames") or []) if str(item or "").strip()]

                service_defaults = {
                    "name": str(service.get("name") or f"Service {index + 1}").strip() or f"Service {index + 1}",
                    "description": str(service.get("description") or "").strip(),
                    "active": bool(service.get("active", True)),
                    "base_price_cents": parsed_cents if parsed_cents is not None and parsed_cents >= 0 else None,
                    "medium_ids": medium_ids,
                    "medium_names": medium_names,
                    "metadata": _safe_json_value(service_metadata),
                }
                service_row = existing_services.get(service_uid)
                if service_row:
                    for field, value in service_defaults.items():
                        setattr(service_row, field, value)
                    service_row.save()
                else:
                    BroadcastHealthInstitutionService.objects.create(
                        institution=institution_row,
                        service_uid=service_uid,
                        **service_defaults,
                    )
            institution_row.service_rows.exclude(service_uid__in=keep_service_ids).delete()

        health_profile.institution_rows.exclude(institution_uid__in=keep_institution_ids).delete()

    return institutions


def _compose_health_profile_payload(
    health_profile: BroadcastHealthProfile | None,
    base_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {}
    if health_profile and isinstance(health_profile.payload, dict):
        payload.update(health_profile.payload)
    if isinstance(base_payload, dict):
        payload.update(base_payload)

    table_institutions = _build_health_institutions_from_tables(health_profile)
    if table_institutions:
        payload["institutions"] = table_institutions
    else:
        payload["institutions"] = _ensure_institution_data(payload.get("institutions") or [])

    return _ensure_health_profile_structure(payload)


def _load_user_profiles(user, include_member_institutions: bool = False):
    prefs = dict(getattr(user, "preferences", {}) or {})
    profiles = dict(prefs.get("profiles") or {})
    account_profile, _ = Profile.objects.get_or_create(user=user)

    feed_profile = BroadcastFeedProfile.objects.filter(profile=account_profile).first()
    if feed_profile and isinstance(feed_profile.payload, dict):
        profiles["broadcast_feed"] = feed_profile.payload
    elif isinstance(profiles.get("broadcast_feed"), dict):
        BroadcastFeedProfile.objects.update_or_create(
            profile=account_profile,
            defaults={"payload": profiles["broadcast_feed"]},
        )

    health_profile = BroadcastHealthProfile.objects.filter(profile=account_profile).first()
    if not health_profile and isinstance(profiles.get("health"), dict):
        health_profile = BroadcastHealthProfile.objects.create(
            profile=account_profile,
            payload=_prepare_health_profile_for_storage(profiles.get("health") or {}),
        )
        _sync_health_profile_tables_from_payload(health_profile, profiles.get("health") or {})

    health_payload = _compose_health_profile_payload(
        health_profile,
        profiles.get("health") if isinstance(profiles.get("health"), dict) else None,
    )

    local_institutions = _ensure_institution_data(health_payload.get("institutions") or [])
    owned_institutions = [inst for inst in local_institutions if _is_user_owner_of_institution(user, inst)]
    authoritative_owned = _collect_owner_accessible_health_institutions(user)
    if authoritative_owned:
        owned_institutions = authoritative_owned
    if health_payload:
        health_payload["institutions"] = owned_institutions

    if include_member_institutions:
        member_institutions = _collect_member_accessible_health_institutions(
            user,
            exclude_owner_user_id=str(getattr(user, "id", "") or ""),
        )
        merged_institutions = _merge_health_institutions(owned_institutions, member_institutions)
        merged_health = dict(health_payload or {})
        merged_health["institutions"] = merged_institutions
        merged_health["owned_institutions"] = owned_institutions
        merged_health["member_institutions"] = member_institutions
        has_owner_profile = len(owned_institutions) > 0
        merged_health["has_owner_profile"] = has_owner_profile
        merged_health["hasOwnerProfile"] = has_owner_profile
        merged_health["viewer_access_mode"] = "owner" if has_owner_profile else "member"
        profiles["health"] = _ensure_health_profile_structure(merged_health)
    elif health_payload:
        profiles["health"] = _ensure_health_profile_structure(health_payload)

    market_profile = BroadcastMarketProfile.objects.filter(profile=account_profile).first()
    if market_profile and isinstance(market_profile.payload, dict):
        profiles["market"] = market_profile.payload
    elif isinstance(profiles.get("market"), dict):
        BroadcastMarketProfile.objects.update_or_create(
            profile=account_profile,
            defaults={"payload": profiles["market"]},
        )

    education_profiles = _serialize_education_profiles(user)
    profiles["education_profiles"] = education_profiles
    if education_profiles:
        default_profile = next((p for p in education_profiles if p.get("is_default")), education_profiles[0])
        profiles["education"] = _build_education_summary(default_profile)
    return profiles


def _prepare_health_profile_for_storage(health_payload: dict[str, Any]) -> dict[str, Any]:
    persisted = dict(health_payload or {})
    for key in (
        "institutions",
        "employees_total",
        "owned_institutions",
        "ownedInstitutions",
        "member_institutions",
        "memberInstitutions",
        "viewer_access_mode",
        "viewerAccessMode",
        "has_owner_profile",
        "hasOwnerProfile",
    ):
        persisted.pop(key, None)
    return _safe_json_value(persisted) if isinstance(_safe_json_value(persisted), dict) else {}


def _save_user_profiles(user, profiles):
    persisted_profiles = dict(profiles or {})
    account_profile, _ = Profile.objects.get_or_create(user=user)

    feed_payload = persisted_profiles.get("broadcast_feed")
    if isinstance(feed_payload, dict):
        BroadcastFeedProfile.objects.update_or_create(
            profile=account_profile,
            defaults={"payload": feed_payload},
        )

    health_payload = persisted_profiles.get("health")
    if isinstance(health_payload, dict):
        health_profile, _ = BroadcastHealthProfile.objects.update_or_create(
            profile=account_profile,
            defaults={"payload": _prepare_health_profile_for_storage(health_payload)},
        )
        _sync_health_profile_tables_from_payload(health_profile, health_payload)
        persisted_profiles["health"] = _compose_health_profile_payload(health_profile)

    market_payload = persisted_profiles.get("market")
    if isinstance(market_payload, dict):
        BroadcastMarketProfile.objects.update_or_create(
            profile=account_profile,
            defaults={"payload": market_payload},
        )

    prefs = dict(getattr(user, "preferences", {}) or {})
    profile_cache = dict(persisted_profiles)
    if isinstance(profile_cache.get("health"), dict):
        slim_health = dict(profile_cache["health"])
        slim_health["institutions"] = []
        slim_health["employees_total"] = 0
        for key in (
            "owned_institutions",
            "ownedInstitutions",
            "member_institutions",
            "memberInstitutions",
        ):
            slim_health.pop(key, None)
        profile_cache["health"] = slim_health
    prefs["profiles"] = profile_cache
    user.preferences = prefs
    user.save(update_fields=["preferences"])

def _delete_user_broadcast(user, entery_id):
    braodcast_item = BroadcastItem.objects.filter(source_id = entery_id, broadcasted_by = user)
    if braodcast_item:
        return braodcast_item.delete()
    return


def _store_upload(file_obj, user=None) -> tuple[str, int]:
    if user and getattr(user, "is_authenticated", False):
        features = get_user_tier_features(user)
        limit_mb = normalize_limit_value(features.get("media_storage_mb"), default=None)
        if limit_mb is not None:
            limit_bytes = int(limit_mb) * 1024 * 1024
            if file_obj.size > limit_bytes:
                raise ValidationError({"detail": "Media file exceeds your tier storage limit."})
    media_root = getattr(settings, "MEDIA_ROOT", "media")
    rel_name = f"{uuid.uuid4().hex}{os.path.splitext(file_obj.name or '')[1] or '.mp4'}"
    rel_path = os.path.join(MEDIA_SUBDIRECTORY, rel_name)
    abs_path = os.path.join(media_root, rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    size = 0
    with open(abs_path, "wb") as destination:
        for chunk in file_obj.chunks():
            destination.write(chunk)
            size += len(chunk)
    return rel_path, size


HEALTH_INSTITUTION_TYPES = {
    "clinic",
    "hospital",
    "lab",
    "wellness_center",
    "pharmacy",
    "diagnostics",
}


def _ensure_entry_id(value: str | None, prefix: str) -> str:
    candidate = str(value).strip() if value else ""
    if candidate:
        return candidate
    return f"{prefix}-{uuid.uuid4().hex}"


def _sanitize_profile_course_entry(entry: object | None) -> dict | None:
    if not isinstance(entry, dict):
        return None
    title = str(entry.get("title") or "").strip()
    if not title:
        return None
    summary = str(entry.get("summary") or entry.get("description") or "").strip()
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    return {"title": title, "summary": summary, "metadata": metadata}


def _sanitize_profile_module_entry(entry: object | None) -> dict | None:
    if not isinstance(entry, dict):
        return None
    title = str(entry.get("title") or "").strip()
    if not title:
        return None
    summary = str(entry.get("summary") or "").strip()
    resource_url = entry.get("resource_url") or entry.get("resourceUrl")
    return {"title": title, "summary": summary, "resource_url": resource_url}


def _sanitize_profile_role_entry(entry: object | None) -> dict | None:
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("name") or "").strip()
    if not name:
        return None
    permissions = entry.get("permissions") or entry.get("permission_list") or []
    if not isinstance(permissions, list):
        permissions = []
    assignments = entry.get("assignments") or entry.get("collaborators") or []
    return {"name": name, "permissions": permissions, "assignments": assignments}


EDUCATION_PROFILE_ROLE_PERMISSIONS = [
    {
        "id": "manage_courses",
        "label": "Manage courses & modules",
        "description": "Create, edit, and remove courses or modules inside the profile.",
    },
    {
        "id": "manage_roles",
        "label": "Manage roles & permissions",
        "description": "Create admin roles and adjust their permissions.",
    },
    {
        "id": "manage_members",
        "label": "Manage members",
        "description": "Invite or remove collaborators who can access this profile.",
    },
    {
        "id": "broadcast_profile",
        "label": "Broadcast profile",
        "description": "Push the entire profile to the broadcast education feed.",
    },
]


def _reset_default_education_profile(user):
    EducationProfile.objects.filter(user=user).update(is_default=False)


def _ensure_default_education_profile(user) -> EducationProfile:
    account_profile, _ = Profile.objects.get_or_create(user=user)
    default = EducationProfile.objects.filter(user=user, is_default=True).first()
    if default:
        if not default.profile_id:
            default.profile = account_profile
            default.save(update_fields=["profile"])
        return default
    first = EducationProfile.objects.filter(user=user).order_by("created_at").first()
    if first:
        if not first.profile_id:
            first.profile = account_profile
        first.is_default = True
        first.save(update_fields=["is_default", "profile"])
        return first
    return EducationProfile.objects.create(user=user, profile=account_profile, name="Education Profile")


def _replace_profile_courses(profile: EducationProfile, courses: list[dict | None]):
    sanitized: list[dict] = []
    for course in courses:
        parsed = _sanitize_profile_course_entry(course)
        if parsed:
            sanitized.append(parsed)
    EducationProfileCourse.objects.filter(profile=profile).delete()
    objs = [
        EducationProfileCourse(profile=profile, title=item["title"], summary=item["summary"], metadata=item["metadata"])
        for item in sanitized
    ]
    EducationProfileCourse.objects.bulk_create(objs)


def _replace_profile_modules(profile: EducationProfile, modules: list[dict | None]):
    sanitized: list[dict] = []
    for mod in modules:
        parsed = _sanitize_profile_module_entry(mod)
        if parsed:
            sanitized.append(parsed)
    EducationProfileModule.objects.filter(profile=profile).delete()
    objs = [
        EducationProfileModule(
            profile=profile,
            title=item["title"],
            summary=item["summary"],
            resource_url=item.get("resource_url"),
        )
        for item in sanitized
    ]
    EducationProfileModule.objects.bulk_create(objs)


def _replace_profile_roles(profile: EducationProfile, roles: list[dict | None]):
    sanitized: list[dict] = []
    for role in roles:
        parsed = _sanitize_profile_role_entry(role)
        if parsed:
            sanitized.append(parsed)
    EducationProfileRole.objects.filter(profile=profile).delete()
    for entry in sanitized:
        permissions = [str(p) for p in entry["permissions"] if isinstance(p, (str, int))]
        role = EducationProfileRole.objects.create(profile=profile, name=entry["name"], permissions=permissions)
        assignment_ids = [candidate for candidate in entry["assignments"] if candidate]
        for assignment_id in assignment_ids:
            try:
                collaborator = User.objects.get(id=assignment_id)
            except User.DoesNotExist:
                continue
        EducationProfileRoleAssignment.objects.create(role=role, user=collaborator)


def _get_education_profile_or_404(user, profile_id: str) -> EducationProfile:
    return get_object_or_404(EducationProfile, user=user, id=profile_id)

def _normalize_health_institution_type(value: str | None) -> str:
    normalized = (value or "").strip().lower().replace(" ", "_")
    return normalized if normalized in HEALTH_INSTITUTION_TYPES else "clinic"


def _parse_positive_int(value: object | None, fallback: int) -> int:
    try:
        number = int(value)
        return max(fallback, number)
    except (TypeError, ValueError):
        return fallback


def _sanitize_employee(worker: object | None, name_hint: str | None) -> dict:
    data = worker if isinstance(worker, dict) else {}
    label = str(data.get("name") or name_hint or "").strip()
    if not label:
        label = f"{name_hint or 'Worker'}"
    return {
        "id": _ensure_entry_id(data.get("id"), "worker"),
        "name": label,
        "role": str(data.get("role") or "Care worker"),
    }


def _sanitize_employees(raw_employees: object | None, institution_name: str) -> list[dict]:
    if isinstance(raw_employees, list):
        employees = [
            _sanitize_employee(emp, f"{institution_name} Worker {idx + 1}")
            for idx, emp in enumerate(raw_employees)
            if isinstance(emp, dict)
        ]
        return employees
    count = _parse_positive_int(raw_employees, 1)
    return [
        {
            "id": _ensure_entry_id(None, "worker"),
            "name": f"{institution_name} Worker {idx + 1}",
            "role": "Care worker",
        }
        for idx in range(count)
    ]


HEALTH_MEMBER_ROLE_PERMISSIONS = {
    "owner": {"analytics": True, "schedules": True, "services": True, "financial": True, "compliance": True, "members": True},
    "admin": {"analytics": True, "schedules": True, "services": True, "financial": True, "compliance": True, "members": True},
    "manager": {"analytics": True, "schedules": True, "services": True, "financial": False, "compliance": False, "members": True},
    "staff": {"analytics": False, "schedules": True, "services": True, "financial": False, "compliance": False, "members": False},
    "analyst": {"analytics": True, "schedules": False, "services": False, "financial": True, "compliance": True, "members": False},
    "member": {"analytics": False, "schedules": False, "services": False, "financial": False, "compliance": False, "members": False},
    "unassigned": {"analytics": False, "schedules": False, "services": False, "financial": False, "compliance": False, "members": False},
}

HEALTH_MEMBER_ROLE_RANK = {
    "member": 0,
    "unassigned": 0,
    "staff": 1,
    "analyst": 1,
    "manager": 2,
    "admin": 3,
    "owner": 4,
}


def _normalize_member_role(value: object | None) -> str:
    role = str(value or "").strip().lower()
    return role if role in HEALTH_MEMBER_ROLE_PERMISSIONS else "staff"


def _least_privileged_member_role(roles: list[str]) -> str:
    normalized = [_normalize_member_role(role) for role in roles if role is not None]
    if not normalized:
        return "staff"
    return min(normalized, key=lambda value: HEALTH_MEMBER_ROLE_RANK.get(value, 0))


def _sanitize_owner_contact(raw: object | None) -> dict:
    data = raw if isinstance(raw, dict) else {}
    user_id = data.get("userId") or data.get("user_id") or data.get("id")
    name = str(data.get("name") or data.get("display_name") or "").strip()
    phone = str(data.get("phone") or "").strip()
    email = str(data.get("email") or "").strip()
    return {
        "userId": str(user_id) if user_id is not None and str(user_id).strip() else "",
        "name": name,
        "phone": phone,
        "email": email,
    }


def _sanitize_member(entry: object | None, index: int, institution_name: str) -> dict | None:
    if not isinstance(entry, dict):
        return None

    user_id = entry.get("userId") or entry.get("user_id") or entry.get("user", {}).get("id") if isinstance(entry.get("user"), dict) else entry.get("userId") or entry.get("user_id")
    phone = str(entry.get("phone") or (entry.get("user", {}).get("phone") if isinstance(entry.get("user"), dict) else "") or "").strip()
    email = str(entry.get("email") or (entry.get("user", {}).get("email") if isinstance(entry.get("user"), dict) else "") or "").strip()
    name = str(entry.get("name") or entry.get("display_name") or f"{institution_name} Member {index + 1}").strip()
    if not name:
        return None

    role = _normalize_member_role(entry.get("role") or entry.get("member_role"))
    permissions = HEALTH_MEMBER_ROLE_PERMISSIONS.get(role, HEALTH_MEMBER_ROLE_PERMISSIONS["staff"])

    return {
        "id": _ensure_entry_id(entry.get("id") or user_id, "member"),
        "userId": str(user_id) if user_id is not None and str(user_id).strip() else "",
        "name": name,
        "phone": phone,
        "email": email,
        "role": role,
        "source": str(entry.get("source") or "owner_added"),
        "permissions": permissions,
    }


def _sanitize_members(raw_members: object | None, employees: list[dict], institution_name: str, owner_contact: dict) -> list[dict]:
    rows: list[dict] = []

    if isinstance(raw_members, list):
        for idx, item in enumerate(raw_members):
            member = _sanitize_member(item, idx, institution_name)
            if member:
                rows.append(member)

    if not rows:
        for idx, emp in enumerate(employees):
            rows.append({
                "id": _ensure_entry_id(emp.get("id"), "member"),
                "userId": "",
                "name": str(emp.get("name") or f"{institution_name} Member {idx + 1}").strip(),
                "phone": "",
                "email": "",
                "role": _normalize_member_role(emp.get("role")),
                "source": "owner_added",
                "permissions": HEALTH_MEMBER_ROLE_PERMISSIONS.get(_normalize_member_role(emp.get("role")), HEALTH_MEMBER_ROLE_PERMISSIONS["staff"]),
            })

    deduped: list[dict] = []
    seen = set()
    for item in rows:
        keys = [
            f"uid:{item.get('userId')}" if item.get('userId') else "",
            f"id:{item.get('id')}" if item.get('id') else "",
            f"phone:{item.get('phone')}" if item.get('phone') else "",
            f"name:{str(item.get('name') or '').lower()}" if item.get('name') else "",
        ]
        keys = [k for k in keys if k]
        if any(k in seen for k in keys):
            continue
        for key in keys:
            seen.add(key)
        deduped.append(item)

    owner_user_id = owner_contact.get("userId")
    owner_name = owner_contact.get("name") or f"{institution_name} Owner"
    owner_phone = owner_contact.get("phone") or ""
    owner_email = owner_contact.get("email") or ""

    if owner_user_id:
        has_owner = any(str(m.get("userId") or "") == str(owner_user_id) for m in deduped)
        if not has_owner:
            deduped.insert(0, {
                "id": _ensure_entry_id(owner_user_id, "member"),
                "userId": str(owner_user_id),
                "name": str(owner_name),
                "phone": str(owner_phone),
                "email": str(owner_email),
                "role": "owner",
                "source": "owner_added",
                "permissions": HEALTH_MEMBER_ROLE_PERMISSIONS["owner"],
            })

    owner_count = sum(1 for member in deduped if _normalize_member_role(member.get("role")) == "owner")
    if owner_count == 0 and deduped:
        deduped[0]["role"] = "owner"
        deduped[0]["permissions"] = HEALTH_MEMBER_ROLE_PERMISSIONS["owner"]

    return deduped


def _sanitize_member_audit_logs(raw_logs: object | None) -> list[dict]:
    if not isinstance(raw_logs, list):
        return []
    logs: list[dict] = []
    for index, item in enumerate(raw_logs[:250]):
        if not isinstance(item, dict):
            continue
        logs.append({
            "id": str(item.get("id") or _ensure_entry_id(None, "audit")),
            "at": str(item.get("at") or timezone.now().isoformat()),
            "actorUserId": str(item.get("actorUserId") or item.get("actor_user_id") or ""),
            "action": str(item.get("action") or f"member.event_{index + 1}"),
            "memberName": str(item.get("memberName") or item.get("member_name") or ""),
            "fromRole": str(item.get("fromRole") or item.get("from_role") or ""),
            "toRole": str(item.get("toRole") or item.get("to_role") or ""),
        })
    return logs


def _safe_json_value(value: object):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_safe_json_value(item) for item in value]
    if isinstance(value, dict):
        safe_obj = {}
        for key, val in value.items():
            if not isinstance(key, str):
                continue
            safe_obj[key] = _safe_json_value(val)
        return safe_obj
    return str(value)


def _sanitize_service_entry(
    entry: object | None,
    index: int,
    *,
    blocked_medium_ids: set[str] | None = None,
) -> dict | None:
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("name") or entry.get("title") or "").strip()
    if not name:
        return None
    service_id = str(entry.get("id") or entry.get("service_id") or f"service_{index + 1}").strip()
    if not service_id:
        service_id = f"service_{index + 1}"
    description = str(entry.get("description") or entry.get("summary") or "").strip()
    row = {
        "id": service_id,
        "name": name,
        "description": description,
        "active": bool(entry.get("active", True)),
    }

    base_price_cents = entry.get("basePriceCents", entry.get("base_price_cents"))
    base_price = entry.get("base_price")

    parsed_cents = None
    try:
        if base_price_cents is not None:
            parsed_cents = int(float(base_price_cents))
        elif base_price is not None:
            parsed_cents = int(round(float(base_price) * 100))
    except (TypeError, ValueError):
        parsed_cents = None

    if isinstance(parsed_cents, int) and parsed_cents >= 0:
        row["basePriceCents"] = parsed_cents

    medium_ids: list[str] = []
    raw_medium_ids = entry.get("medium_ids") or entry.get("mediumIds")
    if isinstance(raw_medium_ids, list):
        medium_ids.extend(str(item or "").strip() for item in raw_medium_ids if str(item or "").strip())

    medium_names: list[str] = []
    raw_medium_names = entry.get("medium_names") or entry.get("mediumNames")
    if isinstance(raw_medium_names, list):
        medium_names.extend(str(item or "").strip() for item in raw_medium_names if str(item or "").strip())

    raw_medium_links = entry.get("medium_links") or entry.get("mediumLinks")
    if isinstance(raw_medium_links, list):
        for link in raw_medium_links:
            if not isinstance(link, dict):
                continue
            medium = link.get("medium") if isinstance(link.get("medium"), dict) else {}
            medium_id = str(link.get("medium_id") or link.get("mediumId") or medium.get("id") or "").strip()
            medium_name = str(medium.get("name") or link.get("name") or "").strip()
            if medium_id:
                medium_ids.append(medium_id)
            if medium_name:
                medium_names.append(medium_name)

    medium_pairs, had_mediums, removed_any = filter_service_medium_pairs(
        medium_ids,
        medium_names,
        blocked_medium_ids=blocked_medium_ids,
    )
    if should_drop_service_after_medium_cleanup(
        had_mediums=had_mediums,
        removed_any=removed_any,
        remaining_pairs=medium_pairs,
    ):
        return None

    filtered_ids = [medium_id for medium_id, _medium_name in medium_pairs if medium_id]
    filtered_names = [medium_name for _medium_id, medium_name in medium_pairs if medium_name]

    if filtered_ids:
        row["medium_ids"] = filtered_ids
        row["mediumIds"] = filtered_ids
    if filtered_names:
        row["medium_names"] = filtered_names
        row["mediumNames"] = filtered_names

    return row


def _sanitize_services(
    raw_services: object | None,
    *,
    blocked_medium_ids: set[str] | None = None,
) -> list[dict]:
    if not isinstance(raw_services, list):
        return []
    rows: list[dict] = []
    for idx, item in enumerate(raw_services):
        parsed = _sanitize_service_entry(item, idx, blocked_medium_ids=blocked_medium_ids)
        if parsed:
            rows.append(parsed)
    return rows


def _sanitize_availability(raw: object | None) -> dict:
    if not isinstance(raw, dict):
        return {}

    def _dict_of_strings(value: object | None) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        out: dict[str, str] = {}
        for key, val in value.items():
            if not isinstance(key, str):
                continue
            out[str(key)] = str(val)
        return out

    def _dict_of_string_lists(value: object | None) -> dict[str, list[str]]:
        if not isinstance(value, dict):
            return {}
        out: dict[str, list[str]] = {}
        for key, val in value.items():
            if not isinstance(key, str) or not isinstance(val, list):
                continue
            ids = [str(item).strip() for item in val if str(item).strip()]
            out[str(key)] = ids
        return out

    payload = _safe_json_value(raw)
    if not isinstance(payload, dict):
        payload = {}

    statuses = _dict_of_strings(raw.get("calendar_statuses") or raw.get("calendarStatuses") or raw.get("date_statuses") or raw.get("dateStatuses"))
    if statuses:
        payload["calendar_statuses"] = statuses

    times = _dict_of_strings(raw.get("calendar_times") or raw.get("calendarTimes") or raw.get("date_times") or raw.get("dateTimes"))
    if times:
        payload["calendar_times"] = times

    service_ids = _dict_of_string_lists(raw.get("calendar_service_ids") or raw.get("calendarServiceIds") or raw.get("date_service_ids") or raw.get("dateServiceIds"))
    if service_ids:
        payload["calendar_service_ids"] = service_ids

    return payload


def _sanitize_membership_settings(entry: dict) -> dict:
    settings = entry.get("membership_settings") if isinstance(entry.get("membership_settings"), dict) else {}
    if not settings and isinstance(entry.get("membershipSettings"), dict):
        settings = entry.get("membershipSettings")

    open_value = settings.get("open") if isinstance(settings, dict) else None
    if open_value is None:
        open_value = settings.get("is_open") if isinstance(settings, dict) else None
    if open_value is None:
        open_value = entry.get("membership_open")
    if open_value is None:
        open_value = entry.get("membershipOpen")

    discount_value = settings.get("discountPercent") if isinstance(settings, dict) else None
    if discount_value is None:
        discount_value = settings.get("discount_percent") if isinstance(settings, dict) else None
    if discount_value is None:
        discount_value = entry.get("membership_discount_pct")
    if discount_value is None:
        discount_value = entry.get("membershipDiscountPct")

    try:
        discount = int(float(discount_value if discount_value is not None else 10))
    except (TypeError, ValueError):
        discount = 10
    discount = max(10, min(100, discount))

    return {
        "open": bool(open_value),
        "discountPercent": discount,
    }


def _sanitize_service_ratings(raw_ratings: object | None) -> list[dict]:
    if not isinstance(raw_ratings, list):
        return []

    rows: list[dict] = []
    dedupe = set()
    for index, item in enumerate(raw_ratings):
        if not isinstance(item, dict):
            continue
        service_id = str(item.get("serviceId") or item.get("service_id") or "").strip()
        user_id = str(item.get("userId") or item.get("user_id") or "").strip()
        if not service_id or not user_id:
            continue

        try:
            rating = int(float(item.get("rating") if item.get("rating") is not None else item.get("score")))
        except (TypeError, ValueError):
            continue
        rating = max(1, min(5, rating))

        key = f"{service_id}:{user_id}"
        if key in dedupe:
            continue
        dedupe.add(key)

        rows.append({
            "id": str(item.get("id") or _ensure_entry_id(None, "rating")),
            "serviceId": service_id,
            "service_id": service_id,
            "serviceName": str(item.get("serviceName") or item.get("service_name") or "").strip(),
            "service_name": str(item.get("serviceName") or item.get("service_name") or "").strip(),
            "userId": user_id,
            "user_id": user_id,
            "userName": str(item.get("userName") or item.get("user_name") or "User").strip() or "User",
            "user_name": str(item.get("userName") or item.get("user_name") or "User").strip() or "User",
            "rating": rating,
            "createdAt": str(item.get("createdAt") or item.get("created_at") or timezone.now().isoformat()),
            "created_at": str(item.get("createdAt") or item.get("created_at") or timezone.now().isoformat()),
            "updatedAt": str(item.get("updatedAt") or item.get("updated_at") or timezone.now().isoformat()),
            "updated_at": str(item.get("updatedAt") or item.get("updated_at") or timezone.now().isoformat()),
        })

    return rows


LANDING_BUILDER_KEYS = {
    "sections",
    "section_groups",
    "sectionGroups",
    "landingBackgroundImageUrl",
    "landingBackgroundColorKey",
    "landingLogoUrl",
    "landing_style",
    "landingStyle",
}

LANDING_BUILDER_CONTAINER_KEYS = (
    "landing_page_builder",
    "landingPageBuilder",
    "profile_editor",
    "profileEditor",
    "landing_preview",
    "landingPreview",
)


def _collect_landing_builder_candidate(source: object | None) -> dict:
    if not isinstance(source, dict):
        return {}

    candidate: dict[str, object] = {}
    for key in LANDING_BUILDER_KEYS:
        if key in source:
            candidate[key] = source.get(key)

    for container_key in LANDING_BUILDER_CONTAINER_KEYS:
        nested = source.get(container_key)
        if not isinstance(nested, dict):
            continue
        for key, value in nested.items():
            if key not in candidate:
                candidate[key] = value

    return candidate


def _build_landing_builder_payload(candidate: dict) -> dict:
    if not isinstance(candidate, dict):
        return {}

    sections = candidate.get("sections")
    if not isinstance(sections, list):
        sections = []

    section_groups = candidate.get("section_groups")
    if not isinstance(section_groups, list):
        section_groups = candidate.get("sectionGroups")
    if not isinstance(section_groups, list):
        section_groups = []

    payload = {
        "sections": _safe_json_value(sections),
    }
    if section_groups:
        payload["section_groups"] = _safe_json_value(section_groups)
        payload["sectionGroups"] = payload["section_groups"]

    landing_background_image = str(candidate.get("landingBackgroundImageUrl") or "").strip()
    landing_background_color = str(candidate.get("landingBackgroundColorKey") or "").strip()
    landing_logo = str(candidate.get("landingLogoUrl") or "").strip()

    if landing_background_image:
        payload["landingBackgroundImageUrl"] = landing_background_image
    if landing_background_color:
        payload["landingBackgroundColorKey"] = landing_background_color
    if landing_logo:
        payload["landingLogoUrl"] = landing_logo

    if "landing_style" in candidate:
        payload["landing_style"] = _safe_json_value(candidate.get("landing_style"))
    if "landingStyle" in candidate:
        payload["landingStyle"] = _safe_json_value(candidate.get("landingStyle"))

    return payload


def _apply_landing_builder_updates(profile: dict, updates: object | None) -> dict:
    if not isinstance(profile, dict):
        profile = {}

    existing_candidate = _collect_landing_builder_candidate(profile)
    incoming_candidate = _collect_landing_builder_candidate(updates)
    if not existing_candidate and not incoming_candidate:
        return profile

    merged_candidate = {**existing_candidate, **incoming_candidate}
    normalized = _build_landing_builder_payload(merged_candidate)
    if not normalized:
        return profile

    profile["landing_page_builder"] = normalized
    profile["profile_editor"] = normalized
    profile["landing_preview"] = normalized
    profile["sections"] = normalized.get("sections", [])

    if "section_groups" in normalized:
        profile["section_groups"] = normalized["section_groups"]
        profile["sectionGroups"] = normalized["section_groups"]
    if "landingBackgroundImageUrl" in normalized:
        profile["landingBackgroundImageUrl"] = normalized["landingBackgroundImageUrl"]
    if "landingBackgroundColorKey" in normalized:
        profile["landingBackgroundColorKey"] = normalized["landingBackgroundColorKey"]
    if "landingLogoUrl" in normalized:
        profile["landingLogoUrl"] = normalized["landingLogoUrl"]
    if "landing_style" in normalized:
        profile["landing_style"] = normalized["landing_style"]
    if "landingStyle" in normalized:
        profile["landingStyle"] = normalized["landingStyle"]

    return profile


def _sanitize_institution(entry: object | None) -> dict | None:
    if not isinstance(entry, dict):
        return None
    blocked_medium_ids = _get_blocked_service_medium_ids()
    inst_type = _normalize_health_institution_type(entry.get("type"))
    name = str(entry.get("name") or inst_type.replace("_", " ").title()).strip()
    institution_id = _ensure_entry_id(
        entry.get("id")
        or entry.get("institution_id")
        or entry.get("institutionId")
        or entry.get("legacy_id")
        or entry.get("legacyId"),
        "inst",
    )
    owner_contact_raw = entry.get("owner_contact") or entry.get("ownerContact") or {}
    owner_contact = _sanitize_owner_contact(owner_contact_raw)

    members_target_count = _parse_positive_int(
        entry.get("members_target_count") or entry.get("membersTargetCount") or entry.get("employees"),
        1,
    )

    employees = _sanitize_employees(entry.get("employees"), name)
    members = _sanitize_members(entry.get("members"), employees, name, owner_contact)
    member_audit_logs = _sanitize_member_audit_logs(entry.get("member_audit_logs") or entry.get("memberAuditLogs"))
    membership_settings = _sanitize_membership_settings(entry)

    sanitized = {
        "id": institution_id,
        "institution_id": institution_id,
        "institutionId": institution_id,
        "type": inst_type,
        "name": name,
        "employees": [
            {
                "id": _ensure_entry_id(member.get("id"), "worker"),
                "name": str(member.get("name") or "Worker"),
                "role": str(member.get("role") or "staff"),
                "phone": str(member.get("phone") or ""),
                "email": str(member.get("email") or ""),
                "user_id": str(member.get("userId") or ""),
            }
            for member in members
        ],
        "members": members,
        "owner_contact": owner_contact,
        "ownerContact": owner_contact,
        "members_target_count": members_target_count,
        "membersTargetCount": members_target_count,
        "member_audit_logs": member_audit_logs,
        "memberAuditLogs": member_audit_logs,
        "membership_open": membership_settings["open"],
        "membershipOpen": membership_settings["open"],
        "membership_discount_pct": membership_settings["discountPercent"],
        "membershipDiscountPct": membership_settings["discountPercent"],
        "membership_settings": membership_settings,
        "membershipSettings": membership_settings,
    }

    services = _sanitize_services(entry.get("services"), blocked_medium_ids=blocked_medium_ids)
    if services:
        sanitized["services"] = services

    service_templates = _sanitize_services(
        entry.get("service_templates"),
        blocked_medium_ids=blocked_medium_ids,
    )
    if service_templates:
        sanitized["service_templates"] = service_templates

    service_templates_alias = _sanitize_services(
        entry.get("serviceTemplates"),
        blocked_medium_ids=blocked_medium_ids,
    )
    if service_templates_alias:
        sanitized["serviceTemplates"] = service_templates_alias

    if "availability" in entry:
        sanitized["availability"] = _sanitize_availability(entry.get("availability"))

    ratings = _sanitize_service_ratings(entry.get("service_ratings") or entry.get("serviceRatings"))
    if ratings:
        sanitized["service_ratings"] = ratings
        sanitized["serviceRatings"] = ratings

    if isinstance(entry.get("dashboard"), dict):
        dashboard = _safe_json_value(entry.get("dashboard"))
        if isinstance(dashboard, dict):
            dashboard_services = _sanitize_services(
                dashboard.get("services"),
                blocked_medium_ids=blocked_medium_ids,
            )
            if dashboard_services:
                dashboard["services"] = dashboard_services
            if "availability" in dashboard:
                dashboard["availability"] = _sanitize_availability(dashboard.get("availability"))
            sanitized["dashboard"] = dashboard

    preserved_keys = [
        "sections",
        "section_groups",
        "sectionGroups",
        "profile_editor",
        "profileEditor",
        "landing_preview",
        "landingPreview",
        "landing_style",
        "landingStyle",
        "landingBackgroundImageUrl",
        "landingBackgroundColorKey",
        "landingLogoUrl",
        "contact",
        "socialLinks",
        "certifications",
        "operatingHours",
        "service_ratings",
        "serviceRatings",
        "membership_open",
        "membershipOpen",
        "membership_discount_pct",
        "membershipDiscountPct",
        "membership_settings",
        "membershipSettings",
        "broadcasted_health_cards",
        "broadcastedHealthCards",
        "engine_executions",
        "engineExecutions",
        "service_sessions",
        "serviceSessions",
    ]
    for key in preserved_keys:
        if key in entry:
            sanitized[key] = _safe_json_value(entry.get(key))

    return sanitized


def _ensure_institution_data(entries: list[dict]) -> list[dict]:
    sanitized: list[dict] = []
    for entry in entries:
        inst = _sanitize_institution(entry)
        if inst:
            sanitized.append(inst)
    return sanitized


def _sanitize_product(product: object | None, shop_name: str, index: int) -> dict:
    data = product if isinstance(product, dict) else {}
    name = str(data.get("name") or f"{shop_name} Product {index + 1}").strip()
    if not name:
        name = f"{shop_name} Product {index + 1}"
    sku = str(data.get("sku") or f"{shop_name[:3].upper()}-{index + 1}").strip()
    if not sku:
        sku = f"{shop_name[:3].upper()}-{index + 1}"
    return {
        "id": _ensure_entry_id(data.get("id"), "prod"),
        "name": name,
        "sku": sku,
    }


def _sanitize_products(raw_products: object | None, shop_name: str) -> list[dict]:
    if isinstance(raw_products, list):
        return [
            _sanitize_product(item, shop_name, idx)
            for idx, item in enumerate(raw_products)
        ]
    if isinstance(raw_products, dict):
        return [_sanitize_product(raw_products, shop_name, 0)]
    count = _parse_positive_int(raw_products, 1)
    return [
        {
            "id": _ensure_entry_id(None, "prod"),
            "name": f"{shop_name} Product {idx + 1}",
            "sku": f"{shop_name[:3].upper()}-{idx + 1}",
        }
        for idx in range(count)
    ]


def _parse_positive_float(value: object | None, fallback: float) -> float:
    try:
        number = float(value)
        return max(fallback, number)
    except (TypeError, ValueError):
        return fallback


def _sanitize_service(entry: object | None, shop_name: str, index: int) -> dict | None:
    data = entry if isinstance(entry, dict) else {}
    title = str(data.get("name") or data.get("title") or f"{shop_name} Service {index + 1}").strip()
    if not title:
        return None
    category = str(data.get("category") or "General Services").strip() or "General Services"
    price = _parse_positive_float(data.get("price") or data.get("base_price") or 0, 0)
    duration = str(data.get("duration") or "45 min").strip() or "45 min"
    return {
        "id": _ensure_entry_id(data.get("id"), "svc"),
        "name": title,
        "category": category,
        "price": price,
        "duration": duration,
        "description": str(data.get("description") or "").strip(),
    }


def _sanitize_shop_services(raw_services: object | None, shop_name: str) -> list[dict]:
    if isinstance(raw_services, list):
        services = [svc for svc in (_sanitize_service(item, shop_name, idx) for idx, item in enumerate(raw_services)) if svc]
        return services
    if isinstance(raw_services, dict):
        service = _sanitize_service(raw_services, shop_name, 0)
        return [service] if service else []
    count = _parse_positive_int(raw_services, 0)
    return [
        {
            "id": _ensure_entry_id(None, "svc"),
            "name": f"{shop_name} Service {idx + 1}",
            "category": "General Services",
            "price": 0,
            "duration": "45 min",
            "description": "",
        }
        for idx in range(count)
    ]


def _sanitize_member(entry: object | None, shop_name: str, index: int) -> dict:
    data = entry if isinstance(entry, dict) else {}
    name = str(data.get("name") or f"{shop_name} Member {index + 1}").strip()
    if not name:
        name = f"{shop_name} Member {index + 1}"
    tier = str(data.get("tier") or data.get("membership_tier") or "Silver").strip() or "Silver"
    joined = str(data.get("joined_at") or timezone.now().isoformat())
    orders = _parse_positive_int(data.get("orders"), 0)
    spend = _parse_positive_float(data.get("total_spend") or data.get("spend"), 0)
    discount = _clamp_discount_floor(data.get("discountPercent") or data.get("discount") or data.get("active_discount"))
    return {
        "id": _ensure_entry_id(data.get("id"), "member"),
        "name": name,
        "tier": tier,
        "joined_at": joined,
        "orders": orders,
        "total_spend": spend,
        "discount_percent": discount,
    }


def _sanitize_shop_members(raw_members: object | None, shop_name: str) -> list[dict]:
    if isinstance(raw_members, list):
        members = [member for member in (_sanitize_member(item, shop_name, idx) for idx, item in enumerate(raw_members)) if member]
        return members
    if isinstance(raw_members, dict):
        member = _sanitize_member(raw_members, shop_name, 0)
        return [member]
    count = _parse_positive_int(raw_members, 0)
    return [
        _sanitize_member({}, shop_name, idx)
        for idx in range(count)
    ]


def _clamp_discount_floor(value: object | None, minimum: int = 5) -> int:
    try:
        discount = int(float(value))
    except (TypeError, ValueError):
        return minimum
    return max(minimum, min(100, discount))


def _build_shop_metrics(entry: object | None, product_count: int, service_count: int) -> dict:
    data = entry if isinstance(entry, dict) else {}
    revenue = _parse_positive_float(data.get("revenue_total") or data.get("revenue"), max(0, product_count) * 1200)
    orders = _parse_positive_int(data.get("order_count") or data.get("orders"), max(1, product_count * 2))
    bookings = _parse_positive_int(data.get("booking_count") or data.get("bookings"), max(0, service_count))
    growth_rate = _parse_positive_float(data.get("growth_rate"), 12.0)
    conversion_rate = _parse_positive_float(data.get("conversion_rate"), 4.0)
    repeat_buyers = _parse_positive_int(data.get("repeat_buyers"), int(orders * 0.2))
    landing_visits = _parse_positive_int(data.get("landing_page_visits"), max(orders, 10))
    return {
        "revenue_total": revenue,
        "order_count": orders,
        "booking_count": bookings,
        "growth_rate": round(growth_rate, 1),
        "conversion_rate": round(conversion_rate, 1),
        "repeat_buyers": repeat_buyers,
        "landing_page_visits": landing_visits,
    }


def _count_shop_products(shop: dict | None) -> int:
    if not isinstance(shop, dict):
        return 0
    products = shop.get("products")
    if isinstance(products, list):
        return len(products)
    return _parse_positive_int(products, 0)


def _sanitize_shop(entry: object | None) -> dict | None:
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("name") or "Shop").strip()
    if not name:
        name = "Shop"
    products = _sanitize_products(entry.get("products"), name)
    services = _sanitize_shop_services(entry.get("services"), name)
    members = _sanitize_shop_members(entry.get("members") or entry.get("members_list"), name)
    slug = str(entry.get("slug") or name).strip() or name
    status = str(entry.get("status") or entry.get("state") or "draft").strip().lower()
    category = str(entry.get("category") or "Global commerce").strip() or "Global commerce"
    description = str(entry.get("description") or entry.get("about") or "").strip()
    tagline = str(entry.get("tagline") or entry.get("summary") or "Premium storefront").strip()
    business_type = str(entry.get("business_type") or entry.get("type") or "products").strip().lower()
    country = str(entry.get("country") or entry.get("country_of_operation") or "Global").strip() or "Global"
    currency = str(entry.get("currency") or entry.get("preferred_currency") or "USD").upper()
    language = str(entry.get("language") or entry.get("primary_language") or "English").strip()
    featured_image = str(entry.get("featured_image") or entry.get("cover_url") or entry.get("banner_url") or "").strip()
    seo_title = str(entry.get("seo_title") or entry.get("meta_title") or "").strip()
    seo_description = str(entry.get("seo_description") or entry.get("meta_description") or "").strip()
    payment_methods = entry.get("payment_methods") or entry.get("payments") or []
    trust_badges = entry.get("trust_badges") or entry.get("badges") or []
    landing_page = entry.get("landing_page") if isinstance(entry.get("landing_page"), dict) else {}
    discount_floor = _clamp_discount_floor(
        entry.get("discountFloor")
        or entry.get("discount_floor")
        or entry.get("membership_discount_pct")
        or entry.get("membershipDiscountPct")
        or entry.get("discount")
    )
    metrics = _build_shop_metrics(entry, len(products), len(services))
    return {
        "id": _ensure_entry_id(entry.get("id"), "shop"),
        "name": name,
        "slug": slug,
        "tagline": tagline,
        "description": description,
        "category": category,
        "status": status,
        "business_type": business_type,
        "country": country,
        "currency": currency,
        "language": language,
        "featured_image": featured_image,
        "seo_title": seo_title,
        "seo_description": seo_description,
        "payment_methods": payment_methods,
        "trust_badges": trust_badges,
        "landing_page": landing_page,
        "discount_floor": discount_floor,
        "membership_discount_pct": discount_floor,
        "products": products,
        "services": services,
        "members": members,
        "product_slots": len(products),
        "service_slots": len(services),
        "members_count": len(members),
        "metrics": metrics,
    }


def _ensure_shop_data(entries: list[dict]) -> list[dict]:
    sanitized: list[dict] = []
    for entry in entries:
        shop = _sanitize_shop(entry)
        if shop:
            sanitized.append(shop)
    return sanitized


def _sanitize_course(entry: object | None) -> dict | None:
    if not isinstance(entry, dict):
        return None
    title = str(entry.get("title") or "").strip()
    if not title:
        return None
    summary = str(entry.get("summary") or entry.get("description") or "Broadcast powered course").strip()
    return {
        "id": _ensure_entry_id(entry.get("id"), "course"),
        "title": title,
        "summary": summary,
    }


def _ensure_course_data(entries: list[dict]) -> list[dict]:
    sanitized: list[dict] = []
    for entry in entries:
        course = _sanitize_course(entry)
        if course:
            sanitized.append(course)
    return sanitized




def _normalize_phone_variants(phone: str) -> set[str]:
    raw = str(phone or '').strip()
    if not raw:
        return set()
    digits = ''.join(ch for ch in raw if ch.isdigit())
    values = {raw}
    if digits:
        values.add(digits)
        values.add(f"+{digits}")
    if raw.startswith('+') and raw[1:]:
        values.add(raw[1:])
    return {value for value in values if value}


def _normalize_email_value(value: object | None) -> str:
    return str(value or '').strip().lower()


def _does_member_represent_user(member: dict, user) -> bool:
    if not isinstance(member, dict) or user is None:
        return False

    member_user_id = str(member.get('userId') or member.get('user_id') or '').strip()
    user_id = str(getattr(user, 'id', '') or '').strip()
    if member_user_id and user_id and member_user_id == user_id:
        return True

    member_phone_variants = _normalize_phone_variants(str(member.get('phone') or '').strip())
    user_phone_variants = _normalize_phone_variants(str(getattr(user, 'phone', '') or '').strip())
    if member_phone_variants and user_phone_variants and member_phone_variants.intersection(user_phone_variants):
        return True

    member_email = _normalize_email_value(member.get('email'))
    user_email = _normalize_email_value(getattr(user, 'email', ''))
    if member_email and user_email and member_email == user_email:
        return True

    return False


def _is_user_owner_of_institution(user, institution: dict) -> bool:
    if user is None or not isinstance(institution, dict):
        return False

    owner_contact = institution.get('owner_contact') if isinstance(institution.get('owner_contact'), dict) else {}
    owner_user_id = str(owner_contact.get('userId') or owner_contact.get('user_id') or '').strip()
    user_id = str(getattr(user, 'id', '') or '').strip()
    if owner_user_id and user_id and owner_user_id == user_id:
        return True

    user_phone_variants = _normalize_phone_variants(str(getattr(user, 'phone', '') or '').strip())
    owner_phone_variants = _normalize_phone_variants(str(owner_contact.get('phone') or '').strip())
    if user_phone_variants and owner_phone_variants and user_phone_variants.intersection(owner_phone_variants):
        return True

    user_email = _normalize_email_value(getattr(user, 'email', ''))
    owner_email = _normalize_email_value(owner_contact.get('email'))
    if user_email and owner_email and user_email == owner_email:
        return True

    members = institution.get('members') if isinstance(institution.get('members'), list) else []
    for member in members:
        if _normalize_member_role(member.get('role') if isinstance(member, dict) else None) != 'owner':
            continue
        if _does_member_represent_user(member, user):
            return True

    return False


def _resolve_institution_membership_settings(institution: dict) -> dict:
    settings = institution.get('membership_settings') if isinstance(institution.get('membership_settings'), dict) else {}
    if not settings and isinstance(institution.get('membershipSettings'), dict):
        settings = institution.get('membershipSettings')

    open_value = settings.get('open') if isinstance(settings, dict) else None
    if open_value is None:
        open_value = settings.get('is_open') if isinstance(settings, dict) else None
    if open_value is None:
        open_value = institution.get('membership_open')
    if open_value is None:
        open_value = institution.get('membershipOpen')

    discount_value = settings.get('discountPercent') if isinstance(settings, dict) else None
    if discount_value is None:
        discount_value = settings.get('discount_percent') if isinstance(settings, dict) else None
    if discount_value is None:
        discount_value = institution.get('membership_discount_pct')
    if discount_value is None:
        discount_value = institution.get('membershipDiscountPct')

    try:
        discount = int(float(discount_value if discount_value is not None else 10))
    except (TypeError, ValueError):
        discount = 10
    discount = max(10, min(100, discount))

    return {'open': bool(open_value), 'discountPercent': discount}


def _resolve_institution_member_role(user, institution: dict, phone_override: str | None = None, owner_user_id: str | None = None) -> tuple[str, bool, str]:
    members = institution.get('members') if isinstance(institution.get('members'), list) else []
    user_id = str(getattr(user, 'id', '') or '')
    phone_variants = _normalize_phone_variants(phone_override or getattr(user, 'phone', '') or '')

    role = 'unassigned'
    is_member = False
    matched_roles: list[str] = []

    if owner_user_id and user_id and str(owner_user_id) == user_id:
        return 'owner', True, str(phone_override or getattr(user, 'phone', '') or '')

    for member in members:
        if not isinstance(member, dict):
            continue
        if _does_member_represent_user(member, user):
            is_member = True
            matched_roles.append(_normalize_member_role(member.get('role')))
            continue
        member_phone_variants = _normalize_phone_variants(str(member.get('phone') or '').strip())
        if phone_variants and member_phone_variants and member_phone_variants.intersection(phone_variants):
            is_member = True
            matched_roles.append(_normalize_member_role(member.get('role')))

    owner_contact = institution.get('owner_contact') if isinstance(institution.get('owner_contact'), dict) else {}
    owner_user_id = str(owner_contact.get('userId') or owner_contact.get('user_id') or '').strip()
    owner_phone_variants = _normalize_phone_variants(str(owner_contact.get('phone') or '').strip())
    if owner_user_id and owner_user_id == user_id:
        role = 'owner'
        is_member = True
    elif phone_variants and owner_phone_variants.intersection(phone_variants):
        role = 'owner'
        is_member = True
    elif matched_roles:
        role = _least_privileged_member_role(matched_roles)
        is_member = True

    return role, is_member, str(phone_override or getattr(user, 'phone', '') or '')


def _resolve_institution_owner_user(institution: dict, owner_user_id_hint: str | None = None) -> tuple[User | None, str]:
    candidate_ids: list[str] = []
    if owner_user_id_hint:
        candidate_ids.append(str(owner_user_id_hint).strip())

    owner_contact = institution.get('owner_contact') if isinstance(institution.get('owner_contact'), dict) else {}
    owner_contact_id = str(owner_contact.get('userId') or owner_contact.get('user_id') or '').strip()
    if owner_contact_id:
        candidate_ids.append(owner_contact_id)

    members = institution.get('members') if isinstance(institution.get('members'), list) else []
    for member in members:
        if not isinstance(member, dict):
            continue
        if _normalize_member_role(member.get('role')) != 'owner':
            continue
        member_user_id = str(member.get('userId') or member.get('user_id') or '').strip()
        if member_user_id:
            candidate_ids.append(member_user_id)

    seen = set()
    for raw_id in candidate_ids:
        if not raw_id or raw_id in seen:
            continue
        seen.add(raw_id)
        try:
            owner_user = User.objects.filter(id=raw_id).first()
        except Exception:
            owner_user = None
        if owner_user:
            return owner_user, str(owner_user.id)

    return None, ''


def _member_matches_owner_contact(member: dict, owner_contact: dict) -> bool:
    if not isinstance(member, dict) or not isinstance(owner_contact, dict):
        return False

    owner_user_id = str(owner_contact.get('userId') or owner_contact.get('user_id') or '').strip()
    owner_phone_variants = _normalize_phone_variants(str(owner_contact.get('phone') or '').strip())
    owner_email = str(owner_contact.get('email') or '').strip().lower()

    member_user_id = str(member.get('userId') or member.get('user_id') or '').strip()
    if owner_user_id and member_user_id and owner_user_id == member_user_id:
        return True

    member_phone_variants = _normalize_phone_variants(str(member.get('phone') or '').strip())
    if owner_phone_variants and member_phone_variants and owner_phone_variants.intersection(member_phone_variants):
        return True

    member_email = str(member.get('email') or '').strip().lower()
    if owner_email and member_email and owner_email == member_email:
        return True

    return False


def _enforce_immutable_owner(existing_institution: dict | None, incoming_institution: dict) -> dict:
    if not isinstance(incoming_institution, dict):
        return incoming_institution
    if not isinstance(existing_institution, dict):
        return incoming_institution

    existing_owner_contact = (
        existing_institution.get('owner_contact')
        if isinstance(existing_institution.get('owner_contact'), dict)
        else {}
    )
    if not existing_owner_contact:
        return incoming_institution

    institution = dict(incoming_institution)
    institution['owner_contact'] = existing_owner_contact
    institution['ownerContact'] = existing_owner_contact

    incoming_members = institution.get('members') if isinstance(institution.get('members'), list) else []
    normalized_members: list[dict] = []
    owner_present = False
    for member in incoming_members:
        if not isinstance(member, dict):
            continue
        row = dict(member)
        if _member_matches_owner_contact(row, existing_owner_contact):
            row['role'] = 'owner'
            row['permissions'] = HEALTH_MEMBER_ROLE_PERMISSIONS['owner']
            owner_present = True
        elif _normalize_member_role(row.get('role')) == 'owner':
            row['role'] = 'admin'
            row['permissions'] = HEALTH_MEMBER_ROLE_PERMISSIONS['admin']
        normalized_members.append(row)

    if not owner_present:
        owner_user_id = str(existing_owner_contact.get('userId') or existing_owner_contact.get('user_id') or '').strip()
        owner_name = str(existing_owner_contact.get('name') or 'Owner').strip() or 'Owner'
        owner_phone = str(existing_owner_contact.get('phone') or '').strip()
        owner_email = str(existing_owner_contact.get('email') or '').strip()
        normalized_members.insert(
            0,
            {
                'id': _ensure_entry_id(owner_user_id or None, 'member'),
                'userId': owner_user_id,
                'name': owner_name,
                'phone': owner_phone,
                'email': owner_email,
                'role': 'owner',
                'source': 'owner_added',
                'permissions': HEALTH_MEMBER_ROLE_PERMISSIONS['owner'],
            },
        )

    institution['members'] = normalized_members
    institution['employees'] = [
        {
            'id': _ensure_entry_id(member.get('id') or member.get('userId'), 'worker'),
            'name': str(member.get('name') or 'Worker'),
            'role': str(member.get('role') or 'staff'),
            'phone': str(member.get('phone') or ''),
            'email': str(member.get('email') or ''),
            'user_id': str(member.get('userId') or member.get('user_id') or ''),
        }
        for member in normalized_members
    ]
    return institution


def _is_visible_health_role(role: str) -> bool:
    normalized = _normalize_member_role(role)
    return normalized in {'owner', 'admin', 'manager', 'staff', 'analyst'}


def _merge_health_institutions(primary: list[dict], secondary: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for rows in (primary, secondary):
        for institution in rows:
            if not isinstance(institution, dict):
                continue
            institution_id = str(institution.get('id') or '').strip()
            if not institution_id:
                continue
            if institution_id in seen:
                continue
            seen.add(institution_id)
            merged.append(institution)
    return merged


def _normalize_identifier_value(value: object | None) -> str:
    return str(value or '').strip()


def _extract_candidate_institution_ids(institution_id: str) -> set[str]:
    raw = _normalize_identifier_value(institution_id)
    if not raw:
        return set()

    candidates: set[str] = {raw}
    if raw.startswith('health:'):
        raw_without_prefix = _normalize_identifier_value(raw.split(':', 1)[1])
        if raw_without_prefix:
            candidates.add(raw_without_prefix)
    if raw.startswith('health-card:'):
        parts = raw.split(':')
        if len(parts) >= 2:
            institution_segment = _normalize_identifier_value(parts[1])
            if institution_segment:
                candidates.add(institution_segment)
    return {value for value in candidates if value}


def _institution_identifier_variants(institution: dict) -> set[str]:
    if not isinstance(institution, dict):
        return set()

    keys = (
        'id',
        'institution_id',
        'institutionId',
        'legacy_id',
        'legacyId',
        'profile_id',
        'profileId',
        'slug',
        'code',
    )
    variants: set[str] = set()
    for key in keys:
        value = _normalize_identifier_value(institution.get(key))
        if not value:
            continue
        variants.add(value)
        variants.add(f'health:{value}')
    return variants


def _institution_matches_candidate(institution: dict, candidate_ids: set[str]) -> bool:
    if not candidate_ids:
        return False
    return bool(_institution_identifier_variants(institution).intersection(candidate_ids))


def _iter_health_institution_rows(owner_only: bool = False):
    queryset = (
        BroadcastHealthProfile.objects
        .select_related('profile__user')
        .prefetch_related("institution_rows__member_rows", "institution_rows__service_rows")
        .all()
    )
    for health_profile in queryset:
        payload = _compose_health_profile_payload(
            health_profile,
            health_profile.payload if isinstance(health_profile.payload, dict) else {},
        )
        institutions = _ensure_institution_data(payload.get('institutions') or [])
        profile_user = getattr(getattr(health_profile, 'profile', None), 'user', None)
        owner_user_id = str(getattr(getattr(health_profile, 'profile', None), 'user_id', '') or '').strip()
        for idx, institution in enumerate(institutions):
            if not isinstance(institution, dict):
                continue
            if owner_only and not _is_user_owner_of_institution(profile_user, institution):
                continue
            yield health_profile, payload, institutions, idx, institution, profile_user, owner_user_id


def _iter_authoritative_health_institution_rows():
    yield from _iter_health_institution_rows(owner_only=True)


def _collect_member_accessible_health_institutions(user, exclude_owner_user_id: str | None = None) -> list[dict]:
    user_id = str(getattr(user, 'id', '') or '').strip()
    user_phone = str(getattr(user, 'phone', '') or '').strip()
    user_email = _normalize_email_value(getattr(user, 'email', ''))
    if not user_id and not user_phone and not user_email:
        return []

    items: list[dict] = []
    seen: set[str] = set()
    skip_owner_id = str(exclude_owner_user_id or '').strip()
    for _health_profile, _payload, _institutions, _idx, institution, _profile_user, owner_user_id in _iter_authoritative_health_institution_rows():
        if skip_owner_id and owner_user_id == skip_owner_id:
            continue
        institution_id = str(institution.get('id') or '').strip()
        if not institution_id or institution_id in seen:
            continue
        role, is_member, _ = _resolve_institution_member_role(user, institution, owner_user_id=owner_user_id)
        if not is_member:
            continue
        if not _is_visible_health_role(role):
            continue
        seen.add(institution_id)
        items.append(institution)
    return items


HEALTH_BOOKING_ENGINE_ORDER = [
    'video',
    'lab',
    'prescription',
    'payment',
    'surgery',
    'admission',
    'emergency',
    'wellness',
    'logistics',
]

HEALTH_MEDIUM_ENGINE_MAP = {
    'video consultation engine': 'video',
    'e-prescription engine': 'prescription',
    'lab order engine': 'lab',
    'imaging order engine': 'lab',
    'admission & bed management engine': 'admission',
    'surgery scheduling engine': 'surgery',
    'emergency dispatch engine': 'emergency',
    'pharmacy & fulfillment engine': 'prescription',
    'payment & billing engine': 'payment',
    'home logistics engine': 'logistics',
    'wellness program engine': 'wellness',
}


def _normalize_string_list(raw: object | None) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item or '').strip() for item in raw if str(item or '').strip()]


def _extract_service_medium_names(service: dict) -> list[str]:
    if not isinstance(service, dict):
        return []

    medium_names: list[str] = []
    medium_names.extend(_normalize_string_list(service.get('medium_names')))
    medium_names.extend(_normalize_string_list(service.get('mediumNames')))

    raw_links = service.get('medium_links') or service.get('mediumLinks')
    if isinstance(raw_links, list):
        for link in raw_links:
            if not isinstance(link, dict):
                continue
            medium = link.get('medium') if isinstance(link.get('medium'), dict) else {}
            medium_name = str(medium.get('name') or link.get('name') or '').strip()
            if medium_name:
                medium_names.append(medium_name)

    return list(dict.fromkeys(medium_names))


def _extract_declared_service_engines(service: dict) -> list[str]:
    if not isinstance(service, dict):
        return []
    declared = []
    declared.extend(_normalize_string_list(service.get('available_engines')))
    declared.extend(_normalize_string_list(service.get('availableEngines')))
    out: list[str] = []
    for item in declared:
        normalized = item.lower().strip()
        if normalized in HEALTH_BOOKING_ENGINE_ORDER and normalized not in out:
            out.append(normalized)
    return out


def _infer_service_engine_keys(service: dict, institution_type: str = '') -> list[str]:
    if not isinstance(service, dict):
        return ['payment']

    detected: set[str] = set(_extract_declared_service_engines(service))

    for medium_name in _extract_service_medium_names(service):
        mapped = HEALTH_MEDIUM_ENGINE_MAP.get(medium_name.strip().lower())
        if mapped:
            detected.add(mapped)

    service_id = str(service.get('id') or service.get('service_id') or '').strip().lower()
    service_name = str(service.get('name') or '').strip().lower()
    service_description = str(service.get('description') or '').strip().lower()
    haystack = f"{service_id} {service_name} {service_description}"

    keyword_map = {
        'video': ('video', 'tele', 'virtual', 'remote'),
        'lab': ('lab', 'blood', 'test', 'diagnostic', 'scan', 'xray', 'mri', 'ct', 'ultrasound', 'pcr', 'imaging'),
        'prescription': ('prescription', 'rx', 'medication', 'pharmacy', 'dispens', 'refill'),
        'payment': ('payment', 'billing', 'invoice', 'charge', 'price'),
        'surgery': ('surgery', 'procedure', 'operation', 'operative'),
        'admission': ('admission', 'inpatient', 'bed', 'ward', 'icu'),
        'emergency': ('emergency', 'urgent', 'trauma', 'critical'),
        'wellness': ('wellness', 'fitness', 'nutrition', 'mental', 'habit', 'challenge', 'weight'),
        'logistics': ('logistics', 'delivery', 'pickup', 'dispatch', 'home sample', 'home delivery'),
    }
    for engine, keywords in keyword_map.items():
        if any(keyword in haystack for keyword in keywords):
            detected.add(engine)

    normalized_type = _normalize_health_institution_type(institution_type or '')
    if normalized_type == 'lab':
        detected.update({'lab', 'payment'})
    elif normalized_type == 'diagnostics':
        detected.update({'lab', 'payment'})
    elif normalized_type == 'pharmacy':
        detected.update({'prescription', 'payment'})
    elif normalized_type == 'wellness_center':
        detected.update({'wellness', 'payment'})
    elif normalized_type == 'hospital':
        detected.update({'payment'})
    else:
        detected.update({'payment'})

    allowed_keys = set(filter_booking_engine_keys(HEALTH_BOOKING_ENGINE_ORDER))
    ordered = [engine for engine in HEALTH_BOOKING_ENGINE_ORDER if engine in detected and engine in allowed_keys]
    return ordered or ['payment']


def _annotate_service_with_engines(service: dict, institution_type: str = '') -> dict:
    if not isinstance(service, dict):
        return service
    row = dict(service)
    engines = _infer_service_engine_keys(row, institution_type)
    row['available_engines'] = engines
    row['availableEngines'] = engines
    return row


def _collect_owner_accessible_health_institutions(user) -> list[dict]:
    user_id = str(getattr(user, 'id', '') or '').strip()
    user_phone = str(getattr(user, 'phone', '') or '').strip()
    user_email = _normalize_email_value(getattr(user, 'email', ''))
    if not user_id and not user_phone and not user_email:
        return []

    items: list[dict] = []
    seen: set[str] = set()
    for _health_profile, _payload, _institutions, _idx, institution, _profile_user, owner_user_id in _iter_authoritative_health_institution_rows():
        institution_id = str(institution.get('id') or '').strip()
        if not institution_id or institution_id in seen:
            continue
        role, is_member, _ = _resolve_institution_member_role(user, institution, owner_user_id=owner_user_id)
        if not is_member or role != 'owner':
            continue
        seen.add(institution_id)
        items.append(institution)
    return items


def _build_health_cards_from_institution(institution: dict) -> list[dict]:
    availability = institution.get('availability') if isinstance(institution.get('availability'), dict) else {}
    if not availability and isinstance(institution.get('dashboard'), dict):
        availability = institution.get('dashboard', {}).get('availability') if isinstance(institution.get('dashboard', {}).get('availability'), dict) else {}

    statuses = availability.get('calendar_statuses') or availability.get('calendarStatuses') or availability.get('date_statuses') or availability.get('dateStatuses') or {}
    times = availability.get('calendar_times') or availability.get('calendarTimes') or availability.get('date_times') or availability.get('dateTimes') or {}
    service_ids_map = availability.get('calendar_service_ids') or availability.get('calendarServiceIds') or availability.get('date_service_ids') or availability.get('dateServiceIds') or {}

    if not isinstance(statuses, dict):
        statuses = {}
    if not isinstance(times, dict):
        times = {}
    if not isinstance(service_ids_map, dict):
        service_ids_map = {}
    institution_type = _normalize_health_institution_type(institution.get('type'))

    service_sources: list[list[dict]] = []
    for candidate in (
        institution.get('services'),
        institution.get('service_templates'),
        institution.get('serviceTemplates'),
        (institution.get('dashboard') or {}).get('services') if isinstance(institution.get('dashboard'), dict) else None,
    ):
        if isinstance(candidate, list):
            service_sources.append(candidate)

    service_map: dict[str, dict] = {}
    for services in service_sources:
        for idx, service in enumerate(services):
            if not isinstance(service, dict):
                continue
            parsed = _sanitize_service_entry(service, idx)
            if not parsed:
                continue
            if parsed.get('active', True) is False:
                continue
            service_id = str(parsed.get('id') or '').strip()
            if not service_id or service_id in service_map:
                continue
            service_map[service_id] = _annotate_service_with_engines(parsed, institution_type)

    def _placeholder_service(service_id: str) -> dict:
        label = str(service_id or '').strip().replace('_', ' ').replace('-', ' ')
        pretty = ' '.join(part.capitalize() for part in label.split())
        return _annotate_service_with_engines({
            'id': str(service_id or '').strip(),
            'name': pretty or 'Health Service',
            'description': '',
            'active': True,
        }, institution_type)

    status_labels = {
        'available': 'Available',
        'limited': 'Limited',
        'fully_booked': 'Booked',
        'on_call': 'On call',
        'holiday': 'Holiday',
        'blocked': 'Blocked',
    }
    status_colors = {
        'available': '#10B981',
        'limited': '#F59E0B',
        'fully_booked': '#EF4444',
        'on_call': '#3B82F6',
        'holiday': '#8B5CF6',
        'blocked': '#6B7280',
    }

    cards: list[dict] = []
    for date_key, ids in service_ids_map.items():
        if not isinstance(ids, list):
            continue
        status_key = str(statuses.get(date_key) or 'available')
        for index, service_id in enumerate(ids):
            resolved_service_id = str(service_id or '').strip()
            if not resolved_service_id:
                continue
            service = service_map.get(resolved_service_id)
            if not service:
                service = _placeholder_service(resolved_service_id)
                service_map[resolved_service_id] = service
            card_id = f"{date_key}:{service.get('id')}:{index}"
            cards.append({
                'id': card_id,
                'date': str(date_key),
                'time': str(times.get(date_key) or ''),
                'statusKey': status_key,
                'statusLabel': status_labels.get(status_key, status_key.title()),
                'statusColor': status_colors.get(status_key, '#10B981'),
                'service': service,
            })

    cards.sort(key=lambda item: f"{item.get('date')} {item.get('time') or '00:00'}")
    return cards


_HEALTH_CARD_COLON_PATTERN = re.compile(r'^(\d{4}-\d{2}-\d{2}):(.+):(\d+)$')
_HEALTH_CARD_DASH_PATTERN = re.compile(r'^(\d{4}-\d{2}-\d{2})-(.+)-(\d+)$')


def _normalize_health_card_id(value: object | None) -> str:
    raw = unquote(str(value or '').strip())
    if not raw:
        return ''

    if raw.startswith('health-card:'):
        parts = raw.split(':', 2)
        raw = str(parts[2] if len(parts) == 3 else '').strip()

    colon_match = _HEALTH_CARD_COLON_PATTERN.match(raw)
    if colon_match:
        date_key, service_id, index = colon_match.groups()
        return f'{date_key}:{service_id}:{index}'

    dash_match = _HEALTH_CARD_DASH_PATTERN.match(raw)
    if dash_match:
        date_key, service_id, index = dash_match.groups()
        return f'{date_key}:{service_id}:{index}'

    return raw


def _extract_health_card_components(value: object | None) -> tuple[str, str, str]:
    normalized = _normalize_health_card_id(value)
    match = _HEALTH_CARD_COLON_PATTERN.match(normalized)
    if not match:
        return '', '', ''
    return match.groups()


def _health_card_id_variants(value: object | None) -> set[str]:
    normalized = _normalize_health_card_id(value)
    if not normalized:
        return set()

    variants = {normalized}
    match = _HEALTH_CARD_COLON_PATTERN.match(normalized)
    if match:
        date_key, service_id, index = match.groups()
        variants.add(f'{date_key}-{service_id}-{index}')
    return variants


def _health_card_ids_match(left: object | None, right: object | None) -> bool:
    left_variants = _health_card_id_variants(left)
    if not left_variants:
        return False
    right_variants = _health_card_id_variants(right)
    if not right_variants:
        return False
    return bool(left_variants.intersection(right_variants))


def _resolve_health_card(cards: list[dict], card_id: object | None, request_payload: object | None = None) -> tuple[dict | None, str]:
    requested_id = _normalize_health_card_id(card_id)
    if not cards:
        return None, requested_id

    direct = next((item for item in cards if _health_card_ids_match(item.get('id'), requested_id)), None)
    if direct:
        return direct, _normalize_health_card_id(direct.get('id')) or requested_id

    date_key, service_id, _index = _extract_health_card_components(requested_id)
    if date_key and service_id:
        fuzzy = next(
            (
                item
                for item in cards
                if str(item.get('date') or '').strip() == date_key
                and str((item.get('service') or {}).get('id') if isinstance(item.get('service'), dict) else '').strip() == service_id
            ),
            None,
        )
        if fuzzy:
            return fuzzy, _normalize_health_card_id(fuzzy.get('id')) or requested_id

    payload = request_payload if isinstance(request_payload, dict) else {}
    service_hint = str(payload.get('serviceId') or payload.get('service_id') or '').strip()
    date_hint = str(payload.get('date') or payload.get('dateKey') or payload.get('date_key') or '').strip()
    time_hint = str(payload.get('time') or payload.get('timeValue') or payload.get('time_value') or '').strip()

    if service_hint or date_hint:
        hinted_rows = [
            item
            for item in cards
            if (not date_hint or str(item.get('date') or '').strip() == date_hint)
            and (
                not service_hint
                or str((item.get('service') or {}).get('id') if isinstance(item.get('service'), dict) else '').strip() == service_hint
            )
            and (not time_hint or str(item.get('time') or '').strip() == time_hint)
        ]
        if hinted_rows:
            target = hinted_rows[0]
            return target, _normalize_health_card_id(target.get('id')) or requested_id

    if service_hint:
        service_rows = [
            item
            for item in cards
            if str((item.get('service') or {}).get('id') if isinstance(item.get('service'), dict) else '').strip() == service_hint
        ]
        if len(service_rows) == 1:
            target = service_rows[0]
            return target, _normalize_health_card_id(target.get('id')) or requested_id

    return None, requested_id


def _mark_stale_health_card_broadcasts(institution_id: object | None, card_id: object | None) -> int:
    resolved_institution_id = str(institution_id or '').strip()
    normalized_card_id = _normalize_health_card_id(card_id)
    if not resolved_institution_id or not normalized_card_id:
        return 0

    variants = _health_card_id_variants(normalized_card_id)
    if not variants:
        variants = {normalized_card_id}

    source_ids = [f'health-card:{resolved_institution_id}:{variant}' for variant in variants]
    return BroadcastItem.objects.filter(
        source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
        source_id__in=source_ids,
        is_deleted=False,
    ).update(is_deleted=True)


def _find_health_institution_owner_context(institution_id: str, preferred_user=None):
    candidate_ids = _extract_candidate_institution_ids(institution_id)
    if not candidate_ids:
        return None, None, None, -1, None

    preferred_user_id = str(getattr(preferred_user, 'id', '') or '').strip()
    authoritative_match = None
    for health_profile, payload, institutions, idx, institution, profile_user, _owner_user_id in _iter_authoritative_health_institution_rows():
        if not _institution_matches_candidate(institution, candidate_ids):
            continue
        profile_user_id = str(getattr(profile_user, 'id', '') or '').strip()
        row = (health_profile, payload, institutions, idx, institution)
        if preferred_user_id and profile_user_id and profile_user_id == preferred_user_id:
            return row
        if authoritative_match is None:
            authoritative_match = row

    if authoritative_match is not None:
        return authoritative_match

    fallback_match = None
    for health_profile, payload, institutions, idx, institution, profile_user, _owner_user_id in _iter_health_institution_rows():
        if not _institution_matches_candidate(institution, candidate_ids):
            continue
        profile_user_id = str(getattr(profile_user, 'id', '') or '').strip()
        row = (health_profile, payload, institutions, idx, institution)
        if preferred_user_id and profile_user_id and profile_user_id == preferred_user_id:
            return row
        if fallback_match is None:
            fallback_match = row

    if fallback_match is not None:
        return fallback_match

    return None, None, None, -1, None


def _build_health_card_broadcast_payload(institution: dict, card: dict) -> dict:
    service = card.get('service') if isinstance(card.get('service'), dict) else {}
    service_name = str(service.get('name') or 'Health Service')
    service_description = str(service.get('description') or '').strip()
    time_label = str(card.get('time') or '').strip()
    date_label = str(card.get('date') or '').strip()
    headline = f"{service_name} on {date_label}{(' at ' + time_label) if time_label else ''}"
    summary = service_description or headline
    source_id = f"health-card:{institution.get('id')}:{card.get('id')}"

    return {
        'profile_id': f"health:{institution.get('id')}",
        'profile_name': str(institution.get('name') or 'Health Institution'),
        'entry': {
            'id': source_id,
            'title': headline,
            'summary': summary,
            'attachments': [],
            'created_at': timezone.now().isoformat(),
            'updated_at': timezone.now().isoformat(),
        },
        'health_card': {
            'institution_id': str(institution.get('id') or ''),
            'institution_name': str(institution.get('name') or ''),
            'card_id': str(card.get('id') or ''),
            'date': date_label,
            'time': time_label,
            'status': str(card.get('statusKey') or ''),
            'service_id': str(service.get('id') or ''),
            'service_name': service_name,
            'service_description': service_description,
            'source': 'healthcare',
        },
    }
def _ensure_health_profile_structure(health: dict) -> dict:
    institutions = health.get('institutions') or []
    health['institutions'] = _ensure_institution_data(institutions)
    health['employees_total'] = sum(len(inst.get('employees', [])) for inst in health['institutions'])
    return health


def _ensure_market_profile_structure(market: dict) -> dict:
    shops = market.get('shops') or []
    market['shops'] = _ensure_shop_data(shops)
    market = _apply_landing_builder_updates(market, market)
    return market


def _ensure_education_profile_structure(education: dict) -> dict:
    courses = education.get('courses') or []
    education['courses'] = _ensure_course_data(courses)
    education = _apply_landing_builder_updates(education, education)
    return education


def _store_thumbnail_upload(file_obj) -> str:
    media_root = getattr(settings, "MEDIA_ROOT", "media")
    ext = os.path.splitext(file_obj.name or "")[1] or ".jpg"
    rel_name = f"{uuid.uuid4().hex}{ext}"
    rel_path = os.path.join(THUMBNAIL_SUBDIRECTORY, rel_name)
    abs_path = os.path.join(media_root, rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as destination:
        for chunk in file_obj.chunks():
            destination.write(chunk)
    return rel_path


class BroadcastVideoUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    title = serializers.CharField(max_length=255, required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    thumbnail_url = serializers.CharField(required=False, allow_blank=True)
    thumbnail = serializers.FileField(required=False)
    channel_id = serializers.UUIDField(required=False)
    transcript_segments = serializers.ListField(child=serializers.JSONField(), required=False, allow_empty=True)


class BroadcastFeedView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        cleaned = cleanup_expired_broadcast_items()
        if cleaned:
            logger.info("Purged %d expired broadcast items before listing the feed.", cleaned)
        limit = int(request.query_params.get("limit", 50))
        limit = max(1, min(limit, 200))
        since = timezone.now() - timedelta(days=10)
        now = timezone.now()

        source_param = (request.query_params.get("source_type") or "").strip().lower()
        raw_tokens = [token.strip() for token in source_param.split(",") if token.strip()]
        source_type_map = {
            "market": BroadcastSourceType.MARKET_PRODUCT,
            "market_product": BroadcastSourceType.MARKET_PRODUCT,
            "market_service": BroadcastSourceType.MARKET_SERVICE,
            "market_all": [BroadcastSourceType.MARKET_PRODUCT, BroadcastSourceType.MARKET_SERVICE],
            "broadcast_feed_entry": BroadcastSourceType.BROADCAST_FEED_ENTRY,
            "healthcare": BroadcastSourceType.COMMUNITY_POST,  # ensure fallback
        }
        resolved_sources: list[str] = []
        for token in raw_tokens:
            value = source_type_map.get(token)
            if isinstance(value, (list, tuple)):
                resolved_sources.extend(value)
            elif value:
                resolved_sources.append(value)
        print("Source filter tokens:", raw_tokens)
        print("Resolved source filters:", resolved_sources)

        broadcast_items_qs = (
            BroadcastItem.objects
            .select_related("broadcasted_by", "broadcasted_by__profile")
            .filter(is_deleted=False, expires_at__gt=now)
        )
        print("Initial broadcast items:", broadcast_items_qs)

        if resolved_sources:
            broadcast_items_qs = broadcast_items_qs.filter(source_type__in=resolved_sources)
        else:
            broadcast_items_qs = broadcast_items_qs.exclude(source_type=BroadcastSourceType.MARKET_PRODUCT)

        print("Filtered broadcast items:", broadcast_items_qs)

        broadcast_items = broadcast_items_qs.order_by("-broadcasted_at")[:limit * 3]

        def _absolutize_avatar(value: Any) -> str | None:
            if value is None:
                return None
            text = str(value).strip()
            if not text:
                return None
            if text.startswith("http://") or text.startswith("https://"):
                return text
            if text.startswith("//"):
                return f"https:{text}"
            if text.startswith("/"):
                return request.build_absolute_uri(text)
            media_url = str(getattr(settings, "MEDIA_URL", "/media/") or "/media/").strip()
            if not media_url.endswith("/"):
                media_url = f"{media_url}/"
            return request.build_absolute_uri(f"{media_url}{text.lstrip('/')}")

        def _absolutize_media_url(value: Any) -> str | None:
            if value is None:
                return None
            text = str(value).strip()
            if not text:
                return None
            if text.startswith("http://") or text.startswith("https://"):
                return text
            if text.startswith("//"):
                return f"https:{text}"
            if text.startswith("/"):
                return request.build_absolute_uri(text)
            media_url = str(getattr(settings, "MEDIA_URL", "/media/") or "/media/").strip()
            if not media_url.endswith("/"):
                media_url = f"{media_url}/"
            return request.build_absolute_uri(f"{media_url}{text.lstrip('/')}")

        def _collect_product_images(prod: Product) -> list[str]:
            urls: list[str] = []
            seen: set[str] = set()

            def _add(raw: Any | None) -> None:
                if raw is None:
                    return
                abs_url = _absolutize_media_url(raw)
                if abs_url and abs_url not in seen:
                    seen.add(abs_url)
                    urls.append(abs_url)

            primary = getattr(prod, "effective_image_url", None)
            if primary:
                _add(primary)

            for img in getattr(prod, "images", []).all():
                try:
                    _add(img.image_file.url)
                except Exception:
                    continue
            return urls

        def _collect_service_images(service_obj: ShopService) -> list[str]:
            urls: list[str] = []
            seen: set[str] = set()

            def _add(raw: Any | None) -> None:
                if raw is None:
                    return
                abs_url = _absolutize_media_url(raw)
                if abs_url and abs_url not in seen:
                    seen.add(abs_url)
                    urls.append(abs_url)

            primary = getattr(service_obj, "effective_image_url", None)
            if primary:
                _add(primary)

            for img in getattr(service_obj, "images", []).all():
                try:
                    _add(img.image_file.url)
                except Exception:
                    continue
            return urls

        def _build_author_payload(user: User | None) -> dict[str, Any]:
            if not user:
                return {}
            profile = getattr(user, "profile", None)
            avatar_from_profile = ""
            if profile is not None:
                avatar_from_profile = str(getattr(profile, "avatar_url", "") or "").strip()
                if not avatar_from_profile and getattr(profile, "avatar_file", None):
                    try:
                        avatar_from_profile = str(profile.avatar_file.url or "").strip()
                    except Exception:
                        avatar_from_profile = ""
            display_name = (
                str(getattr(user, "display_name", "") or "").strip()
                or str(getattr(user, "username", "") or "").strip()
                or str(getattr(user, "phone", "") or "").strip()
                or "KIS user"
            )
            bio = ""
            profile_id = ""
            if profile is not None:
                bio = str(getattr(profile, "bio", "") or "").strip()
                profile_id = str(getattr(profile, "id", "") or "").strip()
            payload: dict[str, Any] = {
                "id": str(user.id),
                "display_name": display_name,
            }
            if profile_id:
                payload["profile_id"] = profile_id
            avatar_url = _absolutize_avatar(avatar_from_profile)
            if avatar_url:
                payload["avatar_url"] = avatar_url
            if bio:
                payload["bio"] = bio
            return payload

        author_user_cache: dict[str, User | None] = {}

        def _build_author_payload_from_id(raw_user_id: Any) -> dict[str, Any]:
            user_id = str(raw_user_id or "").strip()
            if not user_id:
                return {}
            if user_id not in author_user_cache:
                user_qs = User.objects
                if hasattr(User, "profile"):
                    user_qs = user_qs.select_related("profile")
                author_user_cache[user_id] = user_qs.filter(id=user_id).first()
            return _build_author_payload(author_user_cache.get(user_id))

        broadcast_ids = [item.id for item in broadcast_items]
        reaction_counts = {
            row["broadcast_item_id"]: row["total"]
            for row in BroadcastReaction.objects.filter(broadcast_item_id__in=broadcast_ids)
            .values("broadcast_item_id")
            .annotate(total=models.Count("id"))
        }
        viewer_reactions = {
            row.broadcast_item_id: row.emoji
            for row in BroadcastReaction.objects.filter(
                broadcast_item_id__in=broadcast_ids,
                user=request.user,
            )
        }

        community_broadcasts = [item for item in broadcast_items if item.source_type == BroadcastSourceType.COMMUNITY_POST]
        partner_broadcasts = [item for item in broadcast_items if item.source_type == BroadcastSourceType.PARTNER_POST]
        channel_broadcasts = [item for item in broadcast_items if item.source_type == BroadcastSourceType.CHANNEL_MESSAGE]
        market_broadcasts = [item for item in broadcast_items if item.source_type == BroadcastSourceType.MARKET_PRODUCT]
        service_broadcasts = [item for item in broadcast_items if item.source_type == BroadcastSourceType.MARKET_SERVICE]
        profile_broadcasts = [item for item in broadcast_items if item.source_type == BroadcastSourceType.BROADCAST_FEED_ENTRY]

        channel_conversation_ids = list({str(item.conversation_id) for item in channel_broadcasts if item.conversation_id})
        channels = Channel.objects.filter(
            conversation_id__in=channel_conversation_ids,
            is_archived=False,
        ).select_related("partner", "community")
        channel_map = {str(ch.conversation_id): ch for ch in channels}
        channel_members = set(
            ConversationMember.objects.filter(
                user=request.user,
                left_at__isnull=True,
                is_blocked=False,
                conversation_id__in=channel_conversation_ids,
            ).values_list("conversation_id", flat=True)
        )

        channel_message_ids = [item.source_id for item in channel_broadcasts]
        channel_messages = _fetch_channel_messages(
            channel_conversation_ids,
            since,
            limit,
            message_ids=channel_message_ids,
        )
        channel_message_map = {
            str(msg.get("id") or msg.get("_id")): msg
            for msg in channel_messages
        }
        channel_items = []
        for item in channel_broadcasts:
            msg = channel_message_map.get(str(item.source_id))
            if not msg:
                continue
            conversation_id = str(item.conversation_id or msg.get("conversationId") or "")
            channel = channel_map.get(conversation_id)
            if not channel:
                continue
            is_subscribed = str(conversation_id) in channel_members
            text_value = msg.get("text") or ""
            text_doc = msg.get("textDoc") or build_plain_text_document(text_value)
            text_plain = msg.get("textPlain") or text_value
            text_preview = msg.get("textPreview") or text_plain[:200]
            channel_items.append(
                {
                    "id": str(item.id),
                    "source_type": "channel",
                    "source_id": str(item.source_id),
                    "conversation_id": conversation_id,
                    "title": channel.name,
                    "text": text_value,
                    "text_doc": text_doc,
                    "text_plain": text_plain,
                    "text_preview": text_preview,
                    "kind": msg.get("kind"),
                    "attachments": msg.get("attachments") or [],
                    "poll": msg.get("poll"),
                    "event": msg.get("event"),
                    "author": {"id": msg.get("senderId")},
                    "created_at": msg.get("createdAt"),
                    "broadcasted_at": item.broadcasted_at.isoformat(),
                    "expires_at": item.expires_at.isoformat(),
                    "comment_conversation_id": str(item.comment_conversation_id) if item.comment_conversation_id else None,
                    "reaction_count": reaction_counts.get(item.id, 0),
                    "viewer_reaction": viewer_reactions.get(item.id),
                    "source": {
                        "type": "channel",
                        "id": str(channel.id),
                        "name": channel.name,
                        "conversation_id": str(channel.conversation_id),
                        "is_subscribed": is_subscribed,
                        "can_open": is_subscribed,
                    },
                }
            )

        community_post_ids = [item.source_id for item in community_broadcasts]
        community_posts = (
            CommunityPost.objects.select_related("community", "author")
            .filter(
                id__in=community_post_ids,
                status=CommunityPostStatus.PUBLISHED,
                is_deleted=False,
            )
        )
        community_post_map = {str(post.id): post for post in community_posts}
        community_ids = {str(post.community_id) for post in community_posts}
        community_members = set(
            CommunityMembership.objects.filter(
                user=request.user,
                left_at__isnull=True,
                is_banned=False,
                community_id__in=community_ids,
            ).values_list("community_id", flat=True)
        )

        community_items = []
        for item in community_broadcasts:
            post = community_post_map.get(str(item.source_id))
            if not post:
                continue
            is_member = str(post.community_id) in community_members
            text_plain = post.text_plain or ""
            community_items.append(
                {
                    "id": str(item.id),
                    "source_type": "community",
                    "source_id": str(post.id),
                    "title": post.community.name,
                    "text": text_plain,
                    "text_doc": post.text,
                    "text_plain": text_plain,
                    "text_preview": post.text_preview or text_plain[:200],
                    "attachments": post.attachments or [],
                    "poll": post.poll or None,
                    "event": post.event or None,
                    "author": {
                        "id": str(post.author_id),
                        "display_name": post.author.display_name or post.author.username,
                    },
                    "created_at": post.created_at.isoformat(),
                    "broadcasted_at": item.broadcasted_at.isoformat(),
                    "expires_at": item.expires_at.isoformat(),
                    "comment_conversation_id": str(item.comment_conversation_id) if item.comment_conversation_id else None,
                    "reaction_count": reaction_counts.get(item.id, 0),
                    "viewer_reaction": viewer_reactions.get(item.id),
                    "source": {
                        "type": "community",
                        "id": str(post.community_id),
                        "name": post.community.name,
                        "join_policy": post.community.join_policy,
                        "is_member": is_member,
                        "can_open": is_member,
                    },
                }
            )

        partner_post_ids = [item.source_id for item in partner_broadcasts]
        partner_posts = (
            PartnerPost.objects.select_related("partner", "author")
            .filter(
                id__in=partner_post_ids,
                is_deleted=False,
            )
        )
        partner_post_map = {str(post.id): post for post in partner_posts}
        partner_ids = {str(post.partner_id) for post in partner_posts}
        partner_members = set(
            PartnerMembership.objects.filter(
                user=request.user,
                partner_id__in=partner_ids,
                status__in=[PartnerMembershipStatus.MEMBER, PartnerMembershipStatus.SUBSCRIBER],
            ).values_list("partner_id", flat=True)
        )
        partners = Partner.objects.filter(id__in=partner_ids).select_related("join_config")
        partner_map = {str(p.id): p for p in partners}

        partner_items = []
        for item in partner_broadcasts:
            post = partner_post_map.get(str(item.source_id))
            if not post:
                continue
            partner = partner_map.get(str(post.partner_id))
            if not partner:
                continue
            is_member = str(partner.id) in partner_members
            join_cfg = getattr(partner, "join_config", None)
            text_plain = post.text_plain or ""
            partner_items.append(
                {
                    "id": str(item.id),
                    "source_type": "partner",
                    "source_id": str(post.id),
                    "title": partner.name,
                    "text": text_plain,
                    "text_doc": post.text,
                    "text_plain": text_plain,
                    "text_preview": post.text_preview or text_plain[:200],
                    "attachments": post.attachments or [],
                    "poll": post.poll or None,
                    "event": post.event or None,
                    "author": {
                        "id": str(post.author_id),
                        "display_name": post.author.display_name or post.author.username,
                    },
                    "created_at": post.created_at.isoformat(),
                    "broadcasted_at": item.broadcasted_at.isoformat(),
                    "expires_at": item.expires_at.isoformat(),
                    "comment_conversation_id": str(item.comment_conversation_id) if item.comment_conversation_id else None,
                    "reaction_count": reaction_counts.get(item.id, 0),
                    "viewer_reaction": viewer_reactions.get(item.id),
                    "source": {
                        "type": "partner",
                        "id": str(partner.id),
                        "name": partner.name,
                        "is_member": is_member,
                        "can_open": is_member,
                        "allow_apply": bool(getattr(join_cfg, "allow_apply", True)) if join_cfg else True,
                        "allow_subscribe": bool(getattr(join_cfg, "allow_subscribe", True)) if join_cfg else True,
                        "auto_approve": bool(getattr(join_cfg, "auto_approve", False)) if join_cfg else False,
                        "methods": getattr(join_cfg, "methods", []) if join_cfg else [],
                    },
                }
            )

        token_set = set(raw_tokens)
        include_product_items = bool(token_set & {"market", "market_product", "market_all"})
        include_service_items = bool(token_set & {"market_service", "market_all"})

        market_ids = [item.source_id for item in market_broadcasts]
        market_products = (
            Product.objects.filter(id__in=market_ids, is_deleted=False)
            .select_related("shop__landing_page")
            .prefetch_related("images")
        )
        market_map = {str(product.id): product for product in market_products}

        product_items = []
        for item in market_broadcasts:
            product = market_map.get(str(item.source_id))
            if not product:
                continue
            shop = product.shop
            text_plain = product.name or ""
            membership_discount_pct = getattr(shop, 'membership_discount_pct', 10) if shop else 10
            viewer_is_member = False
            if shop and request.user.is_authenticated:
                if request.user.id == shop.owner_id:
                    viewer_is_member = True
                else:
                    viewer_is_member = ShopTeamMember.objects.filter(shop=shop, user=request.user, is_active=True).exists()
            product_images = _collect_product_images(product)
            landing_page = getattr(shop, 'landing_page', None)
            landing_public = bool(landing_page and landing_page.is_public)
            landing_published = bool(landing_page and landing_page.is_published)

            product_items.append(
                {
                    "id": str(item.id),
                    "source_type": "market_product",
                    "source_id": str(product.id),
                    "title": shop.name if shop else "Market",
                    "text": text_plain,
                    "text_doc": build_plain_text_document(text_plain),
                    "text_plain": text_plain,
                    "text_preview": text_plain[:200],
                    "attachments": [],
                    "author": {"id": str(shop.owner_id) if shop else None},
                    "created_at": product.created_at.isoformat(),
                    "broadcasted_at": item.broadcasted_at.isoformat(),
                    "expires_at": item.expires_at.isoformat(),
                    "comment_conversation_id": str(item.comment_conversation_id) if item.comment_conversation_id else None,
                    "reaction_count": reaction_counts.get(item.id, 0),
                    "viewer_reaction": viewer_reactions.get(item.id),
                    "source": {
                        "type": "market",
                        "id": str(shop.id) if shop else None,
                        "name": shop.name if shop else "Market",
                        "can_open": True,
                        "viewer_is_member": viewer_is_member,
                        "membership_open": bool(getattr(shop, 'membership_public', False)),
                        "membership_discount_pct": membership_discount_pct,
                        "landing_is_public": landing_public,
                        "landing_is_published": landing_published,
                        "landing_page_is_public": landing_public,
                        "landing_page_is_published": landing_published,
                        "landingIsPublished": landing_published,
                        "landingPageIsPublished": landing_published,
                        "landing_page": {
                            "is_public": landing_public,
                            "is_published": landing_published,
                            "isPublished": landing_published,
                            "public": landing_public,
                        },
                    },
                    "product": {
                        "id": str(product.id),
                        "name": product.name,
                        "description": product.description,
                        "price": str(product.price),
                        "currency": product.currency,
                        "inventory_type": product.inventory_type,
                        "rating_avg": product.rating_avg,
                        "rating_count": product.rating_count,
                        "images": product_images,
                    },
                }
            )

        service_items = []
        if service_broadcasts:
            service_ids = [item.source_id for item in service_broadcasts]
            market_services = (
                ShopService.objects.filter(id__in=service_ids, is_active=True)
                .select_related("shop__landing_page")
                .prefetch_related("images")
            )
            service_map = {str(service.id): service for service in market_services}
            booking_map: dict[str, dict[str, any]] = {}
            if request.user.is_authenticated:
                booking_qs = ServiceBooking.objects.filter(
                    service_id__in=service_ids,
                    user=request.user,
                    status__in=[
                        ServiceBooking.STATUS_PENDING,
                        ServiceBooking.STATUS_CONFIRMED,
                        ServiceBooking.STATUS_AWAITING_SATISFACTION,
                        ServiceBooking.STATUS_COMPLETED,
                        ServiceBooking.STATUS_DISPUTE,
                    ],
                ).select_related("escrow")
                for booking in booking_qs:
                    serialized = ServiceBookingSerializer(booking, context={"request": request}).data
                    booking_map[str(booking.service_id)] = serialized
            for item in service_broadcasts:
                service = service_map.get(str(item.source_id))
                if not service:
                    continue
                shop = service.shop
                text_plain = service.name or ""
                membership_discount_pct = getattr(shop, 'membership_discount_pct', 10) if shop else 10
                viewer_is_member = False
                if shop and request.user.is_authenticated:
                    if request.user.id == shop.owner_id:
                        viewer_is_member = True
                    else:
                        viewer_is_member = ShopTeamMember.objects.filter(shop=shop, user=request.user, is_active=True).exists()
                service_images = _collect_service_images(service)
                landing_page = getattr(shop, 'landing_page', None)
                landing_public = bool(landing_page and landing_page.is_public)
                landing_published = bool(landing_page and landing_page.is_published)
                service_payload = {
                    "id": str(item.id),
                    "source_type": "market_service",
                    "source_id": str(service.id),
                        "title": shop.name if shop else "Market",
                        "text": text_plain,
                        "text_doc": build_plain_text_document(text_plain),
                        "text_plain": text_plain,
                        "text_preview": text_plain[:200],
                        "attachments": [],
                        "author": {"id": str(shop.owner_id) if shop else None},
                        "created_at": service.created_at.isoformat(),
                        "broadcasted_at": item.broadcasted_at.isoformat(),
                        "expires_at": item.expires_at.isoformat(),
                        "comment_conversation_id": str(item.comment_conversation_id) if item.comment_conversation_id else None,
                        "reaction_count": reaction_counts.get(item.id, 0),
                        "viewer_reaction": viewer_reactions.get(item.id),
                        "source": {
                            "type": "market",
                            "id": str(shop.id) if shop else None,
                            "name": shop.name if shop else "Market",
                            "can_open": True,
                            "viewer_is_member": viewer_is_member,
                            "membership_open": bool(getattr(shop, 'membership_public', False)),
                            "membership_discount_pct": membership_discount_pct,
                            "landing_is_public": landing_public,
                            "landing_is_published": landing_published,
                            "landing_page_is_public": landing_public,
                            "landing_page_is_published": landing_published,
                            "landingIsPublished": landing_published,
                            "landingPageIsPublished": landing_published,
                            "landing_page": {
                                "is_public": landing_public,
                                "is_published": landing_published,
                                "isPublished": landing_published,
                                "public": landing_public,
                            },
                        },
                    "service": {
                        "id": str(service.id),
                        "name": service.name,
                    "short_summary": service.short_summary,
                    "description": service.description,
                    "price": str(service.price),
                    "currency": KIS_COIN_CODE,
                    "delivery_modes": service.delivery_modes,
                    "duration_minutes": service.duration_minutes,
                    "coverage": service.coverage,
                    "availability_rules": service.availability_rules,
                    "status": service.status,
                    "visibility": service.visibility,
                    "rating_avg": service.rating_avg,
                    "rating_count": service.rating_count,
                    "images": service_images,
                    "membership_discount_pct": membership_discount_pct,
                    },
                    "booking": booking_map.get(str(service.id)),
                }
                service_items.append(service_payload)

        market_items = []
        if include_product_items:
            market_items.extend(product_items)
        if include_service_items:
            market_items.extend(service_items)
        market_items.sort(key=lambda entry: entry.get("broadcasted_at") or "", reverse=True)

        profile_items = []
        healthcare_items = []
        for item in profile_broadcasts:
            metadata = item.metadata or {}
            entry = metadata.get("entry") or {}
            attachments = []
            if isinstance(entry.get("attachment"), dict):
                attachments.append(entry["attachment"])
            attachments.extend([att for att in (entry.get("attachments") or []) if isinstance(att, dict)])
            text_plain = entry.get("summary") or entry.get("title") or ""
            profile_id = metadata.get("profile_id") or "main"
            profile_name = metadata.get("profile_name") or "My broadcast feed"
            author_payload = _build_author_payload(getattr(item, "broadcasted_by", None))
            if not author_payload:
                fallback_author_id = (
                    metadata.get("author_id")
                    or metadata.get("authorId")
                    or entry.get("author_id")
                    or entry.get("user_id")
                )
                author_payload = _build_author_payload_from_id(fallback_author_id)
            if not author_payload and isinstance(metadata.get("author"), dict):
                metadata_author = metadata.get("author") or {}
                metadata_author_from_user = _build_author_payload_from_id(
                    metadata_author.get("id") or metadata_author.get("user_id")
                )
                metadata_name = str(
                    metadata_author.get("display_name")
                    or metadata_author.get("name")
                    or ""
                ).strip()
                metadata_avatar = _absolutize_avatar(metadata_author.get("avatar_url"))
                metadata_bio = str(metadata_author.get("bio") or "").strip()
                author_payload = {
                    "id": str(metadata_author.get("id") or "") or None,
                    "display_name": metadata_name or "KIS user",
                }
                metadata_profile_id = str(metadata_author.get("profile_id") or metadata_author.get("profileId") or "").strip()
                if metadata_profile_id:
                    author_payload["profile_id"] = metadata_profile_id
                if metadata_avatar:
                    author_payload["avatar_url"] = metadata_avatar
                if metadata_bio:
                    author_payload["bio"] = metadata_bio
                if not author_payload.get("id"):
                    author_payload.pop("id", None)
                if metadata_author_from_user:
                    author_payload = {
                        **author_payload,
                        **metadata_author_from_user,
                    }
            if not author_payload and isinstance(entry.get("author"), dict):
                entry_author = entry.get("author") or {}
                entry_author_from_user = _build_author_payload_from_id(
                    entry_author.get("id") or entry_author.get("user_id")
                )
                entry_name = str(entry_author.get("display_name") or entry_author.get("name") or "").strip()
                entry_avatar = _absolutize_avatar(entry_author.get("avatar_url"))
                entry_bio = str(entry_author.get("bio") or "").strip()
                author_payload = {
                    "id": str(entry_author.get("id") or "") or None,
                    "display_name": entry_name or "KIS user",
                }
                entry_profile_id = str(entry_author.get("profile_id") or entry_author.get("profileId") or "").strip()
                if entry_profile_id:
                    author_payload["profile_id"] = entry_profile_id
                if entry_avatar:
                    author_payload["avatar_url"] = entry_avatar
                if entry_bio:
                    author_payload["bio"] = entry_bio
                if not author_payload.get("id"):
                    author_payload.pop("id", None)
                if entry_author_from_user:
                    author_payload = {
                        **author_payload,
                        **entry_author_from_user,
                    }

            health_card = metadata.get("health_card") if isinstance(metadata.get("health_card"), dict) else {}
            if health_card:
                institution_id = str(health_card.get("institution_id") or "").strip()
                card_id = _normalize_health_card_id(health_card.get("card_id"))
                card_exists = False
                institution = None
                if institution_id and card_id:
                    _, _, _, _, institution = _find_health_institution_owner_context(institution_id)
                    if institution:
                        card_exists = any(
                            _health_card_ids_match(card.get('id'), card_id)
                            for card in _build_health_cards_from_institution(institution)
                        )

                if not card_exists:
                    BroadcastItem.objects.filter(id=item.id).update(is_deleted=True)
                    continue

                membership = _resolve_institution_membership_settings(institution or {})
                owner_contact = institution.get('owner_contact') if isinstance(institution.get('owner_contact'), dict) else {}
                owner_user_id = str(owner_contact.get('userId') or owner_contact.get('user_id') or '').strip()
                role, is_member, _ = _resolve_institution_member_role(request.user, institution or {}, owner_user_id=owner_user_id)
                can_manage = role in {'owner', 'admin', 'manager'}

                logo_url = ''
                if isinstance(institution, dict):
                    logo_url = str(
                        institution.get('landingLogoUrl')
                        or (institution.get('profile_editor') or {}).get('landingLogoUrl')
                        or (institution.get('profileEditor') or {}).get('landingLogoUrl')
                        or ''
                    ).strip()

                enriched_health_card = {
                    **health_card,
                    'card_id': card_id,
                    'membership_open': bool(membership.get('open')),
                    'membership_discount_pct': int(membership.get('discountPercent') or 10),
                    'viewer_is_member': bool(is_member),
                    'viewer_can_manage': bool(can_manage),
                    'institution_logo_url': logo_url,
                }

                service_name = str(health_card.get("service_name") or entry.get("title") or "Health Service")
                service_description = str(health_card.get("service_description") or text_plain or "")
                healthcare_items.append(
                    {
                        "id": str(item.id),
                        "source_type": "healthcare",
                        "source_id": str(entry.get("id") or item.source_id),
                        "title": service_name,
                        "text": service_description,
                        "text_doc": service_description,
                        "text_plain": service_description,
                        "attachments": attachments,
                        "created_at": entry.get("created_at") or entry.get("updated_at") or item.broadcasted_at.isoformat(),
                        "broadcasted_at": item.broadcasted_at.isoformat(),
                        "expires_at": item.expires_at.isoformat(),
                        "comment_conversation_id": str(item.comment_conversation_id) if item.comment_conversation_id else None,
                        "reaction_count": reaction_counts.get(item.id, 0),
                        "viewer_reaction": viewer_reactions.get(item.id),
                        "health_card": enriched_health_card,
                        "source": {
                            "type": "healthcare",
                            "id": str(health_card.get("institution_id") or profile_id),
                            "name": str(health_card.get("institution_name") or profile_name),
                            "is_subscribed": True,
                            "can_open": True,
                        },
                    }
                )
                continue

            profile_items.append(
                {
                    "id": str(item.id),
                    "source_type": "broadcast_profile",
                    "source_id": str(entry.get("id") or item.source_id),
                    "title": entry.get("title") or profile_name,
                    "text": text_plain,
                    "text_doc": entry.get("summary") or "",
                    "text_plain": text_plain,
                    "attachments": attachments,
                    "created_at": entry.get("created_at") or entry.get("updated_at") or item.broadcasted_at.isoformat(),
                    "broadcasted_at": item.broadcasted_at.isoformat(),
                    "expires_at": item.expires_at.isoformat(),
                    "comment_conversation_id": str(item.comment_conversation_id) if item.comment_conversation_id else None,
                    "reaction_count": reaction_counts.get(item.id, 0),
                    "viewer_reaction": viewer_reactions.get(item.id),
                    "author": author_payload,
                    "source": {
                        "type": "broadcast_profile",
                        "id": profile_id,
                        "name": profile_name,
                        "is_subscribed": True,
                        "can_open": True,
                    },
                }
            )

        education_profile_broadcasts = [
            item for item in broadcast_items if item.source_type == BroadcastSourceType.EDUCATION_PROFILE
        ]
        course_broadcasts = [
            item for item in broadcast_items if item.source_type == BroadcastSourceType.EDUCATION_COURSE
        ]
        course_items = []
        education_profile_items = []
        for item in education_profile_broadcasts:
            metadata = item.metadata or {}
            profile_id = metadata.get("profile_id") or ""
            profile_name = metadata.get("profile_name") or "Education Profile"
            summary_parts = []
            course_count = metadata.get("course_count")
            module_count = metadata.get("module_count")
            if course_count:
                summary_parts.append(f"{course_count} courses")
            if module_count:
                summary_parts.append(f"{module_count} modules")
            summary_text = metadata.get("description") or ", ".join(summary_parts) or profile_name
            education_profile_items.append(
                {
                    "id": str(item.id),
                    "source_type": "education_profile",
                    "source_id": profile_id or str(item.source_id),
                    "title": profile_name,
                    "text": summary_text,
                    "text_doc": summary_text,
                    "text_plain": summary_text,
                    "attachments": [],
                    "created_at": item.created_at.isoformat(),
                    "broadcasted_at": item.broadcasted_at.isoformat(),
                    "expires_at": item.expires_at.isoformat(),
                    "comment_conversation_id": str(item.comment_conversation_id) if item.comment_conversation_id else None,
                    "reaction_count": reaction_counts.get(item.id, 0),
                    "viewer_reaction": viewer_reactions.get(item.id),
                    "courses": metadata.get("courses") or [],
                    "modules": metadata.get("modules") or [],
                    "source": {
                        "type": "education_profile",
                        "id": profile_id,
                        "name": profile_name,
                        "can_open": True,
                    },
                }
            )
        for item in course_broadcasts:
            metadata = item.metadata or {}
            cover_url = metadata.get("cover_image") or metadata.get("cover_url")
            attachments = []
            if cover_url:
                attachments.append({"url": cover_url})
            course_items.append(
                {
                    "id": str(item.id),
                    "source_type": "education_course",
                    "source_id": str(item.source_id),
                    "title": metadata.get("title") or "Course",
                    "text": metadata.get("summary") or "",
                    "text_doc": metadata.get("summary") or "",
                    "text_plain": metadata.get("summary") or "",
                    "attachments": attachments,
                    "created_at": item.created_at.isoformat(),
                    "broadcasted_at": item.broadcasted_at.isoformat(),
                    "expires_at": item.expires_at.isoformat(),
                    "comment_conversation_id": str(item.comment_conversation_id) if item.comment_conversation_id else None,
                    "reaction_count": reaction_counts.get(item.id, 0),
                    "viewer_reaction": viewer_reactions.get(item.id),
                    "source": {
                        "type": metadata.get("source") or "education_profile",
                        "id": metadata.get("partner_id"),
                        "name": metadata.get("partner_name") or "Education",
                    },
                    "course": {
                        "id": metadata.get("id") or item.source_id,
                        "partner_id": metadata.get("partner_id"),
                        "partner_name": metadata.get("partner_name"),
                        "price_amount": metadata.get("price_amount"),
                        "price_currency": metadata.get("price_currency"),
                    },
                }
            )

        items = (
            channel_items
            + community_items
            + partner_items
            + market_items
            + profile_items
            + healthcare_items
            + education_profile_items
            + course_items
        )
        profile = get_affinity_profile(request.user)
        metadata = {item["id"]: {"profile": profile} for item in items}
        ranked_items = rank_feed_items(items, request.user, feed_type="broadcast", metadata_map=metadata)
        log_feed_interaction(request.user, "broadcast", "feed_impression", weight=0.05)

        return Response({"results": ranked_items[:limit]})


class BroadcastChannelMessageView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        conversation_id = request.data.get("conversation_id") or request.data.get("conversationId")
        message_ids = request.data.get("message_ids") or request.data.get("messageIds") or []
        if not conversation_id or not isinstance(message_ids, list) or not message_ids:
            return Response({"detail": "conversation_id and message_ids are required."}, status=400)

        try:
            conversation = Conversation.objects.get(id=conversation_id)
        except Conversation.DoesNotExist:
            return Response({"detail": "Conversation not found."}, status=404)

        if conversation.type != ConversationType.CHANNEL:
            return Response({"detail": "Only channel conversations can broadcast messages."}, status=400)

        member = ConversationMember.objects.filter(
            conversation=conversation,
            user=request.user,
            left_at__isnull=True,
            is_blocked=False,
        ).first()
        if not member:
            return Response({"detail": "Join the channel to broadcast messages."}, status=403)

        conv_settings = ConversationSettings.objects.filter(conversation=conversation).first()
        if conv_settings and conv_settings.send_policy == ConversationSendPolicy.ADMINS_ONLY:
            if member.base_role not in (BaseConversationRole.OWNER, BaseConversationRole.ADMIN):
                return Response({"detail": "Only admins can broadcast from this channel."}, status=403)

        channel = Channel.objects.filter(conversation=conversation).first()

        valid_message_ids = [str(mid) for mid in message_ids if mid]
        if getattr(settings, "NEST_INTERNAL_URL", "") and getattr(settings, "NEST_INTERNAL_TOKEN", ""):
            fetched = _fetch_channel_messages(
                [str(conversation_id)],
                timezone.now() - timedelta(days=30),
                limit=len(valid_message_ids),
                message_ids=valid_message_ids,
                conversation_id=str(conversation_id),
            )
            valid_message_ids = [
                str(msg.get("id") or msg.get("_id"))
                for msg in fetched
                if str(msg.get("id") or msg.get("_id")) in valid_message_ids
            ]

        if not valid_message_ids:
            return Response({"detail": "No valid messages found for broadcast."}, status=400)

        created = []
        expires_at = timezone.now() + timedelta(days=10)
        for msg_id in valid_message_ids:
            item, _ = BroadcastItem.objects.update_or_create(
                source_type=BroadcastSourceType.CHANNEL_MESSAGE,
                source_id=str(msg_id),
                defaults={
                    "conversation_id": conversation.id,
                    "channel": channel,
                    "broadcasted_by": request.user,
                    "broadcasted_at": timezone.now(),
                    "expires_at": expires_at,
                    "is_deleted": False,
                },
            )
            created.append(str(item.id))

        return Response({"broadcast_ids": created}, status=200)


class BroadcastReactView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk=None):
        try:
            item = BroadcastItem.objects.get(id=pk, is_deleted=False)
        except BroadcastItem.DoesNotExist:
            return Response({"detail": "Broadcast item not found."}, status=404)

        emoji = request.data.get("emoji") or "❤️"
        existing = BroadcastReaction.objects.filter(broadcast_item=item, user=request.user).first()
        reacted = True
        if existing:
            if existing.emoji == emoji:
                existing.delete()
                reacted = False
            else:
                existing.emoji = emoji
                existing.save(update_fields=["emoji"])
        else:
            BroadcastReaction.objects.create(broadcast_item=item, user=request.user, emoji=emoji)

        count = BroadcastReaction.objects.filter(broadcast_item=item).count()
        return Response(
            {"reacted": reacted, "emoji": emoji, "count": count},
            status=200,
        )


class BroadcastCommentRoomView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk=None):
        try:
            item = BroadcastItem.objects.get(id=pk, is_deleted=False)
        except BroadcastItem.DoesNotExist:
            return Response({"detail": "Broadcast item not found."}, status=404)

        conversation = item.comment_conversation
        if not conversation:
            title = "Broadcast comments"
            conversation = Conversation.objects.create(
                type=ConversationType.POST,
                title=title,
                description=f"Comments for broadcast item {item.id}",
                created_by=request.user,
            )
            ConversationSettings.objects.get_or_create(
                conversation=conversation,
                defaults={
                    "send_policy": ConversationSendPolicy.ALL_MEMBERS,
                    "join_policy": ChatConversationJoinPolicy.OPEN,
                },
            )
            item.comment_conversation = conversation
            item.save(update_fields=["comment_conversation"])

        ConversationMember.objects.get_or_create(
            conversation=conversation,
            user=request.user,
            defaults={"base_role": BaseConversationRole.MEMBER},
        )

        return Response(
            {"conversation_id": str(conversation.id), "title": conversation.title},
            status=200,
        )


class BroadcastShareView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk=None):
        try:
            item = BroadcastItem.objects.get(id=pk, is_deleted=False)
        except BroadcastItem.DoesNotExist:
            return Response({"detail": "Broadcast item not found."}, status=404)

        platform = request.data.get("platform") or "app"
        payload = {
            "broadcast_id": str(item.id),
            "shared_by": str(request.user.id),
            "platform": platform,
            "at": timezone.now().isoformat(),
        }
        logger.info("[broadcasts] share recorded: %s", payload)

        return Response({"shared": True, "platform": platform}, status=status.HTTP_200_OK)


class BroadcastFeatureListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        features = BroadcastFeature.objects.all()
        serializer = BroadcastFeatureSerializer(features, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ChannelFeatureView(APIView):
    permission_classes = [IsAuthenticated]

    def get_channel(self, channel_id):
        return get_object_or_404(Channel, id=channel_id, is_archived=False)

    def ensure_channel_permissions(self, channel, user):
        if channel.owner != user and not user.is_staff:
            raise PermissionDenied("Only the channel owner or staff can modify features.")

    def check_permissions(self, request, *args, **kwargs):
        """Wrap DRF's check_permissions to accept optional legacy parameters."""
        return super().check_permissions(request)

    def build_payload(self, channel):
        features = BroadcastFeature.objects.all()
        flags = {
            flag.feature.slug: flag.enabled
            for flag in BroadcastFeatureFlag.objects.filter(channel=channel)
        }
        serialized = []
        for feature in features:
            serialized.append(
                {
                    "slug": feature.slug,
                    "name": feature.name,
                    "description": feature.description,
                    "category": feature.category,
                    "default_enabled": feature.default_enabled,
                    "enabled": flags.get(feature.slug, feature.default_enabled),
                }
            )
        return {
            "channel": {"id": str(channel.id), "name": channel.name},
            "features": serialized,
        }

    def get(self, request, channel_id):
        channel = self.get_channel(channel_id)
        self.ensure_channel_permissions(channel, request.user)
        return Response(self.build_payload(channel), status=status.HTTP_200_OK)

    def patch(self, request, channel_id):
        channel = self.get_channel(channel_id)
        self.ensure_channel_permissions(channel, request.user)
        flags_payload = request.data.get("flags")
        if not isinstance(flags_payload, list):
            raise ValidationError({"flags": "A list of feature updates is required."})

        features = {
            feature.slug: feature for feature in BroadcastFeature.objects.all()
        }

        updated = []
        for entry in flags_payload:
            slug = entry.get("slug")
            enabled = bool(entry.get("enabled"))
            feature = features.get(slug)
            if not feature:
                continue
            flag, _ = BroadcastFeatureFlag.objects.update_or_create(
                feature=feature,
                channel=channel,
                defaults={
                    "enabled": enabled,
                },
            )
            updated.append(flag)

        return Response(self.build_payload(channel), status=status.HTTP_200_OK)


class BroadcastVideoListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        video_type = (request.query_params.get("type") or "").lower()
        qs = BroadcastVideo.objects.filter(is_active=True)
        if video_type in ("short", "video"):
            qs = qs.filter(type=video_type)
        videos = qs.order_by("-created_at")[:40]
        serializer = BroadcastVideoSerializer(videos, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class BroadcastVideoUploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        serializer = BroadcastVideoUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        file_obj = serializer.validated_data["file"]
        title = serializer.validated_data.get("title") or os.path.splitext(file_obj.name or "")[0] or "Broadcast video"
        description = serializer.validated_data.get("description", "")
        thumbnail_url = serializer.validated_data.get("thumbnail_url", "")
        thumbnail_file = serializer.validated_data.get("thumbnail")
        if thumbnail_file:
            thumbnail_url = _store_thumbnail_upload(thumbnail_file)
        channel = None
        channel_id = serializer.validated_data.get("channel_id")
        if channel_id:
            channel = Channel.objects.filter(id=channel_id).first()
        relative_path, _ = _store_upload(file_obj, user=request.user)
        absolute_path = os.path.join(getattr(settings, "MEDIA_ROOT", "media"), relative_path)
        duration = _probe_video_duration(absolute_path)
        video_type = "short" if duration < LONG_VIDEO_MIN_SECONDS else "video"
        transcript_segments = _sanitize_transcript_segments(serializer.validated_data.get("transcript_segments", []))
        video = BroadcastVideo.objects.create(
            title=title,
            description=description,
            channel=channel,
            creator=request.user,
            video_url="",
            thumbnail_url=thumbnail_url or "",
            mime_type=file_obj.content_type or "",
            storage_path=relative_path,
            type=video_type,
            duration_seconds=int(round(duration)),
            transcript_segments=transcript_segments,
        )
        video.video_url = build_media_url(request, relative_path)
        ensure_local_thumbnail(video)
        video.save(update_fields=["video_url"])
        return Response(
            BroadcastVideoSerializer(video, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class BroadcastVideoStreamView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, video_id):
        video = get_object_or_404(BroadcastVideo, id=video_id, is_active=True)

        # 1) Real filesystem path (for open/exists)
        file_path = os.path.join(settings.MEDIA_ROOT, video.storage_path)

        if not os.path.exists(file_path):
            raise Http404("Video not found.")

        # 2) Public URL (for the client/browser)
        return self._serve_video(request, file_path, video)

    def _serve_video(self, request, file_path, video):
        file_size = os.path.getsize(file_path)
        range_header = request.headers.get("Range", "").strip()
        mime_type = video.mime_type or "video/mp4"

        if not range_header:
            resp = FileResponse(
                open(file_path, "rb"),
                content_type=mime_type,
            )
            resp["Content-Length"] = str(file_size)
            resp["Content-Disposition"] = f'inline; filename="{os.path.basename(file_path)}"'
            resp["X-Video-URL"] = request.build_absolute_uri(settings.MEDIA_URL + video.storage_path)
            resp["Accept-Ranges"] = "bytes"
            return resp

        match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if not match:
            raise Http404("Invalid range header.")

        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else file_size - 1
        if end >= file_size:
            end = file_size - 1
        length = end - start + 1

        with open(file_path, "rb") as fh:
            fh.seek(start)
            chunk = fh.read(length)

        resp = HttpResponse(
            chunk,
            status=206,
            content_type=mime_type,
        )
        resp["Content-Length"] = str(length)
        resp["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        resp["Accept-Ranges"] = "bytes"
        resp["Content-Disposition"] = f'inline; filename="{os.path.basename(file_path)}"'
        resp["X-Video-URL"] = request.build_absolute_uri(settings.MEDIA_URL + video.storage_path)
        return resp

class BroadcastLessonListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        lesson_type = (request.query_params.get("type") or "").lower()
        partner_id = request.query_params.get("partner_id")
        community_id = request.query_params.get("community_id")

        lessons = BroadcastLesson.objects.filter(is_public=True)
        if lesson_type in dict(BroadcastLesson.LESSON_TYPES):
            lessons = lessons.filter(lesson_type=lesson_type)
        if partner_id:
            lessons = lessons.filter(partner_id=partner_id)
        if community_id:
            lessons = lessons.filter(community_id=community_id)

        lessons = (
            lessons.select_related("partner", "community")
            .annotate(enrollment_count=models.Count("enrollments"))
            .order_by("-starts_at", "-created_at")
        )

        serializer = BroadcastLessonSerializer(
            lessons,
            many=True,
            context={"request": request},
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


class LessonEnrollmentListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        enrollments = (
            LessonEnrollment.objects.filter(user=request.user)
            .select_related("lesson", "lesson__partner", "lesson__community")
            .order_by("-enrolled_at")
        )
        serializer = LessonEnrollmentSerializer(
            enrollments,
            many=True,
            context={"request": request},
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


class LessonEnrollmentActionView(APIView):
    permission_classes = [IsAuthenticated]

    def get_lesson(self, lesson_id):
        return get_object_or_404(BroadcastLesson, id=lesson_id, is_public=True)

    def ensure_lesson_memberships(self, lesson, user, enrollment):
        modified_fields = set()
        if lesson.partner:
            membership, created = PartnerMembership.objects.get_or_create(
                partner=lesson.partner,
                user=user,
                defaults={
                    "status": PartnerMembershipStatus.MEMBER,
                    "lesson_access_only": True,
                },
            )
            if created and not membership.lesson_access_only:
                membership.lesson_access_only = True
                membership.save(update_fields=["lesson_access_only"])
            if enrollment.partner_membership_id != membership.id:
                enrollment.partner_membership_id = membership.id
                modified_fields.add("partner_membership_id")

        if lesson.community:
            membership, created = CommunityMembership.objects.get_or_create(
                community=lesson.community,
                user=user,
                defaults={
                    "role": CommunityRole.MEMBER,
                    "lesson_access_only": True,
                },
            )
            if created and not membership.lesson_access_only:
                membership.lesson_access_only = True
                membership.save(update_fields=["lesson_access_only"])
            if enrollment.community_membership_id != membership.id:
                enrollment.community_membership_id = membership.id
                modified_fields.add("community_membership_id")

        if modified_fields:
            enrollment.save(update_fields=list(modified_fields))

    def post(self, request, lesson_id):
        lesson = self.get_lesson(lesson_id)
        enrollment, created = LessonEnrollment.objects.get_or_create(
            lesson=lesson,
            user=request.user,
            defaults={"status": LessonEnrollmentStatus.ENROLLED},
        )
        self.ensure_lesson_memberships(lesson, request.user, enrollment)
        serializer = LessonEnrollmentSerializer(enrollment, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    def delete(self, request, lesson_id):
        enrollment = get_object_or_404(
            LessonEnrollment,
            lesson_id=lesson_id,
            user=request.user,
        )
        enrollment.status = LessonEnrollmentStatus.CANCELLED
        enrollment.save(update_fields=["status"])
        return Response(status=status.HTTP_204_NO_CONTENT)


TIER_ORDER = ['free', 'basic', 'pro', 'business', 'market pro', 'business pro', 'partner', 'partner pro']
TIER_ALIASES = {
    'market pro': 'business pro',
}

HEALTH_SYSTEM_FEATURES = [
    'Telemedicine scheduling + reminders',
    'Electronic health records with audit trail',
    'Patient portal + secure messaging',
    'Appointment & queue management dashboard',
    'Inventory tracking for consumables & meds',
    'Referrals + specialist routing',
    'Care team collaboration workspaces',
    'Health analytics + population insights',
    'Compliance-ready documentation tooling',
    'Emergency triage + escalation workflows',
    'Telehealth triage automation with decision support',
    'Medication adherence + refill alerts',
    'Credential verification + licensing status dashboards',
    'Billing & insurance reconciliation workflows',
    'Clinical event logging + reporting',
    'Patient satisfaction scoring + outreach campaigns',
    'Wellness challenge + habit tracking programs',
    'Secure document exchange + e-signatures',
    'Referral network heatmaps',
    'Regulatory reporting dashboards',
]


def _normalize_tier(label: str | None) -> str:
    if not label:
        return ''
    normalized = str(label).strip().lower()
    return TIER_ALIASES.get(normalized, normalized)


def _tier_rank(label: str | None) -> int:
    normalized = _normalize_tier(label)
    try:
        return TIER_ORDER.index(normalized)
    except ValueError:
        return 0


def _is_tier_at_least(current: str | None, required: str) -> bool:
    return _tier_rank(current) >= _tier_rank(required)


def _resolve_profile_limit(
    user,
    feature_key: str,
    *,
    legacy_required_tier: str,
    permission_message: str,
):
    """
    Return normalized profile limit:
      - int: finite limit
      - None: unlimited

    If the feature key does not exist yet for a user tier, fall back to legacy
    tier-name checks to keep backward compatibility.
    """
    features = get_user_tier_features(user)
    if feature_key in features:
        normalized = normalize_limit_value(features.get(feature_key), default=0)
        if normalized is not None and normalized <= 0:
            raise PermissionDenied(permission_message)
        return normalized
    if not _is_tier_at_least(user.tier, legacy_required_tier):
        raise PermissionDenied(permission_message)
    return None


class ProfileCreationView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "broadcast_profile_create"

    def get(self, request):
        profiles = _load_user_profiles(request.user, include_member_institutions=True)
        return Response({'profiles': profiles}, status=status.HTTP_200_OK)

    def _charge_credits(self, user, amount, reference):
        if amount <= 0:
            return
        credit_account = get_credit_account(user)
        if credit_account.credits < amount:
            raise ValidationError({'credits': 'Insufficient credits to cover profile extras.'})
        record_ledger(
            user=user,
            kind='profile_create',
            amount_cents=0,
            credits_delta=-int(amount),
            reference=reference,
        )

    def _handle_broadcast_feed(self, user, profiles, payload):
        now = timezone.now()
        expires_at = now + timedelta(days=10)
        existing = profiles.get('broadcast_feed') or {}
        profiles['broadcast_feed'] = {
            'title': payload.get('title') or existing.get('title') or 'Scheduled broadcast feed',
            'profile_name': existing.get('profile_name') or payload.get('profile_name') or 'Broadcast feed',
            'notes': payload.get('notes') or existing.get('notes') or 'Feeds expire after 10 days.',
            'created_at': existing.get('created_at') or now.isoformat(),
            'updated_at': now.isoformat(),
            'expires_at': expires_at.isoformat(),
            'max_duration_days': 10,
            'feeds': existing.get('feeds', []),
        }
        return profiles['broadcast_feed']

    def _handle_health_profile(self, user, profiles, payload):
        institution_limit = _resolve_profile_limit(
            user,
            "health_profiles",
            legacy_required_tier="business pro",
            permission_message="Health profiles require Market Pro / Business Pro access.",
        )

        health = dict(profiles.get('health') or {})
        existing_institutions = _ensure_institution_data(health.get('institutions', []))
        institutions_payload = payload.get('institutions') or []
        if not institutions_payload:
            raise ValidationError({'institutions': 'At least one institution is required.'})

        sanitized_institutions = [inst for inst in [_sanitize_institution(entry) for entry in institutions_payload] if inst]
        if not sanitized_institutions:
            raise ValidationError({'institutions': 'Provide valid institution data.'})
        if institution_limit is not None:
            total_after_create = len(existing_institutions) + len(sanitized_institutions)
            if total_after_create > institution_limit:
                raise ValidationError(
                    {
                        "institutions": (
                            f"Your current tier allows up to {institution_limit} health institution profile"
                            f"{'' if institution_limit == 1 else 's'}."
                        )
                    }
                )

        extras_cost = max(0, len(existing_institutions) + len(sanitized_institutions) - 2) * 10
        employees_cost = sum(
            max(0, len(inst.get('employees', [])) - 5) * 5 for inst in sanitized_institutions
        )
        total_cost = extras_cost + employees_cost
        if total_cost > 0:
            self._charge_credits(user, total_cost, 'health_profile_extras')

        updated_institutions = existing_institutions + sanitized_institutions
        health['institutions'] = updated_institutions
        health = _ensure_health_profile_structure(health)
        health['profile_name'] = payload.get('profile_name') or health.get('profile_name') or 'Health Profile'
        health['features'] = HEALTH_SYSTEM_FEATURES
        health['updated_at'] = timezone.now().isoformat()
        profiles['health'] = health
        return {
            'profile': health,
            'credit_spent': total_cost,
        }

    def _handle_market_profile(self, user, profiles, payload):
        _resolve_profile_limit(
            user,
            "market_profiles",
            legacy_required_tier="business",
            permission_message="Market profiles require Business tier or higher.",
        )
        market = dict(profiles.get('market') or {})
        existing_shops = _ensure_shop_data(market.get('shops', []))
        shop_entries = payload.get('shops') or []
        if not shop_entries:
            raise ValidationError({'shops': 'At least one shop is required to build a market profile.'})

        sanitized_shops = [shop for shop in (_sanitize_shop(entry) for entry in shop_entries) if shop]
        if not sanitized_shops:
            raise ValidationError({'shops': 'Provide at least one valid shop.'})

        product_cost = sum(max(0, _count_shop_products(shop) - 20) * 2 for shop in sanitized_shops)
        total_shops = len(existing_shops) + len(sanitized_shops)
        extras_cost = max(0, total_shops - 5) * 5
        total_cost = extras_cost + product_cost
        if total_cost > 0:
            self._charge_credits(user, total_cost, 'market_profile_slots')

        market['shops'] = existing_shops + sanitized_shops
        market = _apply_landing_builder_updates(market, payload)
        market = _ensure_market_profile_structure(market)
        market['profile_name'] = payload.get('profile_name') or market.get('profile_name') or 'Market Profile'
        market['updated_at'] = timezone.now().isoformat()
        profiles['market'] = market
        return {
            'profile': market,
            'credit_spent': total_cost,
        }

    def _handle_education_profile(self, user, profiles, payload):
        _resolve_profile_limit(
            user,
            "education_profiles",
            legacy_required_tier="business pro",
            permission_message="Education profiles require Business Pro tier or higher.",
        )
        education = dict(profiles.get('education') or {})
        existing_courses = _ensure_course_data(education.get('courses', []))
        course_entries = payload.get('courses') or []
        valid_courses = [course for course in (_sanitize_course(entry) for entry in course_entries) if course]
        if not valid_courses:
            raise ValidationError({'courses': 'Provide at least one course title.'})

        total_courses = len(existing_courses) + len(valid_courses)
        extra_courses = max(0, total_courses - 10)
        total_cost = extra_courses * 2
        if total_cost > 0:
            self._charge_credits(user, total_cost, 'education_profile_expand')

        education['courses'] = existing_courses + valid_courses
        education = _apply_landing_builder_updates(education, payload)
        education = _ensure_education_profile_structure(education)
        education['profile_name'] = payload.get('profile_name') or education.get('profile_name') or 'Education Profile'
        education['updated_at'] = timezone.now().isoformat()
        profiles['education'] = education
        return {
            'profile': education,
            'credit_spent': total_cost,
        }

    def post(self, request):
        profile_type = (request.data.get('profile_type') or '').strip().lower()
        payload = request.data.get('payload') or {}
        if not profile_type:
            raise ValidationError({'profile_type': 'Required.'})

        handler_mapping = {
            'broadcast_feed': self._handle_broadcast_feed,
            'health_profile': self._handle_health_profile,
            'market_profile': self._handle_market_profile,
            'education_profile': self._handle_education_profile,
        }
        handler = handler_mapping.get(profile_type)
        if not handler:
            raise ValidationError({'profile_type': 'Unknown profile type.'})

        profiles = _load_user_profiles(request.user)
        result = handler(request.user, profiles, payload)
        _save_user_profiles(request.user, profiles)
        profiles['health'] = _ensure_health_profile_structure(profiles.get('health') or {})
        profiles['market'] = _ensure_market_profile_structure(profiles.get('market') or {})
        profiles['education'] = _ensure_education_profile_structure(profiles.get('education') or {})
        return Response({'profiles': profiles, 'result': result}, status=status.HTTP_200_OK)


def _guess_media_type_from_mime(mime: str | None) -> str:
    if not mime:
        return 'file'
    mime = mime.lower()
    if mime.startswith('video/'):
        return 'video'
    if mime.startswith('audio/'):
        return 'audio'
    if mime.startswith('image/'):
        return 'image'
    if mime in {'application/pdf', 'text/plain', 'application/msword'}:
        return 'file'
    return 'file'


def _build_feed_attachment(request, file_obj):
    if not file_obj:
        return None
    upload_user = request.user if request and getattr(request, "user", None) else None
    rel_path, bytes_written = _store_upload(file_obj, user=upload_user)
    url = build_media_url(request, rel_path) if request else rel_path
    media_type = _guess_media_type_from_mime(file_obj.content_type)

    attachment = {
        'url': url,
        'path': rel_path,
        'mime_type': file_obj.content_type or '',
        'name': file_obj.name,
        'size': bytes_written,
        'media_type': media_type,
    }

    if media_type == 'video' and request and getattr(request, 'user', None):
        video = BroadcastVideo.objects.create(
            title=file_obj.name or 'Broadcast video',
            description='',
            channel=None,
            creator=request.user if request.user.is_authenticated else None,
            video_url=url,
            thumbnail_url='',
            mime_type=file_obj.content_type or '',
            storage_path=rel_path,
            type='video',
            duration_seconds=int(round(_probe_video_duration(os.path.join(getattr(settings, "MEDIA_ROOT", "media"), rel_path)))),
        )
        attachment['stream_url'] = request.build_absolute_uri(
            reverse('broadcasts:video-stream', kwargs={'video_id': str(video.id)})
        )
        attachment['video_id'] = str(video.id)

    return attachment


def _build_profile_attachment(request, file_obj):
    attachment = _build_feed_attachment(request, file_obj)
    if attachment:
        attachment['profile_attachment'] = True
    return attachment


def _collect_feed_files(request):
    files = []
    if hasattr(request.FILES, 'getlist'):
        files.extend(request.FILES.getlist('attachments'))
    else:
        files.extend(request.FILES.get('attachments') or [])
    media_file = request.FILES.get('media_file')
    if media_file:
        files.append(media_file)
    return [file for file in files if file]


def _parse_media_options(value: object | None) -> dict:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError):
            return {}
    if isinstance(value, dict):
        return value
    return {}


def _build_feed_attachments(request, file_objects):
    attachments = []
    for file_obj in file_objects:
        attachment = _build_feed_attachment(request, file_obj)
        if attachment:
            attachments.append(attachment)
    return attachments


MEDIA_OPTION_DEFAULTS: dict[str, dict] = {
    'video': {
        'thumbnailAttachmentKey': '',
        'thumbnailLabel': '',
        'autoPlay': False,
        'showBadge': True,
    },
    'audio': {
        'waveformStyle': 'classic',
        'episodeNotes': '',
        'audioMood': 'uplifting',
        'hasTranscript': True,
    },
    'image': {
        'borderStyle': 'none',
        'layout': 'portrait',
        'captionTone': '',
        'overlayColor': 'transparent',
    },
    'file': {
        'categoryLabel': 'General resources',
        'secureDownload': False,
        'visibilityNote': '',
        'expiryDays': '7',
    },
    'text': {
        'bold': False,
        'italic': False,
        'underline': False,
        'strikethrough': False,
        'alignment': 'left',
        'fontSize': 'md',
        'highlightColor': 'transparent',
    },
}


def _merge_media_options(media_type: str, existing: dict | None) -> dict:
    defaults = dict(MEDIA_OPTION_DEFAULTS.get(media_type, MEDIA_OPTION_DEFAULTS['text']))
    if not isinstance(existing, dict):
        return defaults
    for key, value in existing.items():
        if key in defaults:
            defaults[key] = value
    return defaults


def _resolve_feed_entry(user, entry_id: str) -> dict:
    profiles = _load_user_profiles(user)
    profile = profiles.get('broadcast_feed')
    if not profile:
        raise ValidationError({'detail': 'Create a broadcast feed profile first.'})
    feeds = list(profile.get('feeds') or [])
    for index, entry in enumerate(feeds):
        if str(entry.get('id')) == str(entry_id):
            return {
                'profiles': profiles,
                'profile': profile,
                'feeds': feeds,
                'index': index,
                'entry': entry,
            }
    raise ValidationError({'detail': 'Feed item not found.'})


class BroadcastFeedEntriesView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [FormParser, MultiPartParser]

    def get(self, request):
        profiles = _load_user_profiles(request.user)
        profile = profiles.get('broadcast_feed')
        return Response(
            {
                'profile': profile,
                'feeds': profile.get('feeds', []) if profile else [],
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        title = (request.data.get('title') or '').strip()
        summary = (request.data.get('summary') or '').strip()
        media_type_input = (request.data.get('media_type') or '').strip().lower()
        files = _collect_feed_files(request)
        attachments = _build_feed_attachments(request, files)
        media_options = _parse_media_options(request.data.get('media_options'))
        if not title:
            raise ValidationError({'title': 'Title is required.'})
        profiles = _load_user_profiles(request.user)
        profile = profiles.get('broadcast_feed')
        if not profile:
            raise ValidationError({'detail': 'Create a broadcast feed profile first.'})
        media_type = media_type_input if media_type_input in {'video', 'audio', 'image', 'file', 'text'} else ''
        if not media_type and attachments:
            media_type = attachments[0].get('media_type', 'file')
        if not media_type:
            media_type = 'text'

        media_options = _merge_media_options(media_type, media_options)

        feeds = list(profile.get('feeds', []))
        profile_row = getattr(request.user, "profile", None)
        avatar_url = ""
        bio = ""
        profile_row_id = ""
        if profile_row is not None:
            profile_row_id = str(getattr(profile_row, "id", "") or "").strip()
            avatar_url = str(getattr(profile_row, "avatar_url", "") or "").strip()
            if not avatar_url and getattr(profile_row, "avatar_file", None):
                try:
                    avatar_url = str(profile_row.avatar_file.url or "").strip()
                except Exception:
                    avatar_url = ""
            bio = str(getattr(profile_row, "bio", "") or "").strip()
        feed_entry = {
            'id': str(uuid.uuid4()),
            'title': title,
            'summary': summary,
            'media_type': media_type,
            'attachments': attachments,
            'attachment': attachments[0] if attachments else None,
            'created_at': timezone.now().isoformat(),
            'media_options': media_options,
            'author': {
                'id': str(request.user.id),
                'profile_id': profile_row_id or None,
                'display_name': str(request.user.display_name or request.user.username or request.user.phone or 'KIS user'),
                'avatar_url': avatar_url,
                'bio': bio,
            },
        }
        feeds.append(feed_entry)
        profile['feeds'] = feeds
        profile['updated_at'] = timezone.now().isoformat()
        profiles['broadcast_feed'] = profile
        _save_user_profiles(request.user, profiles)
        return Response({'profile': profile, 'feeds': feeds, 'feed': feed_entry}, status=status.HTTP_201_CREATED)


class BroadcastFeedEntryDetailView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [FormParser, MultiPartParser]

    def _resolve_entry(self, request, entry_id: str) -> dict:
        return _resolve_feed_entry(request.user, entry_id)

    def get(self, request, entry_id: str):
        resolved = self._resolve_entry(request, entry_id)
        return Response({'feed': resolved['entry'], 'profile': resolved['profile']}, status=status.HTTP_200_OK)

    def patch(self, request, entry_id: str):
        resolved = self._resolve_entry(request, entry_id)
        entry = resolved['entry']
        data = request.data
        title = (data.get('title') or entry.get('title') or '').strip()
        summary = (data.get('summary') or entry.get('summary') or '').strip()
        media_type_input = (data.get('media_type') or '').strip().lower()

        files = _collect_feed_files(request)
        new_attachments = _build_feed_attachments(request, files)
        media_options_input = data.get('media_options')
        media_options = _parse_media_options(media_options_input)
        if media_options_input is None:
            media_options = entry.get('media_options') or {}

        retain_raw = data.get('retain_attachments')
        retained = []
        if retain_raw:
            try:
                parsed = json.loads(retain_raw)
                if isinstance(parsed, list):
                    retained.extend([item for item in parsed if isinstance(item, dict)])
            except (TypeError, ValueError):
                pass

        existing = list(entry.get('attachments') or [])
        if not existing and entry.get('attachment'):
            existing = [entry.get('attachment')]
        if not retain_raw:
            retained = existing

        final_attachments = [att for att in retained if isinstance(att, dict)] + new_attachments

        first_attachment = final_attachments[0] if final_attachments else None

        media_type = ''
        if media_type_input in {'video', 'audio', 'image', 'file', 'text'}:
            media_type = media_type_input
        elif new_attachments:
            media_type = new_attachments[0].get('media_type', entry.get('media_type', 'file'))
        elif first_attachment:
            media_type = first_attachment.get('media_type', entry.get('media_type', 'file'))
        else:
            media_type = entry.get('media_type', 'text')

        media_options = _merge_media_options(media_type, media_options)

        updated_entry = {
            **entry,
            'title': title,
            'summary': summary,
            'media_type': media_type,
            'attachments': final_attachments,
            'attachment': first_attachment,
            'updated_at': timezone.now().isoformat(),
            'media_options': media_options,
        }
        feeds = resolved['feeds']
        feeds[resolved['index']] = updated_entry
        resolved['profile']['feeds'] = feeds
        resolved['profile']['updated_at'] = timezone.now().isoformat()
        resolved['profiles']['broadcast_feed'] = resolved['profile']
        _save_user_profiles(request.user, resolved['profiles'])
        return Response({'feed': updated_entry, 'feeds': feeds, 'profile': resolved['profile']}, status=status.HTTP_200_OK)

    def delete(self, request, entry_id: str):
        resolved = self._resolve_entry(request, entry_id)
        feeds = resolved['feeds']
        feeds.pop(resolved['index'])
        resolved['profile']['feeds'] = feeds
        resolved['profile']['updated_at'] = timezone.now().isoformat()
        resolved['profiles']['broadcast_feed'] = resolved['profile']
        _delete_user_broadcast(request.user, entry_id)
        _save_user_profiles(request.user, resolved['profiles'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class BroadcastFeedEntryAttachmentDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def _attachment_key(self, attachment: dict[str, Any] | None) -> str:
        if not attachment:
            return ''
        return str(attachment.get('path') or attachment.get('url') or attachment.get('name') or attachment.get('id') or '')

    def _normalize_requested_key(self, key: str | None, entry_id: str | None) -> str:
        if not key:
            return ''
        normalized = str(key).strip()
        if entry_id and normalized.startswith(f"{entry_id}:"):
            normalized = normalized[len(str(entry_id)) + 1 :]
        return normalized

    def delete(self, request, entry_id: str):
        attachment_key = request.query_params.get('key') or request.data.get('key')
        if not attachment_key:
            raise ValidationError({'key': 'Attachment key is required.'})

        resolved = _resolve_feed_entry(request.user, entry_id)
        entry = resolved['entry']
        attachments = list(entry.get('attachments') or [])
        if not attachments and entry.get('attachment'):
            attachments = [entry.get('attachment')]

        if len(attachments) <= 1:
            raise ValidationError(
                {
                    'detail': 'Cannot remove the last attachment. Use the delete feed endpoint if you want to remove the entire item.',
                }
            )

        normalized_key = self._normalize_requested_key(attachment_key, entry_id)
        filtered = [att for att in attachments if self._attachment_key(att) != normalized_key]
        if len(filtered) == len(attachments):
            raise ValidationError({'detail': 'Attachment not found.'})

        # update primary attachment placeholder
        entry['attachments'] = filtered
        entry['attachment'] = filtered[0] if filtered else None
        entry['updated_at'] = timezone.now().isoformat()
        feeds = resolved['feeds']
        feeds[resolved['index']] = entry
        resolved['profile']['feeds'] = feeds
        resolved['profile']['updated_at'] = timezone.now().isoformat()
        resolved['profiles']['broadcast_feed'] = resolved['profile']
        _save_user_profiles(request.user, resolved['profiles'])
        return Response({'feed': entry, 'feeds': feeds, 'profile': resolved['profile']}, status=status.HTTP_200_OK)


class BroadcastFeedEntryBroadcastView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, entry_id: str):
        resolved = _resolve_feed_entry(request.user, entry_id)
        entry = resolved['entry']
        profile = resolved['profile']
        entry_updated = {
            **entry,
            'is_broadcast': True,
            'broadcasted_at': timezone.now().isoformat(),
        }
        feeds = resolved['feeds']
        feeds[resolved['index']] = entry_updated
        profile['feeds'] = feeds
        profile['updated_at'] = timezone.now().isoformat()
        resolved['profiles']['broadcast_feed'] = profile
        _save_user_profiles(request.user, resolved['profiles'])
        try:
            profile_row = getattr(request.user, "profile", None)
            profile_id = str(getattr(profile_row, "id", "") or "").strip() if profile_row is not None else ""
            avatar_url = str(getattr(profile_row, "avatar_url", "") or "").strip() if profile_row is not None else ""
            if profile_row is not None and not avatar_url and getattr(profile_row, "avatar_file", None):
                try:
                    avatar_url = str(profile_row.avatar_file.url or "").strip()
                except Exception:
                    avatar_url = ""
            BroadcastItem.objects.update_or_create(
                source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
                source_id=str(entry.get('id')),
                defaults={
                    'broadcasted_by': request.user,
                    'broadcasted_at': timezone.now(),
                    'expires_at': timezone.now() + timedelta(days=10),
                    'is_deleted': False,
                    'metadata': {
                        'entry': entry_updated,
                        'profile_id': str(profile.get('id') or 'main'),
                        'profile_name': profile.get('name') or profile.get('label') or 'My broadcast feed',
                        'author': {
                            'id': str(request.user.id),
                            'profile_id': profile_id or None,
                            'display_name': str(request.user.display_name or request.user.username or request.user.phone or 'KIS user'),
                            'avatar_url': avatar_url,
                            'bio': str(getattr(profile_row, "bio", "") or "").strip() if profile_row is not None else "",
                        },
                    },
                },
            )
        except Exception:
            pass
        return Response({'detail': 'Feed entry broadcasted.'}, status=status.HTTP_200_OK)


class BroadcastSubscribeView(APIView):
    permission_classes = [IsAuthenticated]

    def _ensure_partner_subscription(self, partner_id, user):
        partner = get_object_or_404(Partner, id=partner_id)
        config = getattr(partner, "join_config", None)
        if config and not config.get("allow_subscribe", True):
            raise PermissionDenied("Subscriptions are disabled for this partner.")

        PartnerMembership.objects.update_or_create(
            partner=partner,
            user=user,
            defaults={"status": PartnerMembershipStatus.SUBSCRIBER, "role": "subscriber"},
        )

        if getattr(partner, "main_conversation_id", None):
            ConversationMember.objects.get_or_create(
                conversation_id=partner.main_conversation_id,
                user=user,
                defaults={"base_role": BaseConversationRole.READONLY},
            )

        return partner

    def _ensure_channel_subscription(self, channel_id=None, conversation_id=None, user=None):
        channel = None
        if channel_id:
            channel = Channel.objects.filter(id=channel_id).first()
        if not channel and conversation_id:
            channel = Channel.objects.filter(conversation_id=conversation_id).first()
        if not channel:
            raise ValidationError({"detail": "Channel not found."})
        member, created = ConversationMember.objects.get_or_create(
            conversation=channel.conversation,
            user=user,
            left_at__isnull=True,
            defaults={"base_role": BaseConversationRole.MEMBER},
        )
        return channel, created

    def _ensure_community_subscription(self, community_id, user):
        community = get_object_or_404(Community, id=community_id)
        membership, created = CommunityMembership.objects.get_or_create(
            community=community,
            user=user,
            defaults={"role": CommunityRole.MEMBER, "left_at": None, "is_banned": False},
        )
        if not created:
            membership.left_at = None
            membership.is_banned = False
            membership.save(update_fields=["left_at", "is_banned"])
        return community, membership

    def _ensure_profile_subscription(self, user):
        profiles = _load_user_profiles(user)
        subs = profiles.get("subscriptions") or []
        exists = any(sub for sub in subs if sub.get("type") == "broadcast_feed" and sub.get("id") == "main")
        if not exists:
            subs.append({"type": "broadcast_feed", "id": "main", "created_at": timezone.now().isoformat()})
            profiles["subscriptions"] = subs
            _save_user_profiles(user, profiles)
        return profiles

    def post(self, request):
        target_type = (request.data.get("target_type") or "").strip().lower()
        target_id = request.data.get("target_id")
        conversation_id = request.data.get("conversation_id")

        if not target_type:
            raise ValidationError({"target_type": "Required."})

        if target_type == "partner":
            if not target_id:
                raise ValidationError({"target_id": "Partner id is required."})
            self._ensure_partner_subscription(target_id, request.user)
            return Response({"subscribed": True, "target_type": "partner"}, status=status.HTTP_200_OK)

        if target_type == "channel":
            channel, created = self._ensure_channel_subscription(target_id, conversation_id, request.user)
            return Response(
                {
                    "subscribed": True,
                    "target_type": "channel",
                    "channel_id": str(channel.id),
                    "created": created,
                },
                status=status.HTTP_200_OK,
            )

        if target_type == "community":
            if not target_id:
                raise ValidationError({"target_id": "Community id is required."})
            community, _ = self._ensure_community_subscription(target_id, request.user)
            return Response(
                {"subscribed": True, "target_type": "community", "community_id": str(community.id)}, status=status.HTTP_200_OK
            )

        if target_type == "broadcast_profile":
            profiles = self._ensure_profile_subscription(request.user)
            return Response(
                {"subscribed": True, "target_type": "broadcast_profile", "profiles": profiles}, status=status.HTTP_200_OK
            )

        raise ValidationError({"target_type": "Unknown subscription type."})


class EducationProfileListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        profiles = _serialize_education_profiles(request.user)
        return Response({'profiles': profiles}, status=status.HTTP_200_OK)

    @transaction.atomic
    def post(self, request):
        education_limit = _resolve_profile_limit(
            request.user,
            "education_profiles",
            legacy_required_tier="business pro",
            permission_message="Education profiles require Business Pro tier or higher.",
        )
        if education_limit is not None:
            current_count = EducationProfile.objects.filter(user=request.user).count()
            if current_count >= education_limit:
                raise ValidationError(
                    {
                        "detail": (
                            f"Your current tier allows up to {education_limit} education profile"
                            f"{'' if education_limit == 1 else 's'}."
                        )
                    }
                )

        name = str(request.data.get('name') or '').strip()
        if not name:
            raise ValidationError({'name': 'Profile name is required.'})

        profile_type = request.data.get('profile_type') or EducationProfileType.COURSE
        if profile_type not in EducationProfileType.values:
            profile_type = EducationProfileType.COURSE

        description = str(request.data.get('description') or '').strip()
        metadata = request.data.get('metadata')
        metadata = metadata if isinstance(metadata, dict) else {}
        make_default = bool(request.data.get('is_default') or request.data.get('make_default'))

        if make_default:
            _reset_default_education_profile(request.user)

        account_profile, _ = Profile.objects.get_or_create(user=request.user)

        profile = EducationProfile.objects.create(
            user=request.user,
            profile=account_profile,
            name=name,
            description=description,
            profile_type=profile_type,
            metadata=metadata,
            is_default=make_default,
        )
        _replace_profile_courses(profile, request.data.get('courses') or [])
        _replace_profile_modules(profile, request.data.get('modules') or [])
        _replace_profile_roles(profile, request.data.get('roles') or [])

        if not make_default and not EducationProfile.objects.filter(user=request.user, is_default=True).exists():
            profile.is_default = True
            profile.save(update_fields=['is_default'])

        serializer = EducationProfileSerializer(profile)
        return Response({'profile': serializer.data}, status=status.HTTP_201_CREATED)


class EducationProfileDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, profile_id: str):
        profile = _get_education_profile_or_404(request.user, profile_id)
        serializer = EducationProfileSerializer(profile)
        return Response({'profile': serializer.data}, status=status.HTTP_200_OK)

    @transaction.atomic
    def patch(self, request, profile_id: str):
        profile = _get_education_profile_or_404(request.user, profile_id)
        updates = request.data
        name = updates.get('name')
        if name and isinstance(name, str):
            profile.name = name.strip() or profile.name
        description = updates.get('description')
        if isinstance(description, str):
            profile.description = description.strip()
        profile_type = updates.get('profile_type')
        if profile_type in EducationProfileType.values:
            profile.profile_type = profile_type
        metadata = updates.get('metadata')
        if isinstance(metadata, dict):
            profile.metadata = metadata
        if updates.get('is_default') or updates.get('make_default'):
            _reset_default_education_profile(request.user)
            profile.is_default = True
        _replace_profile_courses(profile, updates.get('courses') or [])
        _replace_profile_modules(profile, updates.get('modules') or [])
        _replace_profile_roles(profile, updates.get('roles') or [])
        profile.save()
        serializer = EducationProfileSerializer(profile)
        return Response({'profile': serializer.data}, status=status.HTTP_200_OK)

    def delete(self, request, profile_id: str):
        profile = _get_education_profile_or_404(request.user, profile_id)
        if profile.is_default:
            _reset_default_education_profile(request.user)
            others = EducationProfile.objects.filter(user=request.user).exclude(id=profile.id)
            next_default = others.first()
            if next_default:
                next_default.is_default = True
                next_default.save(update_fields=['is_default'])
        profile.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProfileAttachmentUploadView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "broadcast_profile_attachment"

    def post(self, request):
        file_obj = request.FILES.get('attachment')
        if not file_obj:
            raise ValidationError({'attachment': 'Attachment is required.'})
        attachment = _build_profile_attachment(request, file_obj)
        return Response({'attachment': attachment}, status=status.HTTP_201_CREATED)




class HealthMediumListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        mediums = [
            medium
            for medium in Medium.objects.all().order_by('name')
            if not is_removed_health_medium_name(getattr(medium, 'name', ''))
        ]
        serializer = MediumSerializer(mediums, many=True)
        return Response({'results': serializer.data}, status=status.HTTP_200_OK)

    def post(self, request):
        return Response(
            {'detail': 'Health engines are fixed and cannot be created.'},
            status=status.HTTP_403_FORBIDDEN,
        )


class HealthMediumDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _check_access(self, request, institution_id: str):
        if not institution_id:
            raise ValidationError({'institution_id': 'institution_id is required.'})

        health_profile, _, _, _, institution = _find_health_institution_owner_context(institution_id, preferred_user=request.user)
        if not institution:
            raise Http404

        owner_user_id = str(getattr(getattr(health_profile, 'profile', None), 'user_id', '') or '')
        role, _, _ = _resolve_institution_member_role(request.user, institution, owner_user_id=owner_user_id)
        if role not in {'owner', 'admin', 'manager'}:
            raise PermissionDenied('Not allowed.')

    def patch(self, request, medium_id: str):
        return Response(
            {'detail': 'Health engines are fixed and cannot be edited.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    def delete(self, request, medium_id: str):
        return Response(
            {'detail': 'Health engines are fixed and cannot be deleted.'},
            status=status.HTTP_403_FORBIDDEN,
        )



class HealthServiceListView(APIView):
    permission_classes = [IsAuthenticated]

    def _check_access(self, request, institution_id: str):
        if not institution_id:
            raise ValidationError({'institution_id': 'institution_id is required.'})

        health_profile, _, _, _, institution = _find_health_institution_owner_context(institution_id, preferred_user=request.user)
        if not institution:
            raise Http404

        owner_user_id = str(getattr(getattr(health_profile, 'profile', None), 'user_id', '') or '')
        role, _, _ = _resolve_institution_member_role(request.user, institution, owner_user_id=owner_user_id)
        if role not in {'owner', 'admin', 'manager'}:
            raise PermissionDenied('Not allowed.')

    def get(self, request):
        qs = Service.objects.filter(models.Q(is_default=True) | models.Q(created_by=request.user)).prefetch_related('medium_links__medium').order_by('name', 'created_at')
        serializer = ServiceSerializer(qs, many=True)
        rows = [row for row in serializer.data if row.get('medium_ids')]
        return Response({'results': rows}, status=status.HTTP_200_OK)

    @transaction.atomic
    def post(self, request):
        institution_id = str(request.data.get('institution_id') or '').strip()
        self._check_access(request, institution_id)

        name = str(request.data.get('name') or '').strip()
        description = str(request.data.get('description') or '').strip()
        medium_ids = request.data.get('medium_ids') or request.data.get('mediumIds') or []

        if not name:
            return Response({'detail': 'name is required.'}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(medium_ids, list) or len(medium_ids) == 0:
            return Response({'detail': 'At least one engine is required.'}, status=status.HTTP_400_BAD_REQUEST)

        normalized_medium_ids = [str(item).strip() for item in medium_ids if str(item).strip()]
        mediums = list(Medium.objects.filter(id__in=normalized_medium_ids))
        if len(mediums) != len(set(normalized_medium_ids)):
            return Response({'detail': 'One or more selected engines are invalid.'}, status=status.HTTP_400_BAD_REQUEST)
        if any(is_blocked_service_medium_name(getattr(medium, 'name', '')) for medium in mediums):
            return Response(
                {'detail': 'Selected engines are coming up and cannot be attached to services yet.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing = Service.objects.filter(name__iexact=name, created_by=request.user).first()
        if existing:
            return Response({'detail': 'You already created a service with this name.'}, status=status.HTTP_409_CONFLICT)

        service = Service.objects.create(
            name=name,
            description=description,
            is_default=False,
            created_by=request.user,
        )

        ServiceMediumMap.objects.bulk_create(
            [ServiceMediumMap(service=service, medium=medium) for medium in mediums],
            ignore_conflicts=True,
        )

        service.refresh_from_db()
        serializer = ServiceSerializer(Service.objects.filter(id=service.id).prefetch_related('medium_links__medium').first())
        return Response({'service': serializer.data}, status=status.HTTP_201_CREATED)


class HealthServiceDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _check_access(self, request, institution_id: str):
        if not institution_id:
            raise ValidationError({'institution_id': 'institution_id is required.'})

        health_profile, _, _, _, institution = _find_health_institution_owner_context(institution_id, preferred_user=request.user)
        if not institution:
            raise Http404

        owner_user_id = str(getattr(getattr(health_profile, 'profile', None), 'user_id', '') or '')
        role, _, _ = _resolve_institution_member_role(request.user, institution, owner_user_id=owner_user_id)
        if role not in {'owner', 'admin', 'manager'}:
            raise PermissionDenied('Not allowed.')

    @transaction.atomic
    def patch(self, request, service_id: str):
        institution_id = str(request.data.get('institution_id') or request.query_params.get('institution_id') or '').strip()
        self._check_access(request, institution_id)

        service = get_object_or_404(Service, id=service_id)
        if service.is_default:
            return Response({'detail': 'Default services cannot be modified.'}, status=status.HTTP_403_FORBIDDEN)
        if str(service.created_by_id or '') != str(request.user.id):
            return Response({'detail': 'Only the creator can modify this service.'}, status=status.HTTP_403_FORBIDDEN)

        name = request.data.get('name')
        description = request.data.get('description')
        medium_ids = request.data.get('medium_ids') or request.data.get('mediumIds')

        updates = []
        if isinstance(name, str) and name.strip():
            service.name = name.strip()
            updates.append('name')
        if isinstance(description, str):
            service.description = description.strip()
            updates.append('description')

        if updates:
            service.save(update_fields=updates)

        if medium_ids is not None:
            if not isinstance(medium_ids, list) or len(medium_ids) == 0:
                return Response({'detail': 'At least one engine is required.'}, status=status.HTTP_400_BAD_REQUEST)
            normalized_medium_ids = [str(item).strip() for item in medium_ids if str(item).strip()]
            mediums = list(Medium.objects.filter(id__in=normalized_medium_ids))
            if len(mediums) != len(set(normalized_medium_ids)):
                return Response({'detail': 'One or more selected engines are invalid.'}, status=status.HTTP_400_BAD_REQUEST)
            if any(is_blocked_service_medium_name(getattr(medium, 'name', '')) for medium in mediums):
                return Response(
                    {'detail': 'Selected engines are coming up and cannot be attached to services yet.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            ServiceMediumMap.objects.filter(service=service).delete()
            ServiceMediumMap.objects.bulk_create(
                [ServiceMediumMap(service=service, medium=medium) for medium in mediums],
                ignore_conflicts=True,
            )

        serializer = ServiceSerializer(Service.objects.filter(id=service.id).prefetch_related('medium_links__medium').first())
        return Response({'service': serializer.data}, status=status.HTTP_200_OK)

    @transaction.atomic
    def delete(self, request, service_id: str):
        institution_id = str(request.data.get('institution_id') or request.query_params.get('institution_id') or '').strip()
        self._check_access(request, institution_id)

        service = get_object_or_404(Service, id=service_id)
        if service.is_default:
            return Response({'detail': 'Default services cannot be deleted.'}, status=status.HTTP_403_FORBIDDEN)
        if str(service.created_by_id or '') != str(request.user.id):
            return Response({'detail': 'Only the creator can delete this service.'}, status=status.HTTP_403_FORBIDDEN)

        ServiceMediumMap.objects.filter(service=service).delete()
        service.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)



class HealthInstitutionCardsView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = []

    def _build_response(self, institution: dict, user, phone_override: str | None = None, owner_user_id: str | None = None):
        role, is_member, checked_phone = _resolve_institution_member_role(user, institution, phone_override, owner_user_id=owner_user_id)
        can_manage = role in {'owner', 'admin', 'manager'}
        viewer_credit = get_credit_account(user)
        viewer_wallet = get_wallet_account(user)

        membership = _resolve_institution_membership_settings(institution)
        cards = _build_health_cards_from_institution(institution)
        valid_card_ids = {
            _normalize_health_card_id(card.get('id'))
            for card in cards
            if _normalize_health_card_id(card.get('id'))
        }
        broadcasted_ids = institution.get('broadcasted_health_cards') if isinstance(institution.get('broadcasted_health_cards'), list) else []
        if not broadcasted_ids and isinstance(institution.get('broadcastedHealthCards'), list):
            broadcasted_ids = institution.get('broadcastedHealthCards')
        broadcasted_set = {
            normalized_id
            for item in broadcasted_ids
            for normalized_id in [_normalize_health_card_id(item)]
            if normalized_id and normalized_id in valid_card_ids
        }

        visible_cards = cards if (is_member or can_manage) else [
            card
            for card in cards
            if _normalize_health_card_id(card.get('id')) in broadcasted_set
        ]

        service_ratings = _sanitize_service_ratings(institution.get('service_ratings') or institution.get('serviceRatings'))
        raw_engine_executions = institution.get('engine_executions') if isinstance(institution.get('engine_executions'), list) else []
        if not raw_engine_executions and isinstance(institution.get('engineExecutions'), list):
            raw_engine_executions = institution.get('engineExecutions')
        engine_executions = []
        for row in raw_engine_executions[:50]:
            if not isinstance(row, dict):
                continue
            engine_executions.append({
                'id': str(row.get('id') or _ensure_entry_id(None, 'engine')),
                'engine': str(row.get('engine') or ''),
                'service_id': str(row.get('service_id') or row.get('serviceId') or ''),
                'service_name': str(row.get('service_name') or row.get('serviceName') or ''),
                'status': str(row.get('status') or 'executed'),
                'created_at': str(row.get('created_at') or row.get('createdAt') or timezone.now().isoformat()),
            })

        raw_service_sessions = institution.get('service_sessions') if isinstance(institution.get('service_sessions'), list) else []
        if not raw_service_sessions and isinstance(institution.get('serviceSessions'), list):
            raw_service_sessions = institution.get('serviceSessions')
        service_sessions = []
        viewer_id = str(getattr(user, 'id', '') or '')
        for row in raw_service_sessions[:200]:
            if not isinstance(row, dict):
                continue
            row_user_id = str(row.get('user_id') or row.get('userId') or '')
            if not can_manage and row_user_id and row_user_id != viewer_id:
                continue
            legacy_required_micro = int(row.get('required_micro') or row.get('requiredMicro') or 0)
            required_cents = _to_cents(
                row.get('required_cents')
                or row.get('requiredCents')
                or round(legacy_required_micro / 10)
            )
            service_sessions.append({
                'id': str(row.get('id') or _ensure_entry_id(None, 'session')),
                'card_id': _normalize_health_card_id(row.get('card_id') or row.get('cardId')),
                'service_id': str(row.get('service_id') or row.get('serviceId') or ''),
                'service_name': str(row.get('service_name') or row.get('serviceName') or ''),
                'status': str(row.get('status') or 'started'),
                'price_cents': int(row.get('price_cents') or row.get('priceCents') or 0),
                'required_cents': required_cents,
                'required_usd': str(
                    row.get('required_usd')
                    or row.get('requiredUsd')
                    or cents_to_usd(required_cents)
                ),
                'required_usd_compact': str(
                    row.get('required_usd_compact')
                    or row.get('requiredUsdCompact')
                    or cents_to_usd_compact(required_cents)
                ),
                'required_credits': int(row.get('required_credits') or row.get('requiredCredits') or 0),
                'payment_mode': str(row.get('payment_mode') or row.get('paymentMode') or 'wallet'),
                'owner_preview': bool(row.get('owner_preview') or row.get('ownerPreview') or False),
                'paid': bool(row.get('paid') or False),
                'started_at': str(row.get('started_at') or row.get('startedAt') or ''),
                'completed_at': str(row.get('completed_at') or row.get('completedAt') or ''),
            })

        return {
            'institution': {
                'id': str(institution.get('id') or ''),
                'name': str(institution.get('name') or ''),
                'type': str(institution.get('type') or 'clinic'),
            },
            'viewer': {
                'user_id': str(getattr(user, 'id', '') or ''),
                'role': role,
                'is_member': is_member,
                'can_manage': can_manage,
                'phone_checked': checked_phone,
                'credit_balance': int(getattr(viewer_credit, 'credits', 0) or 0),
                'wallet_cents': int(getattr(viewer_wallet, 'balance_cents', 0) or 0),
                'wallet_usd': str(cents_to_usd(int(getattr(viewer_wallet, 'balance_cents', 0) or 0))),
                'wallet_usd_compact': cents_to_usd_compact(int(getattr(viewer_wallet, 'balance_cents', 0) or 0)),
            },
            'membership': membership,
            'ratings': service_ratings,
            'engine_executions': engine_executions,
            'service_sessions': service_sessions,
            'broadcasted_card_ids': list(broadcasted_set),
            'broadcastedCardIds': list(broadcasted_set),
            'cards': [
                {
                    **card,
                    'isBroadcasted': _normalize_health_card_id(card.get('id')) in broadcasted_set,
                }
                for card in visible_cards
            ],
        }

    def get(self, request, institution_id: str):
        phone = str(request.query_params.get('phone') or '').strip()
        health_profile, payload, institutions, idx, institution = _find_health_institution_owner_context(institution_id, preferred_user=request.user)
        if not institution:
            return Response({'detail': 'Institution not found.'}, status=status.HTTP_404_NOT_FOUND)

        institutions[idx] = _sanitize_institution(institution) or institution
        institution = institutions[idx]

        owner_user_id = str(getattr(getattr(health_profile, 'profile', None), 'user_id', '') or '')
        return Response(self._build_response(institution, request.user, phone_override=phone, owner_user_id=owner_user_id), status=status.HTTP_200_OK)

    @transaction.atomic
    def post(self, request, institution_id: str):
        action = str(request.data.get('action') or '').strip().lower()
        phone = str(request.data.get('phone') or request.query_params.get('phone') or '').strip()

        health_profile, payload, institutions, idx, institution = _find_health_institution_owner_context(institution_id, preferred_user=request.user)
        if not institution or not payload or institutions is None or idx < 0 or not health_profile:
            return Response({'detail': 'Institution not found.'}, status=status.HTTP_404_NOT_FOUND)

        institution = _sanitize_institution(institution) or institution
        owner_user_id = str(getattr(getattr(health_profile, 'profile', None), 'user_id', '') or '')
        role, is_member, _ = _resolve_institution_member_role(request.user, institution, phone_override=phone, owner_user_id=owner_user_id)
        can_manage = role in {'owner', 'admin', 'manager'}

        if action == 'join':
            membership = _resolve_institution_membership_settings(institution)
            if not membership.get('open'):
                return Response({'detail': 'Membership is closed.'}, status=status.HTTP_403_FORBIDDEN)

            members = institution.get('members') if isinstance(institution.get('members'), list) else []
            user_id = str(request.user.id)
            user_phone = str(request.user.phone or '').strip()
            exists = any(
                str(member.get('userId') or member.get('user_id') or '') == user_id
                for member in members
                if isinstance(member, dict)
            )
            if not exists:
                members.append({
                    'id': f'user-{user_id}',
                    'userId': user_id,
                    'name': str(request.user.display_name or request.user.username or 'Member'),
                    'phone': user_phone,
                    'email': str(request.user.email or ''),
                    'role': 'member',
                    'source': 'subscription',
                    'permissions': HEALTH_MEMBER_ROLE_PERMISSIONS['member'],
                })
                institution['members'] = members

        elif action == 'set_membership':
            if not can_manage:
                return Response({'detail': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)
            open_value = bool(request.data.get('open'))
            discount_raw = request.data.get('discountPercent')
            try:
                discount_value = int(float(discount_raw if discount_raw is not None else 10))
            except (TypeError, ValueError):
                discount_value = 10
            discount_value = max(10, min(100, discount_value))

            institution['membership_open'] = open_value
            institution['membershipOpen'] = open_value
            institution['membership_discount_pct'] = discount_value
            institution['membershipDiscountPct'] = discount_value
            institution['membership_settings'] = {'open': open_value, 'discountPercent': discount_value}
            institution['membershipSettings'] = {'open': open_value, 'discountPercent': discount_value}

        elif action == 'rate':
            service_id = str(request.data.get('serviceId') or '').strip()
            service_name = str(request.data.get('serviceName') or '').strip()
            if not service_id:
                return Response({'detail': 'serviceId is required.'}, status=status.HTTP_400_BAD_REQUEST)
            try:
                rating_value = int(float(request.data.get('rating')))
            except (TypeError, ValueError):
                return Response({'detail': 'rating must be between 1 and 5.'}, status=status.HTTP_400_BAD_REQUEST)
            rating_value = max(1, min(5, rating_value))

            ratings = _sanitize_service_ratings(institution.get('service_ratings') or institution.get('serviceRatings'))
            user_id = str(request.user.id)
            now_iso = timezone.now().isoformat()
            found = False
            for row in ratings:
                if str(row.get('serviceId') or '') == service_id and str(row.get('userId') or '') == user_id:
                    row['rating'] = rating_value
                    row['serviceName'] = service_name or row.get('serviceName') or ''
                    row['service_name'] = row['serviceName']
                    row['userName'] = str(request.user.display_name or request.user.username or 'User')
                    row['user_name'] = row['userName']
                    row['updatedAt'] = now_iso
                    row['updated_at'] = now_iso
                    found = True
                    break
            if not found:
                row = {
                    'id': _ensure_entry_id(None, 'rating'),
                    'serviceId': service_id,
                    'service_id': service_id,
                    'serviceName': service_name,
                    'service_name': service_name,
                    'userId': user_id,
                    'user_id': user_id,
                    'userName': str(request.user.display_name or request.user.username or 'User'),
                    'user_name': str(request.user.display_name or request.user.username or 'User'),
                    'rating': rating_value,
                    'createdAt': now_iso,
                    'created_at': now_iso,
                    'updatedAt': now_iso,
                    'updated_at': now_iso,
                }
                ratings.append(row)

            institution['service_ratings'] = ratings
            institution['serviceRatings'] = ratings

        elif action == 'broadcast_card':
            if not can_manage:
                return Response({'detail': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)

            card_id = _normalize_health_card_id(request.data.get('cardId'))
            enabled = bool(request.data.get('enabled', True))
            if not card_id:
                return Response({'detail': 'cardId is required.'}, status=status.HTTP_400_BAD_REQUEST)

            cards = _build_health_cards_from_institution(institution)
            target_card, resolved_card_id = _resolve_health_card(cards, card_id, request.data)
            if not target_card:
                stale_removed = _mark_stale_health_card_broadcasts(institution.get('id'), card_id)
                logger.info(
                    'Health card lookup miss for broadcast: institution=%s requested=%s available=%s stale_removed=%s',
                    str(institution.get('id') or ''),
                    card_id,
                    [str(item.get('id') or '') for item in cards[:30]],
                    stale_removed,
                )
                return Response({'detail': 'Card not found.'}, status=status.HTTP_404_NOT_FOUND)

            broadcasted = institution.get('broadcasted_health_cards') if isinstance(institution.get('broadcasted_health_cards'), list) else []
            if not broadcasted and isinstance(institution.get('broadcastedHealthCards'), list):
                broadcasted = institution.get('broadcastedHealthCards')
            broadcasted_set = {
                normalized_id
                for item in broadcasted
                for normalized_id in [_normalize_health_card_id(item)]
                if normalized_id
            }

            source_id = f"health-card:{institution.get('id')}:{resolved_card_id}"
            existing_active = BroadcastItem.objects.filter(
                source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
                source_id=source_id,
                is_deleted=False,
            ).exists()

            if enabled:
                if resolved_card_id in broadcasted_set or existing_active:
                    return Response({'detail': 'Card already broadcasted.'}, status=status.HTTP_409_CONFLICT)
                broadcasted_set.add(resolved_card_id)
            else:
                broadcasted_set.discard(resolved_card_id)

            institution['broadcasted_health_cards'] = list(broadcasted_set)
            institution['broadcastedHealthCards'] = list(broadcasted_set)

            if enabled:
                payload_metadata = _build_health_card_broadcast_payload(institution, target_card)
                BroadcastItem.objects.update_or_create(
                    source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
                    source_id=source_id,
                    defaults={
                        'broadcasted_by': request.user,
                        'broadcasted_at': timezone.now(),
                        'expires_at': timezone.now() + timedelta(days=10),
                        'is_deleted': False,
                        'metadata': payload_metadata,
                    },
                )
            else:
                BroadcastItem.objects.filter(
                    source_type=BroadcastSourceType.BROADCAST_FEED_ENTRY,
                    source_id=source_id,
                ).update(is_deleted=True)

        elif action == 'start_service_session':
            card_id = _normalize_health_card_id(request.data.get('cardId') or request.data.get('card_id'))
            if not card_id:
                return Response({'detail': 'cardId is required.'}, status=status.HTTP_400_BAD_REQUEST)

            cards = _build_health_cards_from_institution(institution)
            target_card, resolved_card_id = _resolve_health_card(cards, card_id, request.data)
            if not target_card:
                stale_removed = _mark_stale_health_card_broadcasts(institution.get('id'), card_id)
                logger.info(
                    'Health card lookup miss for booking: institution=%s requested=%s available=%s stale_removed=%s',
                    str(institution.get('id') or ''),
                    card_id,
                    [str(item.get('id') or '') for item in cards[:30]],
                    stale_removed,
                )
                return Response({'detail': 'Card not found.'}, status=status.HTTP_404_NOT_FOUND)

            if not (is_member or can_manage):
                return Response({'detail': 'Only members can book this service.'}, status=status.HTTP_403_FORBIDDEN)

            service = target_card.get('service') if isinstance(target_card.get('service'), dict) else {}
            membership = _resolve_institution_membership_settings(institution)
            base_price_cents = 0
            try:
                base_price_cents = int(float(service.get('basePriceCents') or service.get('base_price_cents') or 0))
            except (TypeError, ValueError):
                base_price_cents = 0
            base_price_cents = max(0, base_price_cents)

            discount_percent = int(membership.get('discountPercent') or 10) if is_member else 0
            discount_percent = max(0, min(100, discount_percent))
            payable_cents = int(round(base_price_cents * (100 - discount_percent) / 100)) if base_price_cents > 0 else 0
            required_cents = _to_cents(payable_cents)
            required_credits = cents_to_credits(payable_cents) if payable_cents > 0 else 0
            owner_preview = _to_bool(request.data.get('ownerPreview') or request.data.get('owner_preview'), False)
            if owner_preview and role != 'owner':
                return Response({'detail': 'Owner preview is only available to the institution owner.'}, status=status.HTTP_403_FORBIDDEN)

            viewer_wallet = get_wallet_account(request.user)
            available_cents = int(getattr(viewer_wallet, 'balance_cents', 0) or 0)
            if required_cents > available_cents and not owner_preview:
                return Response(
                    {
                        'detail': 'Insufficient KIS wallet balance.',
                        'required_credits': required_credits,
                        'available_credits': int(getattr(get_credit_account(request.user), 'credits', 0) or 0),
                        'required_cents': required_cents,
                        'available_cents': available_cents,
                        'required_usd': str(cents_to_usd(required_cents)),
                        'required_usd_compact': cents_to_usd_compact(required_cents),
                        'available_usd': str(cents_to_usd(available_cents)),
                        'available_usd_compact': cents_to_usd_compact(available_cents),
                        'price_cents': payable_cents,
                        'price_usd': str(cents_to_usd(payable_cents)),
                        'price_usd_compact': cents_to_usd_compact(payable_cents),
                    },
                    status=status.HTTP_402_PAYMENT_REQUIRED,
                )

            owner_user, owner_user_id_resolved = _resolve_institution_owner_user(institution, owner_user_id_hint=owner_user_id)
            now_iso = timezone.now().isoformat()
            session_id = _ensure_entry_id(None, 'session')
            charged = False
            if required_cents > 0 and not owner_preview:
                charge_meta = {
                    'institution_id': str(institution.get('id') or ''),
                    'service_id': str(service.get('id') or ''),
                    'service_name': str(service.get('name') or ''),
                    'card_id': resolved_card_id,
                    'payment_mode': 'wallet_usd',
                    'amount_usd': str(cents_to_usd(required_cents)),
                    'amount_usd_compact': cents_to_usd_compact(required_cents),
                }
                record_ledger(
                    user=request.user,
                    kind='purchase',
                    amount_cents=-required_cents,
                    reference=f'broadcast_health_session:{session_id}',
                    meta=charge_meta,
                )
                if owner_user and str(getattr(owner_user, 'id', '')) and str(owner_user.id) != str(request.user.id):
                    record_ledger(
                        user=owner_user,
                        kind='transfer_in',
                        amount_cents=required_cents,
                        reference=f'broadcast_health_session:{session_id}:from:{request.user.id}',
                        meta={
                            **charge_meta,
                            'source_user_id': str(request.user.id),
                        },
                    )
                charged = True

            session_row = {
                'id': session_id,
                'card_id': resolved_card_id,
                'cardId': resolved_card_id,
                'service_id': str(service.get('id') or ''),
                'serviceId': str(service.get('id') or ''),
                'service_name': str(service.get('name') or ''),
                'serviceName': str(service.get('name') or ''),
                'status': 'started',
                'started_at': now_iso,
                'startedAt': now_iso,
                'completed_at': '',
                'completedAt': '',
                'price_cents': payable_cents,
                'priceCents': payable_cents,
                'required_cents': required_cents,
                'requiredCents': required_cents,
                'required_usd': str(cents_to_usd(required_cents)),
                'requiredUsd': str(cents_to_usd(required_cents)),
                'required_usd_compact': cents_to_usd_compact(required_cents),
                'requiredUsdCompact': cents_to_usd_compact(required_cents),
                'required_credits': required_credits,
                'requiredCredits': required_credits,
                'payment_mode': 'owner_preview' if owner_preview else 'wallet',
                'paymentMode': 'owner_preview' if owner_preview else 'wallet',
                'owner_preview': owner_preview,
                'ownerPreview': owner_preview,
                'paid': bool(owner_preview or required_cents <= 0 or charged),
                'paidAt': now_iso if bool(owner_preview or required_cents <= 0 or charged) else '',
                'user_id': str(request.user.id),
                'userId': str(request.user.id),
                'user_phone': str(request.user.phone or ''),
                'userPhone': str(request.user.phone or ''),
                'owner_user_id': owner_user_id_resolved,
                'ownerUserId': owner_user_id_resolved,
                'owner_phone': str(getattr(owner_user, 'phone', '') or ''),
                'ownerPhone': str(getattr(owner_user, 'phone', '') or ''),
            }

            sessions = institution.get('service_sessions') if isinstance(institution.get('service_sessions'), list) else []
            if not sessions and isinstance(institution.get('serviceSessions'), list):
                sessions = institution.get('serviceSessions')
            sessions = [session_row, *[item for item in sessions if isinstance(item, dict)]]
            sessions = sessions[:300]
            institution['service_sessions'] = sessions
            institution['serviceSessions'] = sessions

        elif action == 'complete_service_session':
            session_id = str(request.data.get('sessionId') or request.data.get('session_id') or '').strip()
            if not session_id:
                return Response({'detail': 'sessionId is required.'}, status=status.HTTP_400_BAD_REQUEST)

            sessions = institution.get('service_sessions') if isinstance(institution.get('service_sessions'), list) else []
            if not sessions and isinstance(institution.get('serviceSessions'), list):
                sessions = institution.get('serviceSessions')
            sessions = [item for item in sessions if isinstance(item, dict)]

            row_index = next(
                (
                    idx
                    for idx, row in enumerate(sessions)
                    if str(row.get('id') or '') == session_id and str(row.get('user_id') or row.get('userId') or '') == str(request.user.id)
                ),
                -1,
            )
            if row_index < 0:
                return Response({'detail': 'Session not found.'}, status=status.HTTP_404_NOT_FOUND)

            session_row = dict(sessions[row_index])
            if str(session_row.get('status') or '').lower() == 'completed':
                institution['service_sessions'] = sessions
                institution['serviceSessions'] = sessions
            else:
                legacy_required_micro = int(session_row.get('required_micro') or session_row.get('requiredMicro') or 0)
                required_cents = _to_cents(
                    session_row.get('required_cents')
                    or session_row.get('requiredCents')
                    or round(legacy_required_micro / 10)
                )
                if required_cents <= 0:
                    required_cents = _to_cents(session_row.get('price_cents') or session_row.get('priceCents') or 0)

                payment_mode = str(session_row.get('payment_mode') or session_row.get('paymentMode') or 'wallet').strip().lower()
                owner_preview = bool(session_row.get('owner_preview') or session_row.get('ownerPreview') or False)
                already_paid = bool(session_row.get('paid') or False)

                owner_user_id_resolved = str(session_row.get('owner_user_id') or session_row.get('ownerUserId') or '').strip()
                owner_user = User.objects.filter(id=owner_user_id_resolved).first() if owner_user_id_resolved else None
                if owner_user is None:
                    owner_user, owner_user_id_resolved = _resolve_institution_owner_user(institution, owner_user_id_hint=owner_user_id)

                now_iso = timezone.now().isoformat()
                if required_cents > 0 and not owner_preview and payment_mode != 'owner_preview' and not already_paid:
                    buyer_wallet = get_wallet_account(request.user)
                    available_cents = int(getattr(buyer_wallet, 'balance_cents', 0) or 0)
                    if available_cents < required_cents:
                        return Response(
                            {
                                'detail': 'Insufficient KIS wallet balance.',
                                'required_cents': required_cents,
                                'available_cents': available_cents,
                                'required_usd': str(cents_to_usd(required_cents)),
                                'required_usd_compact': cents_to_usd_compact(required_cents),
                                'available_usd': str(cents_to_usd(available_cents)),
                                'available_usd_compact': cents_to_usd_compact(available_cents),
                            },
                            status=status.HTTP_402_PAYMENT_REQUIRED,
                        )

                    charge_meta = {
                        'institution_id': str(institution.get('id') or ''),
                        'service_id': str(session_row.get('service_id') or session_row.get('serviceId') or ''),
                        'service_name': str(session_row.get('service_name') or session_row.get('serviceName') or ''),
                        'session_id': session_id,
                        'payment_mode': 'wallet_usd',
                        'charged_at': 'complete',
                        'amount_usd': str(cents_to_usd(required_cents)),
                        'amount_usd_compact': cents_to_usd_compact(required_cents),
                    }
                    record_ledger(
                        user=request.user,
                        kind='purchase',
                        amount_cents=-required_cents,
                        reference=f'broadcast_health_session:{session_id}:complete',
                        meta=charge_meta,
                    )
                    if owner_user and str(getattr(owner_user, 'id', '')) and str(owner_user.id) != str(request.user.id):
                        record_ledger(
                            user=owner_user,
                            kind='transfer_in',
                            amount_cents=required_cents,
                            reference=f'broadcast_health_session:{session_id}:complete:from:{request.user.id}',
                            meta={
                                **charge_meta,
                                'source_user_id': str(request.user.id),
                            },
                        )

                session_row['status'] = 'completed'
                session_row['completed_at'] = now_iso
                session_row['completedAt'] = now_iso
                session_row['paid'] = True
                session_row['paidAt'] = now_iso
                session_row['required_cents'] = required_cents
                session_row['requiredCents'] = required_cents
                session_row['required_usd'] = str(cents_to_usd(required_cents))
                session_row['requiredUsd'] = str(cents_to_usd(required_cents))
                session_row['required_usd_compact'] = cents_to_usd_compact(required_cents)
                session_row['requiredUsdCompact'] = cents_to_usd_compact(required_cents)
                session_row['owner_user_id'] = owner_user_id_resolved
                session_row['ownerUserId'] = owner_user_id_resolved
                sessions[row_index] = session_row
                institution['service_sessions'] = sessions
                institution['serviceSessions'] = sessions

        elif action == 'transfer_ownership':
            return Response({'detail': 'Owner role is immutable and cannot be transferred.'}, status=status.HTTP_403_FORBIDDEN)

        elif action == 'execute_engine':
            engine = str(request.data.get('engine') or '').strip().lower()
            service_id = str(request.data.get('serviceId') or request.data.get('service_id') or '').strip()
            service_name = str(request.data.get('serviceName') or request.data.get('service_name') or '').strip()
            if not engine:
                return Response({'detail': 'engine is required.'}, status=status.HTTP_400_BAD_REQUEST)
            if not service_id and not service_name:
                return Response({'detail': 'serviceId or serviceName is required.'}, status=status.HTTP_400_BAD_REQUEST)

            supported = {
                'appointment',
                'video',
                'lab',
                'prescription',
                'payment',
                'surgery',
                'admission',
                'emergency',
                'wellness',
                'logistics',
            }
            if engine not in supported:
                return Response({'detail': 'Unsupported engine.'}, status=status.HTTP_400_BAD_REQUEST)

            now_iso = timezone.now().isoformat()
            execution = {
                'id': _ensure_entry_id(None, 'engine'),
                'engine': engine,
                'service_id': service_id,
                'serviceId': service_id,
                'service_name': service_name,
                'serviceName': service_name,
                'status': 'executed',
                'created_at': now_iso,
                'createdAt': now_iso,
                'user_id': str(request.user.id),
                'userId': str(request.user.id),
                'user_phone': str(request.user.phone or ''),
                'userPhone': str(request.user.phone or ''),
            }

            history = institution.get('engine_executions') if isinstance(institution.get('engine_executions'), list) else []
            if not history and isinstance(institution.get('engineExecutions'), list):
                history = institution.get('engineExecutions')
            history = [execution, *[item for item in history if isinstance(item, dict)]]
            history = history[:100]
            institution['engine_executions'] = history
            institution['engineExecutions'] = history

        else:
            return Response({'detail': 'Unsupported action.'}, status=status.HTTP_400_BAD_REQUEST)

        institutions[idx] = _sanitize_institution(institution) or institution
        payload['institutions'] = institutions
        payload = _ensure_health_profile_structure(payload)

        owner_user = health_profile.profile.user if getattr(health_profile, 'profile', None) else request.user
        owner_profiles = _load_user_profiles(owner_user)
        owner_profiles['health'] = payload
        _save_user_profiles(owner_user, owner_profiles)

        updated_institution = payload.get('institutions', [])[idx]
        return Response(self._build_response(updated_institution, request.user, phone_override=phone, owner_user_id=owner_user_id), status=status.HTTP_200_OK)
class ProfileManagementView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "broadcast_profile_manage"

    PROFILE_MAP = {
        'feed': 'broadcast_feed',
        'health': 'health',
        'market': 'market',
        'education': 'education',
    }

    def _merge_attachments(self, existing: list[dict], incoming: list[dict]) -> list[dict]:
        added = []
        for entry in incoming:
            if isinstance(entry, dict) and entry.get('url'):
                added.append(entry)
        return existing + added

    def _update_health(self, profile, updates):
        profile['attachments'] = self._merge_attachments(profile.get('attachments', []), updates.get('attachments', []))
        profile['notes'] = updates.get('notes') or profile.get('notes')
        if 'institutions' in updates:
            incoming_institutions = _ensure_institution_data(updates.get('institutions') or [])
            existing_institutions = _ensure_institution_data(profile.get('institutions') or [])
            existing_by_id = {
                str(item.get('id') or '').strip(): item
                for item in existing_institutions
                if isinstance(item, dict) and str(item.get('id') or '').strip()
            }
            locked_institutions: list[dict] = []
            for institution in incoming_institutions:
                institution_id = str(institution.get('id') or '').strip()
                existing = existing_by_id.get(institution_id)
                locked = _enforce_immutable_owner(existing, institution)
                locked_institutions.append(_sanitize_institution(locked) or locked)
            profile['institutions'] = locked_institutions
        profile = _ensure_health_profile_structure(profile)
        appointments = updates.get('appointments') or []
        if appointments:
            existing = profile.get('appointments', [])
            profile['appointments'] = existing + [appt for appt in appointments if appt.get('title')]
        profile['updated_at'] = timezone.now().isoformat()
        return profile

    def _update_market(self, profile, updates):
        profile['attachments'] = self._merge_attachments(profile.get('attachments', []), updates.get('attachments', []))
        inventory = updates.get('inventory_updates') or []
        if inventory:
            existing = profile.get('inventory', [])
            profile['inventory'] = existing + [item for item in inventory if item.get('name')]
        if 'shops' in updates:
            profile['shops'] = _ensure_shop_data(updates.get('shops') or [])
        profile = _apply_landing_builder_updates(profile, updates)
        profile = _ensure_market_profile_structure(profile)
        profile['updated_at'] = timezone.now().isoformat()
        return profile

    def _update_education(self, profile, updates):
        profile['attachments'] = self._merge_attachments(profile.get('attachments', []), updates.get('attachments', []))
        modules = updates.get('modules') or []
        if modules:
            existing = profile.get('modules', [])
            profile['modules'] = existing + [
                {
                    'title': mod.get('title'),
                    'summary': mod.get('summary'),
                    'resource_url': mod.get('resource_url'),
                }
                for mod in modules
                if mod.get('title')
            ]
        if 'courses' in updates:
            profile['courses'] = _ensure_course_data(updates.get('courses') or [])
        profile = _apply_landing_builder_updates(profile, updates)
        profile = _ensure_education_profile_structure(profile)
        profile['updated_at'] = timezone.now().isoformat()
        return profile

    def post(self, request):
        profile_type = (request.data.get('profile_type') or '').strip().lower()
        updates = request.data.get('updates') or {}
        if profile_type not in {'health_profile', 'market_profile', 'education_profile'}:
            raise ValidationError({'profile_type': 'Unsupported profile type for management.'})

        key_map = {
            'health_profile': 'health',
            'market_profile': 'market',
            'education_profile': 'education',
        }
        profile_key = key_map[profile_type]

        profiles = _load_user_profiles(request.user)
        profile_data = profiles.get(profile_key)
        if not profile_data:
            raise ValidationError({'detail': 'Create the profile before managing it.'})

        if profile_type == 'health_profile':
            updated = self._update_health(profile_data, updates)
        elif profile_type == 'market_profile':
            updated = self._update_market(profile_data, updates)
        else:
            updated = self._update_education(profile_data, updates)

        profiles[profile_key] = updated
        _save_user_profiles(request.user, profiles)
        return Response({'profile': updated}, status=status.HTTP_200_OK)
