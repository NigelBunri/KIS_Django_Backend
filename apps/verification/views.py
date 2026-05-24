from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.jwt_auth import DeviceBoundJWTAuthentication

from apps.accounts.feature_gate import require_feature

from .constants import VerificationSubjectType
from .models import VerificationAuditEvent, VerificationBadge, VerificationCase
from .providers import provider_public_status, verify_webhook_signature
from .serializers import (
    StaffBadgeIssueSerializer,
    StaffBadgeRevokeSerializer,
    StaffCaseStatusUpdateSerializer,
    StaffVerificationAuditEventSerializer,
    StaffVerificationBadgeSerializer,
    StaffVerificationCaseSerializer,
    UserVerificationEvidenceSerializer,
    UserVerificationReviewSerializer,
    UserVerificationStartSerializer,
)
from .services import (
    apply_provider_webhook_event,
    current_user_verification_status,
    expire_overdue_verification_badges,
    expiring_verification_items,
    filter_staff_verification_cases,
    provider_callback_inspection,
    public_trust_summary,
    record_audit_event,
    review_user_case,
    serialize_case_status,
    staff_issue_badge,
    staff_revoke_badge,
    staff_set_case_status,
    start_user_verification_case,
    submit_case_evidence,
    suspicious_verification_signals,
    unified_identity_trust_overview,
)


def _bounded_limit(request, default=50, maximum=250):
    try:
        value = int(request.query_params.get("limit", default))
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def _request_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


class UserVerificationStatusView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        return Response(current_user_verification_status(request.user), status=status.HTTP_200_OK)


class UnifiedTrustOverviewView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        include_staff = bool(getattr(request.user, "is_staff", False))
        return Response(unified_identity_trust_overview(request.user, include_staff=include_staff), status=status.HTTP_200_OK)


class PublicTrustSummaryView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, subject_type, subject_id):
        include_staff = bool(getattr(request.user, "is_staff", False) and _request_bool(request.query_params.get("staff"), default=False))
        return Response(public_trust_summary(subject_type, subject_id, include_staff=include_staff), status=status.HTTP_200_OK)


class UserVerificationStartView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request):
        require_feature(
            request.user,
            "verification_badge",
            "Verification badges are available on Business Pro plans and above. Upgrade to apply.",
        )
        serializer = UserVerificationStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        case = start_user_verification_case(
            user=request.user,
            level=serializer.validated_data.get("level") or "identity_verified",
            provider=serializer.validated_data.get("provider") or "",
            evidence_metadata=serializer.validated_data.get("evidence_metadata") or {},
        )
        record_audit_event(
            subject=case.subject,
            case=case,
            actor=request.user,
            action="user_case.api_started",
            provider=case.provider,
            request=request,
        )
        return Response(
            {
                "case": serialize_case_status(case),
                "provider": provider_public_status(case.provider, subject_type=VerificationSubjectType.USER),
                "status": current_user_verification_status(request.user),
            },
            status=status.HTTP_201_CREATED,
        )


class UserVerificationEvidenceView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, case_id):
        case = get_object_or_404(
            VerificationCase.objects.select_related("subject"),
            id=case_id,
            subject__subject_type=VerificationSubjectType.USER,
        )
        if not request.user.is_staff and case.subject.owner_id != request.user.id:
            return Response({"detail": "You do not have access to this verification case."}, status=status.HTTP_404_NOT_FOUND)
        serializer = UserVerificationEvidenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        case = submit_case_evidence(
            case=case,
            actor=request.user,
            evidence_metadata=serializer.validated_data["evidence_metadata"],
        )
        record_audit_event(
            subject=case.subject,
            case=case,
            actor=request.user,
            action="user_case.api_evidence_submitted",
            request=request,
        )
        return Response({"case": serialize_case_status(case)}, status=status.HTTP_200_OK)


class StaffUserVerificationReviewView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (permissions.IsAdminUser,)

    def post(self, request, case_id):
        case = get_object_or_404(
            VerificationCase.objects.select_related("subject"),
            id=case_id,
            subject__subject_type=VerificationSubjectType.USER,
        )
        serializer = UserVerificationReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        case, badges = review_user_case(
            case=case,
            actor=request.user,
            action=serializer.validated_data["action"],
            notes=serializer.validated_data.get("notes", ""),
            badge_codes=serializer.validated_data.get("badge_codes") or None,
        )
        record_audit_event(
            subject=case.subject,
            case=case,
            actor=request.user,
            action="user_case.api_reviewed",
            request=request,
            metadata={"action": serializer.validated_data["action"], "badge_codes": [badge.code for badge in badges]},
        )
        return Response(
            {
                "case": serialize_case_status(case),
                "badges": [{"code": badge.code, "label": badge.label, "level": badge.level} for badge in badges],
            },
            status=status.HTTP_200_OK,
        )


class StaffVerificationQueueView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (permissions.IsAdminUser,)

    def get(self, request):
        limit = _bounded_limit(request)
        cases = filter_staff_verification_cases(request.query_params)[:limit]
        return Response(
            {
                "count": len(cases),
                "results": StaffVerificationCaseSerializer(cases, many=True).data,
            },
            status=status.HTTP_200_OK,
        )


