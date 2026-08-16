# core/views.py
from django.shortcuts import get_object_or_404
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

import uuid
from decimal import Decimal
from typing import Any, Dict

from rest_framework import viewsets, status, mixins
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission, IsAuthenticated, IsAdminUser, AllowAny
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter
from drf_spectacular.utils import extend_schema, OpenApiParameter

from drf_spectacular.utils import extend_schema, OpenApiParameter

from . import models, serializers
from .interoperability import export_patient_fhir_bundle, import_patient_fhir_bundle

from apps.notifications import services as notification_services
from .ai_assistance_safety import ai_assistance_safety_policy
from .monetization_safety import monetization_without_legal_risk_summary
from .performance_offline import performance_offline_policy
from .platform_dashboards import unified_platform_dashboard_summary
from .safety_command_center import staff_safety_command_center_summary
from .security_launch_gate import security_privacy_child_safety_launch_gate
from .launch_ops import staff_launch_operations_summary
from .social_recommendations import privacy_safe_social_recommendation_foundation

# Convenience user model
from django.apps import apps
from django.conf import settings
UserModel = apps.get_model(settings.AUTH_USER_MODEL)


def _search_text(value) -> str:
    return str(value or "").strip()


def _display_user(user) -> str:
    return (
        _search_text(getattr(user, "display_name", ""))
        or _search_text(getattr(user, "full_name", ""))
        or _search_text(getattr(user, "username", ""))
        or _search_text(getattr(user, "phone", ""))
        or "KIS user"
    )


def _unified_result(*, kind: str, title: str, subtitle: str = "", target_type: str, target_id, route: str, score: int = 1, metadata=None):
    return {
        "kind": kind,
        "title": title,
        "subtitle": subtitle,
        "target_type": target_type,
        "target_id": str(target_id),
        "route": route,
        "score": score,
        "metadata": metadata or {},
    }


def _blocked_user_ids_for_search(user) -> set[str]:
    if not getattr(user, "is_authenticated", False):
        return set()
    try:
        from apps.moderation.models import UserBlock

        made = UserBlock.objects.filter(blocker=user).values_list("blocked_id", flat=True)
        received = UserBlock.objects.filter(blocked=user).values_list("blocker_id", flat=True)
        return {str(value) for value in made} | {str(value) for value in received}
    except Exception:
        return set()


def _resolve_patient_for_user(user):
    if not getattr(user, "is_authenticated", False):
        return None

    user_id = str(getattr(user, "id", "") or "").strip()
    email = str(getattr(user, "email", "") or "").strip()
    phone_candidates = [
        str(getattr(user, "phone", "") or "").strip(),
        str(getattr(user, "phone_number", "") or "").strip(),
    ]

    patient = models.PatientMasterRecord.objects.filter(primary_contact__user_id=user_id).order_by("-updated_at").first()
    if patient:
        return patient

    if email:
        patient = models.PatientMasterRecord.objects.filter(primary_contact__email__iexact=email).order_by("-updated_at").first()
        if patient:
            return patient

    for phone in phone_candidates:
        if not phone:
            continue
        patient = (
            models.PatientMasterRecord.objects.filter(primary_contact__phone=phone).order_by("-updated_at").first()
            or models.PatientMasterRecord.objects.filter(primary_contact__phone_number=phone).order_by("-updated_at").first()
        )
        if patient:
            return patient

    return None


def _user_org_ids(user):
    if getattr(user, "is_superuser", False):
        return None
    if not getattr(user, "is_authenticated", False):
        return set()
    return set(
        user.medical_staff_profiles.filter(profile__organization_id__isnull=False).values_list(
            "profile__organization_id", flat=True
        )
    )


def _is_patient_owner(user, patient):
    if not getattr(user, "is_authenticated", False) or not patient:
        return False
    user_id = str(getattr(user, "id", "") or "").strip()
    primary_contact = patient.primary_contact if isinstance(patient.primary_contact, dict) else {}
    return bool(user_id and str(primary_contact.get("user_id") or "").strip() == user_id)


def _active_health_access_grant(user, patient):
    if not getattr(user, "is_authenticated", False) or not patient:
        return None
    candidate = (
        patient.access_grants.filter(granted_to=user, status=models.HealthDataAccessGrant.STATUS_ACTIVE)
        .order_by("-created_at")
        .first()
    )
    if candidate and candidate.is_active():
        return candidate
    return None


def _health_scope_allows(grant, required_scope: str, emergency: bool = False) -> bool:
    if not grant:
        return False
    if emergency and grant.allow_emergency_override:
        return True
    if grant.scope == models.HealthDataAccessGrant.SCOPE_FULL:
        return True
    if grant.scope == required_scope:
        return True
    if required_scope == models.HealthDataAccessGrant.SCOPE_SUMMARY and grant.scope in {
        models.HealthDataAccessGrant.SCOPE_RECORDS,
        models.HealthDataAccessGrant.SCOPE_EMERGENCY,
    }:
        return True
    if required_scope == models.HealthDataAccessGrant.SCOPE_EMERGENCY and grant.scope == models.HealthDataAccessGrant.SCOPE_RECORDS:
        return True
    return False


def _can_access_patient_record(user, patient, required_scope: str = models.HealthDataAccessGrant.SCOPE_SUMMARY, emergency: bool = False):
    if getattr(user, "is_superuser", False):
        return True, "superuser", None
    if _is_patient_owner(user, patient):
        return True, "owner", None
    if patient.organization_id and user and getattr(user, "is_authenticated", False):
        org_ids = _user_org_ids(user)
        if org_ids is None or patient.organization_id in org_ids:
            return True, "provider", None
    grant = _active_health_access_grant(user, patient)
    if grant and _health_scope_allows(grant, required_scope, emergency=emergency):
        return True, "delegate", grant
    return False, "", None