class StaffVerificationCaseDetailView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (permissions.IsAdminUser,)

    def get(self, request, case_id):
        case = get_object_or_404(
            VerificationCase.objects.select_related(
                "subject",
                "subject__owner",
                "requested_by",
                "reviewed_by",
            ).prefetch_related("badges"),
            id=case_id,
        )
        return Response(StaffVerificationCaseSerializer(case).data, status=status.HTTP_200_OK)

    def patch(self, request, case_id):
        case = get_object_or_404(VerificationCase.objects.select_related("subject"), id=case_id)
        serializer = StaffCaseStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        case = staff_set_case_status(
            case=case,
            actor=request.user,
            status_value=serializer.validated_data["status"],
            notes=serializer.validated_data.get("notes", ""),
            request=request,
        )
        return Response(StaffVerificationCaseSerializer(case).data, status=status.HTTP_200_OK)


class StaffVerificationBadgeIssueView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (permissions.IsAdminUser,)

    def post(self, request):
        serializer = StaffBadgeIssueSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        badge = staff_issue_badge(
            actor=request.user,
            subject_type=serializer.validated_data["subject_type"],
            subject_id=serializer.validated_data.get("subject_id"),
            case_id=serializer.validated_data.get("case_id"),
            code=serializer.validated_data["code"],
            label=serializer.validated_data.get("label", ""),
            level=serializer.validated_data.get("level", ""),
            public=serializer.validated_data.get("public", True),
            expires_at=serializer.validated_data.get("expires_at"),
            reason=serializer.validated_data.get("reason", ""),
            request=request,
        )
        return Response(StaffVerificationBadgeSerializer(badge).data, status=status.HTTP_201_CREATED)


class StaffVerificationBadgeRevokeView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (permissions.IsAdminUser,)

    def post(self, request, badge_id):
        badge = get_object_or_404(VerificationBadge.objects.select_related("subject", "case"), id=badge_id)
        serializer = StaffBadgeRevokeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        badge = staff_revoke_badge(
            badge=badge,
            actor=request.user,
            reason=serializer.validated_data.get("reason", ""),
            request=request,
        )
        return Response(StaffVerificationBadgeSerializer(badge).data, status=status.HTTP_200_OK)


class StaffVerificationAuditEventsView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (permissions.IsAdminUser,)

    def get(self, request):
        limit = _bounded_limit(request)
        qs = VerificationAuditEvent.objects.select_related("subject", "actor", "case").order_by("-created_at")
        action = request.query_params.get("action")
        provider = request.query_params.get("provider")
        subject_type = request.query_params.get("subject_type")
        if action:
            qs = qs.filter(action=action)
        if provider:
            qs = qs.filter(provider=provider[:32])
        if subject_type:
            qs = qs.filter(subject__subject_type=subject_type)
        rows = list(qs[:limit])
        return Response(
            {
                "count": len(rows),
                "results": StaffVerificationAuditEventSerializer(rows, many=True).data,
            },
            status=status.HTTP_200_OK,
        )


class StaffVerificationProviderCallbacksView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (permissions.IsAdminUser,)

    def get(self, request):
        provider = request.query_params.get("provider", "")
        rows = provider_callback_inspection(provider=provider, limit=_bounded_limit(request))
        return Response(
            {
                "provider": provider,
                "results": StaffVerificationAuditEventSerializer(rows, many=True).data,
            },
            status=status.HTTP_200_OK,
        )


class StaffVerificationSuspiciousSignalsView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (permissions.IsAdminUser,)

    def get(self, request):
        return Response(suspicious_verification_signals(), status=status.HTTP_200_OK)


class StaffVerificationExpiryRemindersView(APIView):
    authentication_classes = (DeviceBoundJWTAuthentication,)
    permission_classes = (permissions.IsAdminUser,)

    def get(self, request):
        try:
            days = int(request.query_params.get("days", 30))
        except (TypeError, ValueError):
            days = 30
        cases, badges = expiring_verification_items(days=days)
        return Response(
            {
                "days": max(1, min(days, 365)),
                "cases": StaffVerificationCaseSerializer(cases[:100], many=True).data,
                "badges": StaffVerificationBadgeSerializer(badges[:100], many=True).data,
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        dry_run = _request_bool(request.data.get("dry_run"), default=True)
        result = expire_overdue_verification_badges(
            actor=request.user,
            request=request,
            dry_run=bool(dry_run),
        )
        return Response(result, status=status.HTTP_200_OK)


class VerificationWebhookView(APIView):
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()

    def post(self, request, provider):
        signature = request.headers.get("X-Verification-Signature") or request.headers.get("X-Signature")
        ok, reason = verify_webhook_signature(body=request.body or b"", signature=signature)
        record_audit_event(
            action="webhook.received" if ok else "webhook.rejected",
            provider=(provider or "")[:32],
            request=request,
            metadata={"reason": reason, "provider": provider},
        )
        if not ok:
            return Response({"detail": "Invalid webhook signature.", "reason": reason}, status=status.HTTP_401_UNAUTHORIZED)
        payload = request.data if isinstance(request.data, dict) else {}
        result = apply_provider_webhook_event(provider=(provider or "")[:32], payload=payload, request=request)
        return Response({"provider": provider, **result}, status=status.HTTP_202_ACCEPTED)