def _accessible_patient_ids(user):
    """Patient ids the given user may access, or None to mean "unrestricted"
    (superuser). Mirrors _can_access_patient_record's three access paths
    (owner / org staff / active delegate grant) at queryset scale, so it can
    back get_queryset() on every PHI-bearing ViewSet. This is the sole source
    of truth for PHI scoping - a client-supplied institution/patient/org query
    param may only narrow an already-scoped queryset via filterset_fields, it
    must never be able to widen it."""
    if getattr(user, "is_superuser", False):
        return None
    if not getattr(user, "is_authenticated", False):
        return models.PatientMasterRecord.objects.none().values_list("id", flat=True)
    user_id = str(getattr(user, "id", "") or "")
    org_ids = _user_org_ids(user)
    granted_patient_ids = (
        models.HealthDataAccessGrant.objects.filter(
            granted_to=user,
            status=models.HealthDataAccessGrant.STATUS_ACTIVE,
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
        .values_list("patient_id", flat=True)
    )
    accessible = Q(primary_contact__user_id=user_id) | Q(id__in=granted_patient_ids)
    if org_ids:
        accessible |= Q(organization_id__in=org_ids)
    return models.PatientMasterRecord.objects.filter(accessible).values_list("id", flat=True)


class PatientScopedQuerySetMixin:
    """Scopes a ViewSet's queryset to patients the requesting user may access.

    Set `patient_lookup` to the field path from this model to
    PatientMasterRecord (e.g. "patient" or "session__patient"). retrieve/
    update/destroy of an out-of-scope object correctly 404 via DRF's
    get_object(), since it filters through get_queryset() first.
    """

    patient_lookup = "patient"

    def get_queryset(self):
        queryset = super().get_queryset()
        allowed_patient_ids = _accessible_patient_ids(self.request.user)
        if allowed_patient_ids is None:
            return queryset
        return queryset.filter(**{f"{self.patient_lookup}__in": allowed_patient_ids})

    def _assert_write_access(self, patient):
        """Guard against IDOR on create/update: a client-supplied `patient`
        (or `session`) foreign key must resolve to a patient this user can
        actually access, regardless of what the request body says."""
        if patient is None:
            return
        allowed, _reason, _grant = _can_access_patient_record(
            self.request.user,
            patient,
            required_scope=models.HealthDataAccessGrant.SCOPE_RECORDS,
        )
        if not allowed:
            raise PermissionDenied("You do not have access to this patient's records.")


class OrgScopedQuerySetMixin:
    """Scopes a ViewSet's queryset to HealthcareOrganizations the requesting
    user staffs (via _user_org_ids). Set org_lookup to the field path from
    this model to HealthcareOrganization (e.g. "organization" or
    "staff_profile__profile__organization")."""

    org_lookup = "organization"

    def get_queryset(self):
        queryset = super().get_queryset()
        org_ids = _user_org_ids(self.request.user)
        if org_ids is None:
            return queryset
        return queryset.filter(**{f"{self.org_lookup}__in": org_ids})

    def _assert_org_write_access(self, organization_id):
        if organization_id is None:
            return
        org_ids = _user_org_ids(self.request.user)
        if org_ids is not None and organization_id not in org_ids:
            raise PermissionDenied("You cannot act on records outside your organization.")


def _log_patient_access(actor, patient, action: str, reason: str, grant=None, metadata=None):
    payload = {
        "patient_id": str(patient.id),
        "reason": reason,
    }
    if grant is not None:
        payload["grant_id"] = str(grant.id)
        payload["grant_scope"] = grant.scope
        payload["grant_role"] = grant.role
    if metadata:
        payload.update(metadata)
    models.ComplianceAuditLog.log(
        actor=actor,
        action=action,
        target_type="PatientMasterRecord",
        target_id=str(patient.id),
        severity=models.ComplianceAuditLog.SEVERITY_MEDIUM,
        metadata=payload,
    )


def _log_staff_action(staff, actor, action, metadata=None, note=""):
    models.StaffAuditLog.objects.create(
        staff=staff,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        metadata=metadata or {},
        note=note,
    )


def _record_clinical_event(
    patient,
    event_type: str,
    description: str,
    user=None,
    task=None,
    escalation=None,
    triage=None,
    referral=None,
    metadata=None,
):
    models.ClinicalEventLog.objects.create(
        patient=patient,
        event_type=event_type,
        description=description,
        triggered_by=user if getattr(user, "is_authenticated", False) else None,
        task=task,
        escalation=escalation,
        triage=triage,
        referral=referral,
        metadata=metadata or {},
    )


# ---------------------------
# Custom DRF permission wrappers
# ---------------------------
class IsSuperuserOrAdmin(IsAdminUser):
    """
    Slight wrapper: admin users or Django superusers.
    """
    def has_permission(self, request, view):
        if getattr(request.user, "is_superuser", False):
            return True
        return super().has_permission(request, view)


class CanManageRolesPermission(IsAuthenticated):
    """
    Placeholder permission: restrict role/permission/ace management to staff or superusers.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if getattr(request.user, "is_superuser", False):
            return True
        return bool(getattr(request.user, "is_staff", False))


class CanAccessComplianceData(BasePermission):
    """Authentication alone is not sufficient here - each compliance
    ViewSet additionally scopes its queryset to the caller's own org(s) via
    _user_org_ids(), and write actions re-check org membership on the
    specific object before acting on it."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)


class UnifiedSearchView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "search"

    def _limit(self, request):
        try:
            return min(max(int(request.query_params.get("limit") or 8), 1), 25)
        except (TypeError, ValueError):
            return 8

    def _groups(self, request):
        raw = _search_text(request.query_params.get("groups") or request.query_params.get("group") or "all").lower()
        if not raw or raw == "all":
            return {
                "contacts",
                "conversations",
                "channels",
                "channel_content",
                "bible",
                "health",
                "market",
                "education",
                "partners",
                "notifications",
                "verification",
            }
        aliases = {
            "people": "contacts",
            "chat": "conversations",
            "chats": "conversations",
            "feeds": "channel_content",
            "commerce": "market",
            "shops": "market",
            "courses": "education",
            "partner": "partners",
        }
        return {aliases.get(item.strip(), item.strip()) for item in raw.split(",") if item.strip()}

    def _search_contacts(self, request, query: str, limit: int):
        blocked_user_ids = _blocked_user_ids_for_search(request.user)
        qs = UserModel.objects.filter(is_active=True).filter(
            Q(display_name__icontains=query)
            | Q(username__icontains=query)
            | Q(phone__icontains=query)
            | Q(email__icontains=query)
        ).exclude(id=request.user.id)
        if blocked_user_ids:
            qs = qs.exclude(id__in=blocked_user_ids)
        qs = qs.order_by("display_name", "username", "phone")[:limit]
        return [
            _unified_result(
                kind="contact",
                title=_display_user(user),
                subtitle=_search_text(getattr(user, "phone", "")),
                target_type="user",
                target_id=user.id,
                route="Messages/AddContactOrDirectChat",
                score=100,
                metadata={"username": _search_text(getattr(user, "username", ""))},
            )
            for user in qs
        ]

    def _search_conversations(self, request, query: str, limit: int):
        try:
            from apps.chat.models import Conversation
        except Exception:
            return []
        qs = (
            Conversation.objects.filter(
                memberships__user=request.user,
                memberships__left_at__isnull=True,
                memberships__is_hidden=False,
            )
            .filter(
                Q(title__icontains=query)
                | Q(description__icontains=query)
                | Q(last_message_preview__icontains=query)
                | Q(memberships__user__display_name__icontains=query)
                | Q(memberships__user__phone__icontains=query)
                | Q(memberships__user__username__icontains=query)
            )
            .distinct()
            .order_by("-last_message_at", "-updated_at")[:limit]
        )
        rows = []
        for conv in qs:
            rows.append(
                _unified_result(
                    kind="conversation",
                    title=_search_text(conv.title) or "Conversation",
                    subtitle=_search_text(conv.last_message_preview),
                    target_type="conversation",
                    target_id=conv.id,
                    route="Messages/ChatRoom",
                    score=95,
                    metadata={"conversation_type": conv.type},
                )
            )
        return rows

    def _search_channels(self, request, query: str, limit: int):
        try:
            from apps.broadcasts.models import BroadcastChannel
        except Exception:
            return []
        blocked_user_ids = _blocked_user_ids_for_search(request.user)
        qs = (
            BroadcastChannel.objects.filter(is_deleted=False)
            .filter(Q(is_public=True) | Q(owner_user=request.user) | Q(roles__user=request.user))
            .filter(Q(handle__icontains=query) | Q(display_name__icontains=query) | Q(description__icontains=query))
            .distinct()
        )
        if blocked_user_ids:
            qs = qs.exclude(owner_user_id__in=blocked_user_ids)
        qs = qs.order_by("-subscriber_count", "-updated_at")[:limit]
        return [
            _unified_result(
                kind="channel",
                title=channel.display_name,
                subtitle=f"@{channel.handle}",
                target_type="broadcast_channel",
                target_id=channel.id,
                route="Broadcast/ChannelHome",
                score=90,
                metadata={"handle": channel.handle, "avatar_url": channel.avatar_url},
            )
            for channel in qs
        ]

    def _search_channel_content(self, request, query: str, limit: int):
        try:
            from apps.broadcasts.models import ChannelContent
        except Exception:
            return []
        blocked_user_ids = _blocked_user_ids_for_search(request.user)
        qs = (
            ChannelContent.objects.select_related("channel")
            .filter(is_deleted=False)
            .filter(
                Q(status="published", visibility__in=["public", "unlisted"])
                | Q(channel__owner_user=request.user)
                | Q(channel__roles__user=request.user)
            )
            .filter(Q(title__icontains=query) | Q(description__icontains=query) | Q(text_plain__icontains=query))
            .distinct()
        )
        if blocked_user_ids:
            qs = qs.exclude(channel__owner_user_id__in=blocked_user_ids).exclude(created_by_id__in=blocked_user_ids)
        qs = qs.order_by("-published_at", "-updated_at")[:limit]
        return [
            _unified_result(
                kind="channel_content",
                title=_search_text(content.title) or _search_text(content.text_plain)[:80] or "Channel content",
                subtitle=content.channel.display_name,
                target_type="channel_content",
                target_id=content.id,
                route="Broadcast/ChannelContentDetail",
                score=85,
                metadata={
                    "channel_id": str(content.channel_id),
                    "content_type": content.content_type,
                    "thumbnail_url": content.thumbnail_url,
                },
            )
            for content in qs
        ]

    def _search_bible(self, request, query: str, limit: int):
        try:
            from apps.bible.models import BibleVerse
            from apps.bible.views import public_translation_queryset
        except Exception:
            return []
        translation_ids = public_translation_queryset().values_list("id", flat=True)
        qs = (
            BibleVerse.objects.select_related("chapter", "chapter__book", "translation")
            .filter(translation_id__in=translation_ids, text__icontains=query)
            .order_by("chapter__book__order", "chapter__number", "number")[:limit]
        )
        rows = []
        for verse in qs:
            chapter = verse.chapter
            book = chapter.book
            reference = f"{book.name} {chapter.number}:{verse.number}"
            rows.append(
                _unified_result(
                    kind="bible_verse",
                    title=reference,
                    subtitle=verse.text[:180],
                    target_type="bible_verse",
                    target_id=verse.id,
                    route="Bible/Reader",
                    score=80,
                    metadata={
                        "translation": str(verse.translation_id),
                        "book": getattr(book, "code", "") or book.name,
                        "chapter": chapter.number,
                        "verse": verse.number,
                    },
                )
            )
        return rows

    def _search_health(self, request, query: str, limit: int):
        try:
            from apps.broadcasts.models import BroadcastHealthInstitution
        except Exception:
            return []
        blocked_user_ids = _blocked_user_ids_for_search(request.user)
        qs = BroadcastHealthInstitution.objects.filter(
            Q(name__icontains=query)
            | Q(owner_name__icontains=query)
            | Q(owner_phone__icontains=query)
            | Q(owner_email__icontains=query)
            | Q(institution_type__icontains=query)
        )
        if blocked_user_ids:
            qs = qs.exclude(owner_user_id__in=blocked_user_ids)
        qs = qs.order_by("-updated_at", "-created_at")[:limit]
        return [
            _unified_result(
                kind="health_institution",
                title=row.name,
                subtitle=_search_text(getattr(row, "institution_type", "")),
                target_type="health_institution",
                target_id=row.id,
                route="Health/Institution",
                score=70,
                metadata={},
            )
            for row in qs
        ]

    def _search_market(self, request, query: str, limit: int):
        try:
            from apps.commerce.models import Product, Shop
        except Exception:
            return []
        blocked_user_ids = _blocked_user_ids_for_search(request.user)
        rows = []
        shop_qs = Shop.objects.filter(is_deleted=False).filter(
            Q(name__icontains=query) | Q(description__icontains=query) | Q(slug__icontains=query)
        )
        if blocked_user_ids:
            shop_qs = shop_qs.exclude(owner_id__in=blocked_user_ids)
        for shop in shop_qs.order_by("-is_verified", "-rating_avg", "-updated_at")[:limit]:
            rows.append(
                _unified_result(
                    kind="market_shop",
                    title=shop.name,
                    subtitle="Verified seller" if getattr(shop, "is_verified", False) else "Marketplace shop",
                    target_type="shop",
                    target_id=shop.id,
                    route="Market/Shop",
                    score=74 + (10 if getattr(shop, "is_verified", False) else 0),
                    metadata={"slug": shop.slug, "verified": bool(getattr(shop, "is_verified", False))},
                )
            )
        remaining = max(limit - len(rows), 0)
        if remaining:
            product_qs = Product.objects.select_related("shop").filter(is_deleted=False, is_active=True).filter(
                Q(name__icontains=query) | Q(description__icontains=query) | Q(sku__icontains=query) | Q(shop__name__icontains=query)
            )
            if blocked_user_ids:
                product_qs = product_qs.exclude(shop__owner_id__in=blocked_user_ids)
            for product in product_qs.order_by("-is_featured", "-updated_at")[:remaining]:
                rows.append(
                    _unified_result(
                        kind="market_product",
                        title=product.name,
                        subtitle=_search_text(getattr(product.shop, "name", ""),) or "Marketplace product",
                        target_type="product",
                        target_id=product.id,
                        route="Market/Product",
                        score=72 + (10 if getattr(product, "is_featured", False) else 0),
                        metadata={"shop_id": str(product.shop_id), "currency": "USD"},
                    )
                )
        return rows

    def _search_education(self, request, query: str, limit: int):
        try:
            from apps.broadcasts.models import EducationAcademicRecordStatus, EducationInstitution, EducationInstitutionCourse
        except Exception:
            return []
        blocked_user_ids = _blocked_user_ids_for_search(request.user)
        rows = []
        institution_qs = EducationInstitution.objects.filter(is_active=True).filter(
            Q(name__icontains=query) | Q(description__icontains=query) | Q(institution_type__icontains=query)
        )
        if blocked_user_ids:
            institution_qs = institution_qs.exclude(owner_id__in=blocked_user_ids)
        for institution in institution_qs.order_by("-updated_at")[:limit]:
            rows.append(
                _unified_result(
                    kind="education_institution",
                    title=institution.name,
                    subtitle=_search_text(institution.institution_type) or "Education institution",
                    target_type="education_institution",
                    target_id=institution.id,
                    route="Education/Institution",
                    score=73,
                    metadata={"institution_type": institution.institution_type},
                )
            )
        remaining = max(limit - len(rows), 0)
        if remaining:
            course_qs = EducationInstitutionCourse.objects.select_related("institution").filter(
                institution__is_active=True,
                status=EducationAcademicRecordStatus.PUBLISHED,
            ).filter(
                Q(title__icontains=query) | Q(summary__icontains=query) | Q(description__icontains=query) | Q(code__icontains=query) | Q(institution__name__icontains=query)
            )
            if blocked_user_ids:
                course_qs = course_qs.exclude(institution__owner_id__in=blocked_user_ids)
            for course in course_qs.order_by("-updated_at", "title")[:remaining]:
                rows.append(
                    _unified_result(
                        kind="education_course",
                        title=course.title,
                        subtitle=_search_text(getattr(course.institution, "name", "")) or "Education course",
                        target_type="education_course",
                        target_id=course.id,
                        route="Education/Course",
                        score=71,
                        metadata={"institution_id": str(course.institution_id), "status": course.status},
                    )
                )
        return rows

    def _search_partners(self, request, query: str, limit: int):
        try:
            from apps.partners.models import Partner, PartnerJoinConfig
        except Exception:
            return []
        blocked_user_ids = _blocked_user_ids_for_search(request.user)
        public_partner_ids = PartnerJoinConfig.objects.filter(allow_public_listing=True).values_list("partner_id", flat=True)
        qs = Partner.objects.filter(is_active=True, id__in=public_partner_ids).filter(
            Q(name__icontains=query) | Q(slug__icontains=query) | Q(description__icontains=query)
        )
        if blocked_user_ids:
            qs = qs.exclude(owner_id__in=blocked_user_ids)
        return [
            _unified_result(
                kind="partner",
                title=row.name,
                subtitle="Partner workspace",
                target_type="partner",
                target_id=row.id,
                route="Partners/Workspace",
                score=69,
                metadata={"slug": row.slug},
            )
            for row in qs.order_by("name")[:limit]
        ]

    def _search_notifications(self, request, query: str, limit: int):
        try:
            from apps.notifications.models import Notification
        except Exception:
            return []
        qs = (
            Notification.objects.filter(user_id=request.user.id)
            .filter(Q(title__icontains=query) | Q(body__icontains=query))
            .order_by("-created_at")[:limit]
        )
        return [
            _unified_result(
                kind="notification",
                title=row.title,
                subtitle=row.body[:180],
                target_type=row.target_type or "notification",
                target_id=row.target_id or row.id,
                route="Notifications/Detail",
                score=60,
                metadata={"notification_id": str(row.id), "read": bool(getattr(row, "is_read", False))},
            )
            for row in qs
        ]

    def _search_verification(self, request, query: str, limit: int):
        try:
            from apps.verification.models import VerificationSubject
        except Exception:
            return []
        qs = VerificationSubject.objects.filter(owner=request.user).filter(
            Q(display_name__icontains=query) | Q(subject_type__icontains=query) | Q(current_status__icontains=query)
        ).order_by("-updated_at")[:limit]
        return [
            _unified_result(
                kind="verification",
                title=row.display_name,
                subtitle=f"{row.subject_type} · {row.current_status}",
                target_type="verification_subject",
                target_id=row.id,
                route="Profile/VerificationCenter",
                score=50,
                metadata={"subject_type": row.subject_type, "status": row.current_status},
            )
            for row in qs
        ]

    def get(self, request):
        query = _search_text(request.query_params.get("q") or request.query_params.get("query"))
        if len(query) < 2:
            return Response({"query": query, "results": [], "groups": {}, "count": 0})
        limit = self._limit(request)
        groups = self._groups(request)
        providers = {
            "contacts": self._search_contacts,
            "conversations": self._search_conversations,
            "channels": self._search_channels,
            "channel_content": self._search_channel_content,
            "bible": self._search_bible,
            "health": self._search_health,
            "market": self._search_market,
            "education": self._search_education,
            "partners": self._search_partners,
            "notifications": self._search_notifications,
            "verification": self._search_verification,
        }
        grouped = {}
        results = []
        for key, provider in providers.items():
            if key not in groups:
                continue
            rows = provider(request, query, limit)
            grouped[key] = rows
            results.extend(rows)
        results.sort(key=lambda row: (int(row.get("score") or 0), row.get("title") or ""), reverse=True)
        return Response(
            {
                "query": query,
                "results": results[: limit * max(len(grouped), 1)],
                "groups": grouped,
                "count": len(results),
            },
            status=status.HTTP_200_OK,
        )


# ---------------------------
# Simple ModelViewSets for Permission & Role
# ---------------------------
@extend_schema(tags=["Permissions"])
class PermissionViewSet(viewsets.ModelViewSet):
    queryset = models.Permission.objects.all()
    serializer_class = serializers.PermissionSerializer
    permission_classes = [IsSuperuserOrAdmin]


@extend_schema(tags=["Roles"])
class RoleViewSet(viewsets.ModelViewSet):
    queryset = models.Role.objects.prefetch_related("permissions").all()
    serializer_class = serializers.RoleShortSerializer
    permission_classes = [IsSuperuserOrAdmin]

    @extend_schema(
        request=serializers.PermissionSerializer,
        responses=serializers.RoleShortSerializer,
        description="Add a permission to a role",
    )
    @action(detail=True, methods=["post"], permission_classes=[IsSuperuserOrAdmin])
    def add_permission(self, request, pk=None):
        role = self.get_object()
        codename = request.data.get("codename")
        if not codename:
            return Response({"detail": "codename required"}, status=status.HTTP_400_BAD_REQUEST)
        perm, _ = models.Permission.objects.get_or_create(codename=codename)
        role.permissions.add(perm)
        role.save()
        return Response(self.get_serializer(role).data)


# ---------------------------
# RoleAssignment & ACE management
# ---------------------------
@extend_schema(tags=["RoleAssignments"])
class RoleAssignmentViewSet(viewsets.ModelViewSet):
    queryset = models.RoleAssignment.objects.select_related("role").all()
    serializer_class = serializers.RoleAssignmentSerializer
    permission_classes = [CanManageRolesPermission]

    @extend_schema(description="Assign a role to a principal")
    def perform_create(self, serializer):
        serializer.save()


@extend_schema(tags=["AccessControlEntries"])
class AccessControlEntryViewSet(viewsets.ModelViewSet):
    queryset = models.AccessControlEntry.objects.all()
    serializer_class = serializers.AccessControlEntrySerializer
    permission_classes = [CanManageRolesPermission]

    @extend_schema(description="Create an Access Control Entry (ACE)")
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)


@extend_schema(tags=["Healthcare"])
class HealthcareOrganizationViewSet(viewsets.ModelViewSet):
    queryset = models.HealthcareOrganization.objects.all()
    serializer_class = serializers.HealthcareOrganizationSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["org_type", "status", "tenant_id", "onboarding_status"]
    search_fields = ["name", "slug", "region"]

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        org = self.get_object()
        org.status = models.HealthcareOrganization.STATUS_SUSPENDED
        org.is_active = False
        org.save(update_fields=["status", "is_active"])
        return Response(self.get_serializer(org).data)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        org = self.get_object()
        org.status = models.HealthcareOrganization.STATUS_ACTIVE
        org.is_active = True
        org.save(update_fields=["status", "is_active"])
        return Response(self.get_serializer(org).data)


@extend_schema(tags=["Healthcare"])
class LocationViewSet(viewsets.ModelViewSet):
    queryset = models.Location.objects.select_related("organization", "profile").all()
    serializer_class = serializers.LocationSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["organization", "profile", "is_primary"]
    search_fields = ["label"]


@extend_schema(tags=["Healthcare"])
class WardViewSet(viewsets.ModelViewSet):
    queryset = models.Ward.objects.select_related("location").all()
    serializer_class = serializers.WardSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["location", "is_isolation"]
    search_fields = ["name"]


@extend_schema(tags=["Healthcare"])
class ServiceViewSet(viewsets.ModelViewSet):
    queryset = models.Service.objects.select_related("profile", "department").all()
    serializer_class = serializers.ServiceSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["profile", "department", "category"]
    search_fields = ["name"]


@extend_schema(tags=["Healthcare"])
class EquipmentViewSet(viewsets.ModelViewSet):
    queryset = models.Equipment.objects.select_related("profile", "ward").all()
    serializer_class = serializers.EquipmentSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["profile", "ward", "status"]
    search_fields = ["name"]


@extend_schema(tags=["Healthcare"])
class MedicalProfileViewSet(viewsets.ModelViewSet):
    queryset = models.MedicalProfile.objects.select_related("organization").all()
    serializer_class = serializers.MedicalProfileSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["profile_type", "status", "organization__id", "onboarding_status"]
    search_fields = ["name", "slug"]

    def get_serializer_class(self):
        if self.action in ("retrieve",):
            return serializers.MedicalProfileDetailSerializer
        return serializers.MedicalProfileSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        profile = self.get_object()
        profile.status = models.MedicalProfile.STATUS_PAUSED
        profile.save(update_fields=["status"])
        return Response(self.get_serializer(profile).data)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        profile = self.get_object()
        profile.status = models.MedicalProfile.STATUS_ACTIVE
        profile.save(update_fields=["status"])
        return Response(self.get_serializer(profile).data)

    @action(detail=True, methods=["post"])
    def set_active(self, request, pk=None):
        profile = self.get_object()
        prefs = dict(getattr(request.user, "preferences", {}) or {})
        prefs["active_medical_profile"] = str(profile.id)
        prefs["active_medical_organization"] = str(profile.organization_id) if profile.organization_id else None
        request.user.preferences = prefs
        request.user.save(update_fields=["preferences"])
        return Response(serializers.MedicalProfileDetailSerializer(profile).data)


class MedicalContextView(APIView):
    permission_classes = [IsAuthenticated]

    def _serialize_organization(self, org: models.HealthcareOrganization) -> Dict[str, Any]:
        profiles = [serializers.MedicalProfileSerializer(profile).data for profile in org.profiles.all()]
        locations = [serializers.LocationSerializer(location).data for location in org.locations.all()]
        return {
            "id": str(org.id),
            "name": org.name,
            "org_type": org.org_type,
            "status": org.status,
            "region": org.region,
            "metadata": org.metadata,
            "profiles": profiles,
            "locations": locations,
        }

    def get(self, request):
        prefs = dict(getattr(request.user, "preferences", {}) or {})
        active_profile_id = prefs.get("active_medical_profile")
        active_profile = None
        if active_profile_id:
            try:
                active_profile = models.MedicalProfile.objects.get(id=active_profile_id)
            except models.MedicalProfile.DoesNotExist:
                active_profile = None

        organizations = (
            models.HealthcareOrganization.objects.filter(is_active=True)
            .prefetch_related("profiles", "locations")
            .all()
        )

        payload = {
            "organizations": [self._serialize_organization(org) for org in organizations],
            "active_profile_id": str(active_profile.id) if active_profile else None,
            "active_organization_id": str(active_profile.organization_id) if active_profile and active_profile.organization_id else None,
            "active_profile": serializers.MedicalProfileSerializer(active_profile).data if active_profile else None,
        }
        return Response(payload)


@extend_schema(tags=["Healthcare"])
class StaffProfileViewSet(viewsets.ModelViewSet):
    queryset = models.StaffProfile.objects.select_related("profile", "user").all()
    serializer_class = serializers.StaffProfileSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["profile", "role", "is_on_call"]
    search_fields = ["role"]

    def _allowed_org_ids(self):
        return _user_org_ids(self.request.user)

    def get_queryset(self):
        queryset = super().get_queryset()
        allowed = self._allowed_org_ids()
        if allowed is None:
            return queryset
        return queryset.filter(profile__organization_id__in=allowed)

    def perform_create(self, serializer):
        profile = serializer.validated_data.get("profile")
        allowed = self._allowed_org_ids()
        if allowed is not None and profile and profile.organization_id not in allowed:
            raise PermissionDenied("You cannot manage staff outside your organization.")
        staff = serializer.save()
        _log_staff_action(staff, self.request.user, "create", metadata={"profile": str(staff.profile_id)})

    @action(detail=True, methods=["post"])
    def assign_role(self, request, pk=None):
        staff = self.get_object()
        allowed = self._allowed_org_ids()
        if allowed is not None and staff.profile.organization_id not in allowed:
            raise PermissionDenied("Access denied.")
        updates = {}
        for field in ("role", "scope", "permissions", "licenses"):
            if field in request.data:
                updates[field] = request.data.get(field)
        if not updates:
            raise ValidationError({"detail": "Provide at least one field to update."})
        for key, value in updates.items():
            setattr(staff, key, value)
        staff.save(update_fields=list(updates.keys()) + ["updated_at"])
        _log_staff_action(staff, request.user, "assign_role", metadata=updates)
        return Response(self.get_serializer(staff).data)


@extend_schema(tags=["Healthcare"])
class StaffAuditViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.StaffAuditLog.objects.select_related("staff", "actor").all()
    serializer_class = serializers.StaffAuditSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["staff", "actor", "action"]

    @action(detail=True, methods=["post"])
    def assign_shift(self, request, pk=None):
        staff = self.get_object()
        allowed = self._allowed_org_ids()
        if allowed is not None and staff.profile.organization_id not in allowed:
            raise PermissionDenied("Access denied.")
        shifts = request.data.get("shifts")
        if not isinstance(shifts, list):
            raise ValidationError({"shifts": "Provide a list of shift objects."})
        staff.shifts = shifts
        staff.save(update_fields=["shifts", "updated_at"])
        _log_staff_action(staff, request.user, "assign_shift", metadata={"shifts": shifts})
        return Response(self.get_serializer(staff).data)

    @action(detail=True, methods=["post"])
    def refresh_shifts(self, request, pk=None):
        shifts = request.data.get("shifts")
        if isinstance(shifts, list):
            staff = self.get_object()
            staff.shifts = shifts
            staff.save(update_fields=["shifts", "updated_at"])
            _log_staff_action(staff, request.user, "refresh_shifts", metadata={"shifts": shifts})
            return Response(self.get_serializer(staff).data)
        return Response({"detail": "Provide 'shifts' array in payload."}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=["Compliance"])
class ComplianceAuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = models.ComplianceAuditLog.objects.select_related("actor").all()
    serializer_class = serializers.ComplianceAuditLogSerializer
    permission_classes = [CanAccessComplianceData]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["severity", "action", "target_type"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return queryset
        # Audit log targets are polymorphic (target_type/target_id), so we
        # can't join to a single org/patient FK generically. Scope to the
        # caller's own actions plus any patient-targeted entries for
        # patients they can access - this closes the platform-wide read
        # (any authenticated user could previously page through every
        # patient-access log) without needing bespoke resolution for every
        # possible target_type.
        own_patient_ids = {str(pid) for pid in _accessible_patient_ids(user) or []}
        return queryset.filter(
            Q(actor=user) | (Q(target_type="PatientMasterRecord") & Q(target_id__in=own_patient_ids))
        )


@extend_schema(tags=["Compliance"])
class CredentialVerificationViewSet(OrgScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.CredentialVerification.objects.select_related("staff_profile", "staff_profile__profile", "verified_by").all()
    serializer_class = serializers.CredentialVerificationSerializer
    permission_classes = [CanAccessComplianceData]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["staff_profile", "status"]
    ordering_fields = ["expires_at"]
    org_lookup = "staff_profile__profile__organization"

    def perform_create(self, serializer):
        staff_profile = serializer.validated_data.get("staff_profile")
        if staff_profile is not None:
            self._assert_org_write_access(staff_profile.profile.organization_id)
        serializer.save()

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        credential = self.get_object()
        self._assert_org_write_access(credential.staff_profile.profile.organization_id)
        credential.mark_verified(request.user)
        models.ComplianceAuditLog.log(
            actor=request.user,
            action="compliance.credential.verify",
            target_type="CredentialVerification",
            target_id=str(credential.id),
            severity=models.ComplianceAuditLog.SEVERITY_MEDIUM,
            metadata={
                "credential_type": credential.credential_type,
                "license_number": credential.license_number,
            },
        )
        return Response(self.get_serializer(credential).data)


@extend_schema(tags=["Compliance"])
class RegulatoryReportViewSet(OrgScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.RegulatoryReport.objects.select_related("profile", "organization").all()
    serializer_class = serializers.RegulatoryReportSerializer
    permission_classes = [CanAccessComplianceData]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["profile", "organization", "report_type", "status"]
    ordering_fields = ["period_start"]

    def perform_create(self, serializer):
        organization = serializer.validated_data.get("organization")
        self._assert_org_write_access(organization.id if organization else None)
        serializer.save()

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        report = self.get_object()
        self._assert_org_write_access(report.organization_id)
        report.submit()
        models.ComplianceAuditLog.log(
            actor=request.user,
            action="compliance.regulatory_report.submit",
            target_type="RegulatoryReport",
            target_id=str(report.id),
            severity=models.ComplianceAuditLog.SEVERITY_HIGH,
            metadata={
                "report_type": report.report_type,
                "period_start": str(report.period_start),
                "period_end": str(report.period_end),
            },
        )
        return Response(self.get_serializer(report).data)


@extend_schema(tags=["Compliance"])
class ComplianceDocumentViewSet(OrgScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.ComplianceDocument.objects.select_related("profile", "organization", "signed_by").all()
    serializer_class = serializers.ComplianceDocumentSerializer
    permission_classes = [CanAccessComplianceData]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["profile", "organization", "status", "is_signed"]
    ordering_fields = ["created_at"]

    def perform_create(self, serializer):
        organization = serializer.validated_data.get("organization")
        self._assert_org_write_access(organization.id if organization else None)
        serializer.save()

    @action(detail=True, methods=["post"])
    def sign(self, request, pk=None):
        document = self.get_object()
        self._assert_org_write_access(document.organization_id)
        document.mark_signed(request.user)
        models.ComplianceAuditLog.log(
            actor=request.user,
            action="compliance.document.sign",
            target_type="ComplianceDocument",
            target_id=str(document.id),
            severity=models.ComplianceAuditLog.SEVERITY_MEDIUM,
            metadata={
                "document_name": document.document_name,
                "profile": document.profile_id,
            },
        )
        return Response(self.get_serializer(document).data)


@extend_schema(tags=["Compliance"])
class DataAccessConsentViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.DataAccessConsent.objects.select_related("patient").all()
    serializer_class = serializers.DataAccessConsentSerializer
    permission_classes = [CanAccessComplianceData]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["patient", "granted_to", "status"]
    ordering_fields = ["created_at"]

    def perform_create(self, serializer):
        self._assert_write_access(serializer.validated_data.get("patient"))
        serializer.save()

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        consent = self.get_object()
        consent.revoke()
        models.ComplianceAuditLog.log(
            actor=request.user,
            action="compliance.data_access.revoke",
            target_type="DataAccessConsent",
            target_id=str(consent.id),
            severity=models.ComplianceAuditLog.SEVERITY_HIGH,
            metadata={
                "patient": consent.patient_id,
                "granted_to": consent.granted_to,
            },
        )
        return Response(self.get_serializer(consent).data)


@extend_schema(tags=["Patients"])
class HealthDataAccessGrantViewSet(viewsets.ModelViewSet):
    queryset = models.HealthDataAccessGrant.objects.select_related("patient", "granted_to", "granted_by").all()
    serializer_class = serializers.HealthDataAccessGrantSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["patient", "granted_to", "role", "scope", "status"]
    ordering_fields = ["created_at", "expires_at"]

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self.request.user, "is_superuser", False):
            return queryset
        patient_ids = models.PatientMasterRecord.objects.filter(
            primary_contact__user_id=str(getattr(self.request.user, "id", "") or "")
        ).values_list("id", flat=True)
        return queryset.filter(Q(granted_to=self.request.user) | Q(patient_id__in=patient_ids))

    def perform_create(self, serializer):
        patient = serializer.validated_data.get("patient")
        allowed, reason, _grant = _can_access_patient_record(
            self.request.user,
            patient,
            required_scope=models.HealthDataAccessGrant.SCOPE_FULL,
        )
        if not allowed or reason not in {"owner", "superuser"}:
            raise PermissionDenied("Only the patient owner can grant health data access.")
        grant = serializer.save(granted_by=self.request.user)
        _log_patient_access(
            self.request.user,
            patient,
            action="health_data_access.grant.create",
            reason="owner_grant",
            grant=grant,
            metadata={"granted_to": str(grant.granted_to_id), "scope": grant.scope},
        )

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        grant = self.get_object()
        allowed, reason, _ = _can_access_patient_record(
            request.user,
            grant.patient,
            required_scope=models.HealthDataAccessGrant.SCOPE_FULL,
        )
        if not allowed or (reason not in {"owner", "superuser"} and grant.granted_to_id != request.user.id):
            raise PermissionDenied("You cannot revoke this access grant.")
        grant.revoke()
        _log_patient_access(
            request.user,
            grant.patient,
            action="health_data_access.grant.revoke",
            reason="grant_revoked",
            grant=grant,
        )
        return Response(self.get_serializer(grant).data)


@extend_schema(tags=["Patients"])
class PatientMasterRecordViewSet(viewsets.ModelViewSet):
    queryset = (
        models.PatientMasterRecord.objects.select_related("organization")
        .prefetch_related(
            "family_profiles",
            "consents",
            "encounters",
            "appointments",
            "medications",
            "allergies",
            "vitals",
            "wellness_metrics",
            "problems",
            "immunizations",
            "procedures",
            "documents",
        )
        .all()
    )
    serializer_class = serializers.PatientMasterRecordSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["tenant_id", "mrn", "status", "organization"]
    search_fields = ["first_name", "last_name", "mrn"]

    def get_queryset(self):
        queryset = super().get_queryset()
        allowed_patient_ids = _accessible_patient_ids(self.request.user)
        if allowed_patient_ids is None:
            return queryset
        return queryset.filter(id__in=allowed_patient_ids)

    def get_serializer_class(self):
        if self.action in ("retrieve",):
            return serializers.PatientMasterRecordDetailSerializer
        return serializers.PatientMasterRecordSerializer

    def perform_create(self, serializer):
        patient = serializer.save()
        self._annotate_patient_intake(patient)

    def _annotate_patient_intake(self, patient: models.PatientMasterRecord):
        if getattr(self.request.user, "is_authenticated", False):
            notification_services.create_notification(
                user_id=self.request.user.id,
                type="patient.intake.created",
                title="New patient intake ready",
                body=f"{patient.first_name} {patient.last_name} ({patient.mrn}) was added to the master index.",
                context={"patient_id": str(patient.id)},
                dedup_key=f"patient:intake:{patient.id}",
            )

    @extend_schema(
        summary="Get patient profile summary with family and consent detail"
    )
    @action(detail=True, methods=["get"])
    def summary(self, request, pk=None):
        patient = self.get_object()
        allowed, reason, grant = _can_access_patient_record(
            request.user,
            patient,
            required_scope=models.HealthDataAccessGrant.SCOPE_SUMMARY,
        )
        if not allowed:
            raise PermissionDenied("You do not have access to this patient summary.")
        _log_patient_access(request.user, patient, "patient.summary.read", reason, grant=grant)
        serializer = serializers.PatientMasterRecordDetailSerializer(patient)
        return Response(serializer.data)

    @extend_schema(
        summary="Get canonical patient health profile",
        responses=serializers.PatientCanonicalHealthProfileSerializer,
    )
    @action(detail=True, methods=["get"], url_path="health-profile")
    def health_profile(self, request, pk=None):
        patient = self.get_object()
        allowed, reason, grant = _can_access_patient_record(
            request.user,
            patient,
            required_scope=models.HealthDataAccessGrant.SCOPE_RECORDS,
        )
        if not allowed:
            raise PermissionDenied("You do not have access to this health profile.")
        _log_patient_access(request.user, patient, "patient.health_profile.read", reason, grant=grant)
        serializer = serializers.PatientCanonicalHealthProfileSerializer(patient, context={"request": request})
        return Response(serializer.data)

    @extend_schema(
        summary="Get patient-facing health summary",
        responses=serializers.PatientHealthSummarySerializer,
    )
    @action(detail=True, methods=["get"], url_path="health-summary")
    def health_summary(self, request, pk=None):
        patient = self.get_object()
        allowed, reason, grant = _can_access_patient_record(
            request.user,
            patient,
            required_scope=models.HealthDataAccessGrant.SCOPE_SUMMARY,
        )
        if not allowed:
            raise PermissionDenied("You do not have access to this health summary.")
        _log_patient_access(request.user, patient, "patient.health_summary.read", reason, grant=grant)
        serializer = serializers.PatientHealthSummarySerializer(patient, context={"request": request})
        return Response(serializer.data)

    @extend_schema(
        summary="Get patient emergency card",
        responses=serializers.PatientEmergencyCardSerializer,
    )
    @action(detail=True, methods=["get"], url_path="emergency-card")
    def emergency_card(self, request, pk=None):
        patient = self.get_object()
        allowed, reason, grant = _can_access_patient_record(
            request.user,
            patient,
            required_scope=models.HealthDataAccessGrant.SCOPE_EMERGENCY,
            emergency=True,
        )
        if not allowed:
            raise PermissionDenied("You do not have access to this emergency card.")
        _log_patient_access(request.user, patient, "patient.emergency_card.read", reason, grant=grant)
        serializer = serializers.PatientEmergencyCardSerializer(patient, context={"request": request})
        return Response(serializer.data)

    @extend_schema(
        summary="Get my canonical health profile",
        responses=serializers.PatientCanonicalHealthProfileSerializer,
    )
    @action(detail=False, methods=["get"], url_path="my-health-profile")
    def my_health_profile(self, request):
        patient = _resolve_patient_for_user(request.user)
        if not patient:
            user = request.user
            user_id = str(getattr(user, "id", "") or "").strip()
            email = str(getattr(user, "email", "") or "").strip()
            phone = str(getattr(user, "phone", "") or getattr(user, "phone_number", "") or "").strip()
            first_name = str(getattr(user, "first_name", "") or "").strip() or "User"
            last_name = str(getattr(user, "last_name", "") or "").strip() or user_id[:8] or "Unknown"
            mrn = f"MRN-{uuid.uuid4().hex[:12].upper()}"
            primary_contact = {}
            if user_id:
                primary_contact["user_id"] = user_id
            if email:
                primary_contact["email"] = email
            if phone:
                primary_contact["phone"] = phone
            if not user_id:
                return Response(
                    {"detail": "Unable to resolve or create patient record.", "code": "patient_profile_not_linked"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            with transaction.atomic():
                existing = models.PatientMasterRecord.objects.filter(
                    primary_contact__user_id=user_id
                ).first()
                if existing:
                    patient = existing
                else:
                    patient = models.PatientMasterRecord.objects.create(
                        mrn=mrn,
                        first_name=first_name,
                        last_name=last_name,
                        primary_contact=primary_contact,
                        status=models.PatientMasterRecord.STATUS_ACTIVE,
                    )
        _log_patient_access(request.user, patient, "patient.health_profile.read", "owner")
        serializer = serializers.PatientCanonicalHealthProfileSerializer(
            patient,
            context={"request": request, "linked_user": request.user},
        )
        return Response(serializer.data)

    @extend_schema(
        summary="Get my patient-facing health summary",
        responses=serializers.PatientHealthSummarySerializer,
    )
    @action(detail=False, methods=["get"], url_path="my-health-summary")
    def my_health_summary(self, request):
        patient = _resolve_patient_for_user(request.user)
        if not patient:
            return Response(
                {
                    "detail": "No linked patient master record found for the current user.",
                    "code": "patient_profile_not_linked",
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        _log_patient_access(request.user, patient, "patient.health_summary.read", "owner")
        serializer = serializers.PatientHealthSummarySerializer(
            patient,
            context={"request": request, "linked_user": request.user},
        )
        return Response(serializer.data)

    @extend_schema(
        summary="Get my emergency card",
        responses=serializers.PatientEmergencyCardSerializer,
    )
    @action(detail=False, methods=["get"], url_path="my-emergency-card")
    def my_emergency_card(self, request):
        patient = _resolve_patient_for_user(request.user)
        if not patient:
            return Response(
                {
                    "detail": "No linked patient master record found for the current user.",
                    "code": "patient_profile_not_linked",
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        _log_patient_access(request.user, patient, "patient.emergency_card.read", "owner")
        serializer = serializers.PatientEmergencyCardSerializer(
            patient,
            context={"request": request, "linked_user": request.user},
        )
        return Response(serializer.data)

    @extend_schema(summary="Get sharing summary for patient health data")
    @action(detail=True, methods=["get"], url_path="sharing-summary")
    def sharing_summary(self, request, pk=None):
        patient = self.get_object()
        allowed, reason, grant = _can_access_patient_record(
            request.user,
            patient,
            required_scope=models.HealthDataAccessGrant.SCOPE_SUMMARY,
        )
        if not allowed:
            raise PermissionDenied("You do not have access to this sharing summary.")
        _log_patient_access(request.user, patient, "patient.sharing_summary.read", reason, grant=grant)
        grants = patient.access_grants.select_related("granted_to", "granted_by").order_by("-created_at")[:20]
        return Response(
            {
                "patient_id": str(patient.id),
                "viewer_mode": reason,
                "grants": serializers.HealthDataAccessGrantSerializer(grants, many=True).data,
                "active_grants_count": patient.access_grants.filter(status=models.HealthDataAccessGrant.STATUS_ACTIVE).count(),
            }
        )

    @extend_schema(summary="Get access history for patient health data")
    @action(detail=True, methods=["get"], url_path="access-history")
    def access_history(self, request, pk=None):
        patient = self.get_object()
        allowed, reason, grant = _can_access_patient_record(
            request.user,
            patient,
            required_scope=models.HealthDataAccessGrant.SCOPE_SUMMARY,
        )
        if not allowed:
            raise PermissionDenied("You do not have access to this access history.")
        _log_patient_access(request.user, patient, "patient.access_history.read", reason, grant=grant)
        logs = models.ComplianceAuditLog.objects.filter(
            target_type="PatientMasterRecord",
            target_id=str(patient.id),
        ).order_by("-created_at")[:50]
        return Response(
            {
                "patient_id": str(patient.id),
                "results": serializers.ComplianceAuditLogSerializer(logs, many=True).data,
            }
        )

    @extend_schema(summary="Get my sharing summary for health data")
    @action(detail=False, methods=["get"], url_path="my-sharing-summary")
    def my_sharing_summary(self, request):
        patient = _resolve_patient_for_user(request.user)
        if not patient:
            return Response(
                {"detail": "No linked patient master record found for the current user.", "code": "patient_profile_not_linked"},
                status=status.HTTP_404_NOT_FOUND,
            )
        grants = patient.access_grants.select_related("granted_to", "granted_by").order_by("-created_at")[:20]
        return Response(
            {
                "patient_id": str(patient.id),
                "viewer_mode": "owner",
                "grants": serializers.HealthDataAccessGrantSerializer(grants, many=True).data,
                "active_grants_count": patient.access_grants.filter(status=models.HealthDataAccessGrant.STATUS_ACTIVE).count(),
            }
        )

    @extend_schema(summary="Get my access history for health data")
    @action(detail=False, methods=["get"], url_path="my-access-history")
    def my_access_history(self, request):
        patient = _resolve_patient_for_user(request.user)
        if not patient:
            return Response(
                {"detail": "No linked patient master record found for the current user.", "code": "patient_profile_not_linked"},
                status=status.HTTP_404_NOT_FOUND,
            )
        logs = models.ComplianceAuditLog.objects.filter(
            target_type="PatientMasterRecord",
            target_id=str(patient.id),
        ).order_by("-created_at")[:50]
        return Response(
            {
                "patient_id": str(patient.id),
                "results": serializers.ComplianceAuditLogSerializer(logs, many=True).data,
            }
        )

    @extend_schema(summary="Export patient health record bundle")
    @action(detail=True, methods=["get"], url_path="export-bundle")
    def export_bundle(self, request, pk=None):
        patient = self.get_object()
        bundle = export_patient_fhir_bundle(patient)
        models.HealthRecordExchangeLog.objects.create(
            patient=patient,
            actor=request.user if getattr(request.user, "is_authenticated", False) else None,
            direction=models.HealthRecordExchangeLog.DIRECTION_EXPORT,
            exchange_format=models.HealthRecordExchangeLog.FORMAT_FHIR_BUNDLE,
            source_label="patient_export",
            status=models.HealthRecordExchangeLog.STATUS_SUCCESS,
            summary={"entry_count": len(bundle.get("entry") or [])},
            raw_payload=bundle,
        )
        return Response(bundle)

    @extend_schema(summary="Import patient health record bundle")
    @action(detail=True, methods=["post"], url_path="import-bundle")
    def import_bundle(self, request, pk=None):
        patient = self.get_object()
        bundle = request.data.get("bundle") if isinstance(request.data, dict) else None
        if not isinstance(bundle, dict):
            raise ValidationError({"bundle": "A bundle object is required."})
        try:
            summary = import_patient_fhir_bundle(patient=patient, bundle=bundle, actor=request.user)
            models.HealthRecordExchangeLog.objects.create(
                patient=patient,
                actor=request.user if getattr(request.user, "is_authenticated", False) else None,
                direction=models.HealthRecordExchangeLog.DIRECTION_IMPORT,
                exchange_format=models.HealthRecordExchangeLog.FORMAT_FHIR_BUNDLE,
                source_label=str(request.data.get("source_label") or "bundle_import"),
                status=models.HealthRecordExchangeLog.STATUS_SUCCESS,
                summary=summary,
                raw_payload=bundle,
            )
            models.ComplianceAuditLog.log(
                actor=request.user,
                action="health_record.import",
                target_type="PatientMasterRecord",
                target_id=str(patient.id),
                metadata=summary,
            )
            return Response({"patient_id": str(patient.id), "summary": summary}, status=status.HTTP_200_OK)
        except Exception as exc:
            models.HealthRecordExchangeLog.objects.create(
                patient=patient,
                actor=request.user if getattr(request.user, "is_authenticated", False) else None,
                direction=models.HealthRecordExchangeLog.DIRECTION_IMPORT,
                exchange_format=models.HealthRecordExchangeLog.FORMAT_FHIR_BUNDLE,
                source_label=str(request.data.get("source_label") or "bundle_import"),
                status=models.HealthRecordExchangeLog.STATUS_FAILED,
                summary={"error": str(exc)},
                raw_payload=bundle,
            )
            raise ValidationError({"bundle": str(exc)})

    @extend_schema(summary="Export my health record bundle")
    @action(detail=False, methods=["get"], url_path="my-export-bundle")
    def my_export_bundle(self, request):
        patient = _resolve_patient_for_user(request.user)
        if not patient:
            return Response(
                {"detail": "No linked patient master record found for the current user.", "code": "patient_profile_not_linked"},
                status=status.HTTP_404_NOT_FOUND,
            )
        bundle = export_patient_fhir_bundle(patient)
        models.HealthRecordExchangeLog.objects.create(
            patient=patient,
            actor=request.user if getattr(request.user, "is_authenticated", False) else None,
            direction=models.HealthRecordExchangeLog.DIRECTION_EXPORT,
            exchange_format=models.HealthRecordExchangeLog.FORMAT_FHIR_BUNDLE,
            source_label="my_patient_export",
            status=models.HealthRecordExchangeLog.STATUS_SUCCESS,
            summary={"entry_count": len(bundle.get("entry") or [])},
            raw_payload=bundle,
        )
        return Response(bundle)

    @extend_schema(summary="Import into my health record from bundle")
    @action(detail=False, methods=["post"], url_path="my-import-bundle")
    def my_import_bundle(self, request):
        patient = _resolve_patient_for_user(request.user)
        if not patient:
            return Response(
                {"detail": "No linked patient master record found for the current user.", "code": "patient_profile_not_linked"},
                status=status.HTTP_404_NOT_FOUND,
            )
        bundle = request.data.get("bundle") if isinstance(request.data, dict) else None
        if not isinstance(bundle, dict):
            raise ValidationError({"bundle": "A bundle object is required."})
        try:
            summary = import_patient_fhir_bundle(patient=patient, bundle=bundle, actor=request.user)
            models.HealthRecordExchangeLog.objects.create(
                patient=patient,
                actor=request.user if getattr(request.user, "is_authenticated", False) else None,
                direction=models.HealthRecordExchangeLog.DIRECTION_IMPORT,
                exchange_format=models.HealthRecordExchangeLog.FORMAT_FHIR_BUNDLE,
                source_label=str(request.data.get("source_label") or "my_bundle_import"),
                status=models.HealthRecordExchangeLog.STATUS_SUCCESS,
                summary=summary,
                raw_payload=bundle,
            )
            return Response({"patient_id": str(patient.id), "summary": summary}, status=status.HTTP_200_OK)
        except Exception as exc:
            models.HealthRecordExchangeLog.objects.create(
                patient=patient,
                actor=request.user if getattr(request.user, "is_authenticated", False) else None,
                direction=models.HealthRecordExchangeLog.DIRECTION_IMPORT,
                exchange_format=models.HealthRecordExchangeLog.FORMAT_FHIR_BUNDLE,
                source_label=str(request.data.get("source_label") or "my_bundle_import"),
                status=models.HealthRecordExchangeLog.STATUS_FAILED,
                summary={"error": str(exc)},
                raw_payload=bundle,
            )
            raise ValidationError({"bundle": str(exc)})


@extend_schema(tags=["Patients"])
class FamilyProfileViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.PatientFamilyProfile.objects.select_related("patient").all()
    serializer_class = serializers.FamilyProfileSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "relationship"]

    def perform_create(self, serializer):
        self._assert_write_access(serializer.validated_data.get("patient"))
        serializer.save()


@extend_schema(tags=["Patients"])
class ConsentRecordViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.ConsentRecord.objects.select_related("patient").all()
    serializer_class = serializers.ConsentRecordSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "purpose", "granted_by"]
    search_fields = ["purpose"]

    def perform_create(self, serializer):
        self._assert_write_access(serializer.validated_data.get("patient"))
        granted_by = serializer.validated_data.get("granted_by")
        if granted_by is None and getattr(self.request.user, "is_authenticated", False):
            serializer.save(granted_by=self.request.user)
            return
        serializer.save()


@extend_schema(tags=["Patients"])
class EncounterViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.EncounterNote.objects.select_related("patient", "organization", "clinician").all()
    serializer_class = serializers.EncounterSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "organization", "encounter_type"]

    def perform_create(self, serializer):
        self._assert_write_access(serializer.validated_data.get("patient"))
        encounter = serializer.save(clinician=self.request.user)
        if not encounter:
            return


@extend_schema(tags=["Patients"])
class AppointmentViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.Appointment.objects.select_related("patient", "profile").all()
    serializer_class = serializers.AppointmentSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "profile", "status"]

    def perform_create(self, serializer):
        self._assert_write_access(serializer.validated_data.get("patient"))
        serializer.save()


@extend_schema(tags=["Patients"])
class MedicationOrderViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.MedicationOrder.objects.select_related("patient", "clinician", "profile").all()
    serializer_class = serializers.MedicationOrderSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "profile", "status", "drug_name"]
    search_fields = ["drug_name"]

    def perform_create(self, serializer):
        patient = serializer.validated_data.get("patient")
        self._assert_write_access(patient)
        order = serializer.save(clinician=self.request.user)
        order.fhir_resource = {
            "resourceType": "MedicationRequest",
            "status": order.status,
            "medicationCodeableConcept": {"text": order.drug_name},
            "subject": {"reference": f"Patient/{patient.mrn}"},
            "dosageInstruction": [
                {
                    "text": f"{order.route} {order.dosage} {order.frequency}".strip(),
                    "route": {"text": order.route},
                }
            ],
        }
        order.save(update_fields=["fhir_resource"])


@extend_schema(tags=["Patients"])
class AllergyRecordViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.AllergyRecord.objects.select_related("patient").all()
    serializer_class = serializers.AllergyRecordSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "status", "severity"]
    search_fields = ["agent"]

    def perform_create(self, serializer):
        self._assert_write_access(serializer.validated_data.get("patient"))
        allergy = serializer.save()
        if not allergy:
            return


@extend_schema(tags=["Patients"])
class VitalSignViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.VitalSign.objects.select_related("patient", "profile", "clinician").all()
    serializer_class = serializers.VitalSignSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "vital_type"]
    search_fields = ["notes"]

    def perform_create(self, serializer):
        self._assert_write_access(serializer.validated_data.get("patient"))
        vital = serializer.save(clinician=self.request.user)
        if not vital:
            return


@extend_schema(tags=["Patients"])
class ProblemRecordViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.ProblemRecord.objects.select_related("patient", "profile", "diagnosed_by").all()
    serializer_class = serializers.ProblemRecordSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "profile", "clinical_status", "verification_status", "severity"]
    search_fields = ["title", "code", "notes"]

    def perform_create(self, serializer):
        self._assert_write_access(serializer.validated_data.get("patient"))
        serializer.save(diagnosed_by=self.request.user)


@extend_schema(tags=["Patients"])
class ImmunizationRecordViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.ImmunizationRecord.objects.select_related("patient", "profile", "administered_by").all()
    serializer_class = serializers.ImmunizationRecordSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "profile", "status", "vaccine_code"]
    search_fields = ["vaccine_name", "manufacturer", "lot_number"]

    def perform_create(self, serializer):
        self._assert_write_access(serializer.validated_data.get("patient"))
        serializer.save(administered_by=self.request.user)


@extend_schema(tags=["Patients"])
class ProcedureRecordViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.ProcedureRecord.objects.select_related("patient", "profile", "performed_by").all()
    serializer_class = serializers.ProcedureRecordSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "profile", "status", "procedure_code"]
    search_fields = ["procedure_name", "location", "notes"]

    def perform_create(self, serializer):
        self._assert_write_access(serializer.validated_data.get("patient"))
        serializer.save(performed_by=self.request.user)


@extend_schema(tags=["Patients"])
class HealthDocumentViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.HealthDocument.objects.select_related("patient", "profile", "uploaded_by").all()
    serializer_class = serializers.HealthDocumentSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "profile", "category", "status", "source_type"]
    search_fields = ["title", "source_label", "notes", "file_url"]

    def perform_create(self, serializer):
        self._assert_write_access(serializer.validated_data.get("patient"))
        serializer.save(uploaded_by=self.request.user)


@extend_schema(tags=["Patients"])
class HealthRecordExchangeLogViewSet(PatientScopedQuerySetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = models.HealthRecordExchangeLog.objects.select_related("patient", "actor").all()
    serializer_class = serializers.HealthRecordExchangeLogSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "direction", "exchange_format", "status"]


@extend_schema(tags=["Patients"])
class WellnessMetricViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.WellnessMetric.objects.select_related("patient", "profile", "recorded_by").all()
    serializer_class = serializers.WellnessMetricSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "profile", "metric_type", "source", "is_clinically_verified"]
    search_fields = ["source_label"]

    def perform_create(self, serializer):
        self._assert_write_access(serializer.validated_data.get("patient"))
        serializer.save(recorded_by=self.request.user)


@extend_schema(tags=["Clinical"])
class ClinicalTaskViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.ClinicalTask.objects.select_related("patient", "assigned_to", "created_by").all()
    serializer_class = serializers.ClinicalTaskSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "assigned_to", "status", "priority"]
    search_fields = ["title", "description"]

    def perform_create(self, serializer):
        self._assert_write_access(serializer.validated_data.get("patient"))
        task = serializer.save(created_by=self.request.user)
        if task.assigned_to_id:
            notification_services.create_notification(
                user_id=task.assigned_to_id,
                type="clinical.task.created",
                title="New clinical task assigned",
                body=f"{task.title} · {task.description[:120]}",
                context={"task_id": str(task.id), "patient_id": str(task.patient_id)},
                dedup_key=f"clinical:task:{task.id}:assigned",
            )
        _record_clinical_event(
            patient=task.patient,
            event_type=models.ClinicalEventLog.EVENT_TASK_CREATED,
            description=f"Task '{task.title}' created ({task.priority}).",
            user=self.request.user,
            task=task,
        )

    def perform_update(self, serializer):
        existing = self.get_object()
        prior_status = existing.status
        task = serializer.save()
        if prior_status != task.status and task.status == models.ClinicalTask.STATUS_COMPLETED:
            if not task.completed_at:
                task.completed_at = timezone.now()
                task.save(update_fields=["completed_at"])
            _record_clinical_event(
                patient=task.patient,
                event_type=models.ClinicalEventLog.EVENT_TASK_COMPLETED,
                description=f"Task '{task.title}' completed.",
                user=self.request.user,
                task=task,
            )


@extend_schema(tags=["Clinical"])
class EmergencyEscalationViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.EmergencyEscalation.objects.select_related("patient", "reported_by").all()
    serializer_class = serializers.EmergencyEscalationSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "severity", "status"]

    def perform_create(self, serializer):
        self._assert_write_access(serializer.validated_data.get("patient"))
        escalation = serializer.save(reported_by=self.request.user)
        notified_users = []
        if escalation.patient and escalation.patient.organization_id:
            staff = models.StaffProfile.objects.filter(
                profile__organization_id=escalation.patient.organization_id, is_on_call=True
            )
            for member in staff:
                user = member.user
                if user and user.id:
                    notified_users.append(str(user.id))
                    notification_services.create_notification(
                        user_id=user.id,
                        type="clinical.escalation",
                        title="Emergency escalation recorded",
                        body=f"{escalation.severity.title()} escalation for {escalation.patient}.",
                        context={"escalation_id": str(escalation.id)},
                        dedup_key=f"clinical:escalation:{escalation.id}",
                    )
        _record_clinical_event(
            patient=escalation.patient,
            event_type=models.ClinicalEventLog.EVENT_ESCALATION,
            description=f"Escalation ({escalation.severity}) recorded and notified {len(notified_users)} staff.",
            user=self.request.user,
            escalation=escalation,
            metadata={"notified": notified_users},
        )

    def perform_update(self, serializer):
        prior_status = self.get_object().status
        escalation = serializer.save()
        if prior_status != escalation.status and escalation.status == models.EmergencyEscalation.STATUS_RESOLVED:
            escalation.resolved_at = timezone.now()
            escalation.save(update_fields=["resolved_at"])
            _record_clinical_event(
                patient=escalation.patient,
                event_type=models.ClinicalEventLog.EVENT_ESCALATION,
                description="Escalation resolved.",
                user=self.request.user,
                escalation=escalation,
            )


@extend_schema(tags=["Clinical"])
class TriageRecordViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.TriageRecord.objects.select_related("patient", "created_by").all()
    serializer_class = serializers.TriageRecordSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "acuity_level"]

    def perform_create(self, serializer):
        self._assert_write_access(serializer.validated_data.get("patient"))
        record = serializer.save(created_by=self.request.user)
        new_level = record.acuity_level
        _record_clinical_event(
            patient=record.patient,
            event_type=models.ClinicalEventLog.EVENT_TRIAGE,
            description=f"Triage completed ({new_level}).",
            user=self.request.user,
            triage=record,
        )


@extend_schema(tags=["Clinical"])
class ReferralRouteViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.ReferralRoute.objects.select_related("patient", "from_organization", "to_organization").all()
    serializer_class = serializers.ReferralRouteSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "from_organization", "to_organization", "status"]

    def perform_create(self, serializer):
        self._assert_write_access(serializer.validated_data.get("patient"))
        referral = serializer.save(created_by=self.request.user)
        _record_clinical_event(
            patient=referral.patient,
            event_type=models.ClinicalEventLog.EVENT_REFERRAL,
            description=f"Referral to {referral.to_organization or referral.metadata.get('to_org_name')}.",
            user=self.request.user,
            referral=referral,
        )


@extend_schema(tags=["Clinical"])
class ClinicalEventLogViewSet(PatientScopedQuerySetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = models.ClinicalEventLog.objects.select_related("patient", "triggered_by").all()
    serializer_class = serializers.ClinicalEventLogSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "event_type"]

    @action(detail=False, methods=["post"])
    def acknowledge(self, request):
        return Response({"detail": "Use specialized routes to mark events acknowledged."})


class CommandCenterOverview(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Never trust a client-supplied institution/organization id here - the
        # frontend doesn't even send one today, and even if it did, scope must
        # be derived from the authenticated user's own org memberships.
        org_ids = _user_org_ids(request.user)

        task_qs = models.ClinicalTask.objects.filter(
            status__in=[models.ClinicalTask.STATUS_PENDING, models.ClinicalTask.STATUS_IN_PROGRESS]
        )
        escalation_qs = models.EmergencyEscalation.objects.exclude(status=models.EmergencyEscalation.STATUS_RESOLVED)
        referral_qs = models.ReferralRoute.objects.filter(status=models.ReferralRoute.STATUS_PENDING)
        events_qs = models.ClinicalEventLog.objects.order_by("-created_at")
        triage_qs = models.TriageRecord.objects.order_by("-created_at")

        if org_ids is not None:
            if org_ids:
                task_qs = task_qs.filter(patient__organization_id__in=org_ids)
                escalation_qs = escalation_qs.filter(patient__organization_id__in=org_ids)
                referral_qs = referral_qs.filter(patient__organization_id__in=org_ids)
                events_qs = events_qs.filter(patient__organization_id__in=org_ids)
                triage_qs = triage_qs.filter(patient__organization_id__in=org_ids)
            else:
                # Authenticated but not staff at any org: scope to the
                # caller's own patient record(s) only, never platform-wide.
                own_patient_ids = _accessible_patient_ids(request.user)
                task_qs = task_qs.filter(patient_id__in=own_patient_ids)
                escalation_qs = escalation_qs.filter(patient_id__in=own_patient_ids)
                referral_qs = referral_qs.filter(patient_id__in=own_patient_ids)
                events_qs = events_qs.filter(patient_id__in=own_patient_ids)
                triage_qs = triage_qs.filter(patient_id__in=own_patient_ids)

        return Response(
            {
                "pending_tasks": task_qs.count(),
                "active_escalations": escalation_qs.count(),
                "open_referrals": referral_qs.count(),
                "recent_events": serializers.ClinicalEventLogSerializer(events_qs[:5], many=True).data,
                "recent_triage": serializers.TriageRecordSerializer(triage_qs[:3], many=True).data,
            }
        )


@extend_schema(tags=["Telemedicine"])
class TelemedicineSessionViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.TelemedicineSession.objects.select_related("patient", "clinician", "profile", "appointment").all()
    serializer_class = serializers.TelemedicineSessionSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["status", "patient", "clinician", "profile"]
    reminder_window_hours = 24

    def perform_create(self, serializer):
        self._assert_write_access(serializer.validated_data.get("patient"))
        clinician = serializer.validated_data.get("clinician")
        if clinician is None and getattr(self.request.user, "is_authenticated", False):
            clinician = self.request.user
        session = serializer.save(clinician=clinician)
        self._notify_session_created(session)

    def _notify_session_created(self, session: models.TelemedicineSession):
        if not session.clinician_id:
            return
        scheduled_at = (
            session.appointment.scheduled_at
            if session.appointment and session.appointment.scheduled_at
            else session.started_at
        )
        scheduled_label = scheduled_at.isoformat() if scheduled_at else "soon"
        notification_services.create_notification(
            user_id=session.clinician_id,
            type="telemedicine.session.created",
            title="Telemedicine session scheduled",
            body=(
                f"Session for {session.patient or 'a patient'} "
                f"is booked for {scheduled_label}."
            ),
            context={
                "session_id": str(session.id),
                "patient_id": str(session.patient_id) if session.patient_id else None,
                "appointment_id": str(session.appointment_id) if session.appointment_id else None,
            },
            dedup_key=f"telemedicine:session:{session.id}:created",
        )

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        session = self.get_object()
        session.status = models.TelemedicineSession.STATUS_ACTIVE
        session.started_at = timezone.now()
        session.save(update_fields=["status", "started_at"])
        return Response(self.get_serializer(session).data)

    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        session = self.get_object()
        session.status = models.TelemedicineSession.STATUS_ENDED
        session.ended_at = timezone.now()
        session.save(update_fields=["status", "ended_at"])
        return Response(self.get_serializer(session).data)


@extend_schema(tags=["Telemedicine"])
class TelemedicineDeviceViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.TelemedicineDevice.objects.select_related("session", "session__patient").all()
    serializer_class = serializers.TelemedicineDeviceSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["session", "device_type"]
    patient_lookup = "session__patient"

    def perform_create(self, serializer):
        session = serializer.validated_data.get("session")
        self._assert_write_access(session.patient if session else None)
        serializer.save()


@extend_schema(tags=["Telemedicine"])
class VoiceDictationViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.VoiceDictation.objects.select_related("session", "session__patient", "clinician").all()
    serializer_class = serializers.VoiceDictationSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["session", "clinician"]
    patient_lookup = "session__patient"

    def perform_create(self, serializer):
        session = serializer.validated_data.get("session")
        self._assert_write_access(session.patient if session else None)
        dictation = serializer.save()
        transcript = (
            dictation.audio_metadata.get("transcription")
            or dictation.audio_metadata.get("transcription_prompt")
            or dictation.audio_metadata.get("notes")
            or ""
        )
        dictation.transcript = transcript
        dictation.save(update_fields=["transcript"])


@extend_schema(tags=["Medical Resources"])
class InventoryItemViewSet(viewsets.ModelViewSet):
    queryset = models.InventoryItem.objects.select_related("profile", "organization").all()
    serializer_class = serializers.InventoryItemSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["profile", "organization", "status", "category"]
    search_fields = ["name", "sku"]

    def perform_update(self, serializer):
        # ensure quantity doesn't go negative
        item = serializer.save()
        if item.quantity_on_hand < Decimal("0.00"):
            item.quantity_on_hand = Decimal("0.00")
            item.save(update_fields=["quantity_on_hand"])


@extend_schema(tags=["Medical Resources"])
class DiagnosticOrderViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.DiagnosticOrder.objects.select_related("patient", "profile", "requested_by").all()
    serializer_class = serializers.DiagnosticOrderSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "profile", "status"]
    search_fields = ["test_name"]

    def perform_create(self, serializer):
        self._assert_write_access(serializer.validated_data.get("patient"))
        serializer.save()


@extend_schema(tags=["Medical Resources"])
class ImagingStudyViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.ImagingStudy.objects.select_related("patient", "profile").all()
    serializer_class = serializers.ImagingStudySerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "profile", "status", "modality"]
    search_fields = ["body_region"]

    def perform_create(self, serializer):
        self._assert_write_access(serializer.validated_data.get("patient"))
        serializer.save()


@extend_schema(tags=["Medical Resources"])
class MedicationAdherenceReminderViewSet(PatientScopedQuerySetMixin, viewsets.ModelViewSet):
    queryset = models.MedicationAdherenceReminder.objects.select_related("patient", "medication_order").all()
    serializer_class = serializers.MedicationAdherenceReminderSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["patient", "status", "channel"]

    def perform_create(self, serializer):
        self._assert_write_access(serializer.validated_data.get("patient"))
        serializer.save()

    @action(detail=True, methods=["post"])
    def mark_sent(self, request, pk=None):
        reminder = self.get_object()
        reminder.status = models.MedicationAdherenceReminder.STATUS_SENT
        reminder.save(update_fields=["status"])
        return Response(self.get_serializer(reminder).data)

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        reminder = self.get_object()
        reminder.status = models.MedicationAdherenceReminder.STATUS_ACKNOWLEDGED
        reminder.save(update_fields=["status"])
        return Response(self.get_serializer(reminder).data)


@extend_schema(tags=["Medical Resources"])
class SupplyForecastViewSet(viewsets.ModelViewSet):
    queryset = models.SupplyForecast.objects.select_related("profile").all()
    serializer_class = serializers.SupplyForecastSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["profile", "category"]
# ---------------------------
# Community / Group / Channel ViewSets
# ---------------------------
@extend_schema(tags=["Communities"])
class CommunityViewSet(viewsets.ModelViewSet):
    queryset = models.Community.objects.all()
    serializer_class = serializers.CommunitySerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [AllowAny()]
        return [IsAuthenticated()]
    
    def perform_create(self, serializer):
        # set owner to request.user by default if not provided
        owner = serializer.validated_data.get("owner", None)
        if owner is None:
            # set owner generic fields to request.user
            user_ct = ContentType.objects.get_for_model(self.request.user.__class__)
            serializer.validated_data["owner"] = self.request.user
        serializer.save()

    @extend_schema(
        request=serializers.AccessControlEntrySerializer,
        responses=serializers.AccessControlEntrySerializer,
        description="Grant an ACE to a principal on this community",
    )
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def grant(self, request, pk=None):
        community = self.get_object()
        data = request.data.copy()
        data["target"] = {"type": f"{community._meta.app_label}.{community._meta.model_name}", "id": str(community.pk)}
        serializer = serializers.AccessControlEntrySerializer(data=data)
        serializer.is_valid(raise_exception=True)
        ace = serializer.save()
        return Response(serializers.AccessControlEntrySerializer(ace).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Groups"])
class GroupViewSet(viewsets.ModelViewSet):
    queryset = models.Group.objects.select_related("community").all()
    serializer_class = serializers.GroupSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [AllowAny()]
        return [IsAuthenticated()]
    
    def perform_create(self, serializer):
        # Create group and default settings handled by serializer.create
        serializer.save()

    @extend_schema(description="Join a group respecting its join policy")
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def join(self, request, pk=None):
        group = self.get_object()
        user = request.user
        settings_obj = getattr(group, "settings", None)
        join_policy = settings_obj.join_policy if settings_obj else "OPEN"
        invite_token = request.data.get("invite_token")

        if join_policy == "INVITE" and not invite_token:
            return Response({"detail": "Invite token required"}, status=status.HTTP_400_BAD_REQUEST)

        if invite_token:
            try:
                invite = models.MembershipInvite.objects.get(token=invite_token, group=group)
            except models.MembershipInvite.DoesNotExist:
                return Response({"detail": "Invalid invite token"}, status=status.HTTP_400_BAD_REQUEST)
            if not invite.is_valid():
                return Response({"detail": "Invite expired/used"}, status=status.HTTP_400_BAD_REQUEST)
            invite.use()

        user_ct = ContentType.objects.get_for_model(user.__class__)
        with transaction.atomic():
            mem, created = models.Membership.objects.update_or_create(
                group=group,
                user_content_type=user_ct,
                user_object_id=str(user.pk),
                defaults={"status": models.Membership.STATUS_ACTIVE, "joined_at": timezone.now()},
            )
            group.recalc_member_count()
        return Response(serializers.MembershipSerializer(mem).data, status=status.HTTP_200_OK)

    @extend_schema(description="Leave a group")
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def leave(self, request, pk=None):
        group = self.get_object()
        user = request.user
        user_ct = ContentType.objects.get_for_model(user.__class__)
        try:
            mem = models.Membership.objects.get(group=group, user_content_type=user_ct, user_object_id=str(user.pk))
        except models.Membership.DoesNotExist:
            return Response({"detail": "Not a member"}, status=status.HTTP_400_BAD_REQUEST)
        mem.status = models.Membership.STATUS_LEFT
        mem.save()
        group.recalc_member_count()
        return Response({"detail": "Left group"}, status=status.HTTP_200_OK)

    @extend_schema(
        request=serializers.MembershipInviteSerializer,
        responses=serializers.MembershipInviteSerializer,
        description="Create an invite for the group",
    )
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def invite(self, request, pk=None):
        group = self.get_object()
        user = request.user
        if not (group.can_user(user, "group.invite") or group.is_member(user) and group.memberships.filter(
                user_content_type=ContentType.objects.get_for_model(user.__class__),
                user_object_id=str(user.pk),
                is_moderator=True).exists()):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        serializer = serializers.MembershipInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invite = serializer.save(group=group, created_by=user)
        return Response(serializers.MembershipInviteSerializer(invite).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        request={"type": "object", "properties": {"user": {}, "role": {"type": "string"}}},
        responses=serializers.MembershipSerializer,
        description="Promote a member to a role",
    )
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def promote(self, request, pk=None):
        group = self.get_object()
        user = request.user
        if not group.can_user(user, "group.manage_members"):
            if not group.memberships.filter(user_content_type=ContentType.objects.get_for_model(user.__class__),
                                            user_object_id=str(user.pk),
                                            is_moderator=True).exists():
                return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        target_user_data = request.data.get("user")
        role_id = request.data.get("role")
        if not target_user_data or not role_id:
            return Response({"detail": "user and role required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            target = serializers.GenericRelatedField().to_internal_value(target_user_data)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        role = get_object_or_404(models.Role, pk=role_id)
        user_ct = ContentType.objects.get_for_model(target.__class__)
        try:
            mem = models.Membership.objects.get(group=group, user_content_type=user_ct, user_object_id=str(target.pk))
        except models.Membership.DoesNotExist:
            return Response({"detail": "Target is not a member"}, status=status.HTTP_400_BAD_REQUEST)

        mem.promote_to_role(role)
        return Response(serializers.MembershipSerializer(mem).data, status=status.HTTP_200_OK)


@extend_schema(tags=["Channels"])
class ChannelViewSet(viewsets.ModelViewSet):
    queryset = models.Channel.objects.prefetch_related("communities", "groups").all()
    serializer_class = serializers.ChannelSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [AllowAny()]
        return [IsAuthenticated()]

    @extend_schema(description="Join a channel if allowed by group/community membership or ACEs")
    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def join(self, request, pk=None):
        channel = self.get_object()
        user = request.user

        if channel.is_public:
            for g in channel.groups.all():
                if g.is_member(user):
                    return Response({"detail": "Allowed via group membership"}, status=status.HTTP_200_OK)
            for c in channel.communities.all():
                if models.CommunityPermissionHelper.can_user_on_community(user, c, "community.member"):
                    return Response({"detail": "Allowed via community membership"}, status=status.HTTP_200_OK)

        if channel.can_user(user, "channel.join"):
            return Response({"detail": "Allowed"}, status=status.HTTP_200_OK)

        return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)


# ---------------------------
# Membership & Invite ViewSets
# ---------------------------
@extend_schema(tags=["Memberships"])
class MembershipViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet,
                        mixins.UpdateModelMixin, mixins.DestroyModelMixin):
    queryset = models.Membership.objects.select_related("group").all()
    serializer_class = serializers.MembershipSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if getattr(user, "is_superuser", False) or user.is_staff:
            return super().get_queryset()
        user_ct = ContentType.objects.get_for_model(user.__class__)
        return super().get_queryset().filter(user_content_type=user_ct, user_object_id=str(user.pk))


@extend_schema(tags=["MembershipInvites"])
class MembershipInviteViewSet(viewsets.ModelViewSet):
    queryset = models.MembershipInvite.objects.all()
    serializer_class = serializers.MembershipInviteSerializer
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request={"type": "object", "properties": {"token": {"type": "string"}}},
        responses=serializers.MembershipSerializer,
        description="Redeem an invite token to join a group/community",
    )
    @action(detail=False, methods=["post"], permission_classes=[AllowAny])
    def redeem(self, request):
        token = request.data.get("token")
        if not token:
            return Response({"detail": "token required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            invite = models.MembershipInvite.objects.get(token=token)
        except models.MembershipInvite.DoesNotExist:
            return Response({"detail": "Invalid token"}, status=status.HTTP_400_BAD_REQUEST)

        if not invite.is_valid():
            return Response({"detail": "Invite expired or used"}, status=status.HTTP_400_BAD_REQUEST)

        if not request.user or not request.user.is_authenticated:
            return Response({"detail": "Authentication required to redeem"}, status=status.HTTP_401_UNAUTHORIZED)

        user = request.user
        user_ct = ContentType.objects.get_for_model(user.__class__)

        with transaction.atomic():
            if invite.group:
                mem, created = models.Membership.objects.update_or_create(
                    group=invite.group,
                    user_content_type=user_ct,
                    user_object_id=str(user.pk),
                    defaults={"status": models.Membership.STATUS_ACTIVE, "joined_at": timezone.now()}
                )
                invite.use()
                invite.save()
                invite.group.recalc_member_count()
                return Response(serializers.MembershipSerializer(mem).data, status=status.HTTP_201_CREATED)
            elif invite.community:
                invite.use()
                return Response({"detail": "Invite redeemed for community (application-defined)"},
                                status=status.HTTP_200_OK)
            else:
                return Response({"detail": "Invite has no target"}, status=status.HTTP_400_BAD_REQUEST)


# ---------------------------
# ModerationAction ViewSet
# ---------------------------
@extend_schema(tags=["ModerationActions"])
class ModerationActionViewSet(viewsets.ModelViewSet):
    queryset = models.ModerationAction.objects.all()
    serializer_class = serializers.ModerationActionSerializer
    permission_classes = [IsAuthenticated]

    @extend_schema(description="Create a moderation action with permission checks")
    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        data.setdefault("performed_by", {"type": f"{request.user._meta.app_label}.{request.user._meta.model_name}", "id": str(request.user.pk)})
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)

        target = serializer.validated_data["target"]
        if not getattr(request.user, "is_superuser", False):
            if hasattr(target, "can_user"):
                if not target.can_user(request.user, "moderation.action"):
                    return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)


# ---------------------------
# Settings viewsets - update only
# ---------------------------
@extend_schema(tags=["GroupSettings"])
class GroupSettingsViewSet(viewsets.GenericViewSet, mixins.RetrieveModelMixin, mixins.UpdateModelMixin):
    queryset = models.GroupSettings.objects.select_related("group").all()
    serializer_class = serializers.GroupSettingsUpdateSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return super().get_object()


@extend_schema(tags=["ChannelSettings"])
class ChannelSettingsViewSet(viewsets.GenericViewSet, mixins.RetrieveModelMixin, mixins.UpdateModelMixin):
    queryset = models.ChannelSettings.objects.select_related("channel").all()
    serializer_class = serializers.ChannelSettingsUpdateSerializer
    permission_classes = [IsAuthenticated]


class SocialRecommendationFoundationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            limit = int(request.query_params.get("limit", 8))
        except (TypeError, ValueError):
            limit = 8
        return Response(
            privacy_safe_social_recommendation_foundation(request.user, limit=limit),
            status=status.HTTP_200_OK,
        )


class UnifiedPlatformDashboardSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            unified_platform_dashboard_summary(request.user),
            status=status.HTTP_200_OK,
        )


class StaffSafetyCommandCenterView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response(
            staff_safety_command_center_summary(),
            status=status.HTTP_200_OK,
        )


class PerformanceOfflinePolicyView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            performance_offline_policy(request.user),
            status=status.HTTP_200_OK,
        )


class SecurityPrivacyLaunchGateView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response(
            security_privacy_child_safety_launch_gate(),
            status=status.HTTP_200_OK,
        )


class LaunchOperationsReadinessView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response(
            staff_launch_operations_summary(),
            status=status.HTTP_200_OK,
        )


class MonetizationSafetySummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            monetization_without_legal_risk_summary(),
            status=status.HTTP_200_OK,
        )


class AIAssistanceSafetyPolicyView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            ai_assistance_safety_policy(request.user),
            status=status.HTTP_200_OK,
        )


class LinkPreviewView(APIView):
    """
    GET /api/v1/link-preview/?url=<url>
    Fetches OG metadata for a URL and returns title, description, image, site_name.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        url = request.query_params.get("url", "").strip()
        if not url:
            return Response({"detail": "url param is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            import requests as _requests
            from html.parser import HTMLParser

            class _OGParser(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.og = {}

                def handle_starttag(self, tag, attrs):
                    if tag == "meta":
                        attr_dict = dict(attrs)
                        prop = attr_dict.get("property") or attr_dict.get("name") or ""
                        content = attr_dict.get("content") or ""
                        if prop.startswith("og:") and content:
                            self.og[prop[3:]] = content

            resp = _requests.get(url, timeout=5, headers={"User-Agent": "KISBot/1.0"})
            resp.raise_for_status()
            parser = _OGParser()
            parser.feed(resp.text[:50000])  # cap parse size
            og = parser.og
            return Response({
                "title": og.get("title", ""),
                "description": og.get("description", ""),
                "image": og.get("image", ""),
                "site_name": og.get("site_name", ""),
            }, status=status.HTTP_200_OK)
        except Exception:
            return Response({
                "title": "",
                "description": "",
                "image": "",
                "site_name": "",
            }, status=status.HTTP_200_OK)


class TranslateView(APIView):
    """
    POST /api/v1/translate/
    Body: { text: str, target_lang: str }
    Returns: { translated: str }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        import os

        text = request.data.get("text", "")
        target_lang = request.data.get("target_lang", "en")

        if not text:
            return Response({"translated": "", "note": "No text provided."}, status=status.HTTP_200_OK)

        api_key = os.environ.get("GOOGLE_TRANSLATE_API_KEY", "")
        if not api_key:
            return Response({"translated": text, "note": "Translation not configured"}, status=status.HTTP_200_OK)

        try:
            import requests as _requests
            resp = _requests.post(
                "https://translation.googleapis.com/language/translate/v2",
                params={"key": api_key},
                json={"q": text, "target": target_lang, "format": "text"},
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            translated = data["data"]["translations"][0]["translatedText"]
            return Response({"translated": translated}, status=status.HTTP_200_OK)
        except Exception:
            return Response({"translated": text, "note": "Translation failed"}, status=status.HTTP_200_OK)
