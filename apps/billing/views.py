from __future__ import annotations

import logging
import uuid
import os
from datetime import timedelta
from typing import Any
import requests
from django.conf import settings
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from django.core.exceptions import ValidationError
from django_filters.rest_framework import DjangoFilterBackend
from apps.core.money import parse_frontend_money_to_cents

from apps.accounts.models import User, AccountTier, Subscription, AuditLog
from apps.accounts.tiers import tier_rank as _tier_rank
from apps.partners.services import ensure_partner_profiles_for_user
from apps.core.phone_utils import to_e164
from apps.core.models import HealthcareOrganization, MedicalProfile
from .models import (
    BillingReconciliation,
    CreditAccount,
    DirectPaymentAuditEvent,
    DirectPaymentIntent,
    InsuranceClaim,
    PaymentDispute,
    PromoCode,
    PromoRedemption,
    RevenueLaunchEvidenceAuditEvent,
    RevenueLaunchEvidenceRecord,
    WalletAccount,
    WalletLedgerEntry,
    WalletTransaction,
)
from .serializers import (
    BillingReconciliationSerializer,
    CreditAccountSerializer,
    DirectPaymentAuditEventSerializer,
    DirectPaymentIntentSerializer,
    InsuranceClaimSerializer,
    PaymentDisputeSerializer,
    WalletAccountSerializer,
    WalletLedgerEntrySerializer,
    WalletTransactionSerializer,
    PromoCodeSerializer,
    PricingTierSerializer,
    RevenueLaunchEvidenceRecordSerializer,
)
from .direct_payments import (
    create_direct_payment_intent,
    reconcile_direct_payment_callback,
    reconcile_stripe_direct_payment_event,
    verify_flutterwave_transaction,
)
from .profitability_entitlements import get_profitability_entitlement_catalog
from .profitability_analytics import get_profitability_command_center_summary
from .profitability_beta_launch import get_profitability_beta_launch_plan
from .profitability_beta_operations import get_profitability_beta_operations_summary
from .profitability_evidence_workflow import get_revenue_evidence_workflow_plan
from .profitability_launch_gate import get_profitability_launch_gate_summary
from .profitability_production_go_no_go import get_profitability_production_go_no_go_summary
from .profitability_revenue_ops import get_revenue_ops_evidence_console_summary
from .profitability_revenue_readiness import (
    get_revenue_launch_readiness_summary,
    user_can_review_revenue_evidence,
)
from .profitability_staging_proof import get_staging_monetization_proof_workflows
from .profitability_subscription_lifecycle import get_profitability_subscription_lifecycle_summary
from apps.accounts.serializers import SubscriptionSerializer
from .documents import build_invoice_urls, build_receipt_urls
from .services import (
    get_wallet_account,
    get_credit_account,
    record_ledger,
    convert_cash_to_credits,
    convert_credits_to_cash,
    transfer_balance,
    upgrade_with_credits,
    apply_tier_upgrade,
    finalize_expired_subscription,
    finalize_stale_pending_wallet_transaction,
    reverse_tier_upgrade_payment,
    cents_to_credits,
    cents_to_usd,
    cents_to_usd_compact,
    credits_to_cents,
    adjust_points,
    redeem_promo_code,
    PromoCodeNotFound,
    PromoCodeNotActive,
    PromoCodeExpired,
    PromoCodeUsageLimitReached,
    PromoCodeAlreadyRedeemed,
    PromoCodeLegacyCashBonusBlocked,
)
from apps.rewards.services import (
    calculate_redemption,
    reserve_redemption,
    confirm_redemption,
    release_redemption,
    InsufficientRewardBalance,
)

logger = logging.getLogger(__name__)

FLW_BASE_URL = "https://api.flutterwave.com/v3"
def _flutterwave_headers() -> dict:
    secret = settings.FLW_SECRET_KEY
    return {
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
    }


def _flutterwave_payment_link(payload: dict) -> dict:
    url = f"{FLW_BASE_URL}/payments"
    response = requests.post(url, json=payload, headers=_flutterwave_headers(), timeout=30)
    data = response.json() if response.content else {}
    if response.status_code >= 300:
        raise ValueError(data.get("message") or "Failed to create payment")
    return data


def _ensure_payments_ready() -> None:
    if not getattr(settings, "FLW_SECRET_KEY", None):
        raise ValueError("FLW_SECRET_KEY is not configured")


def _parse_int_field(value, field_name: str) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid {field_name}")


_parse_frontend_money_to_cents = parse_frontend_money_to_cents


def _legacy_financial_flow_disabled(message: str) -> Response:
    return Response(
        {
            "detail": message,
            "code": "legacy_financial_flow_disabled",
        },
        status=status.HTTP_403_FORBIDDEN,
    )


def _normalized_phone_variants(phone: str, country_hint: str | None = None) -> list[str]:
    raw = str(phone or "").strip()
    if not raw:
        return []

    region = str(country_hint or "CM").strip().upper() or "CM"
    digits = "".join(ch for ch in raw if ch.isdigit())
    variants: list[str] = []
    seen = set()

    def add(value: str):
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        variants.append(normalized)

    add(raw)
    if digits:
        add(digits)
        add(f"+{digits}")
        if digits.startswith("00") and len(digits) > 2:
            add(digits[2:])
            add(f"+{digits[2:]}")
        if digits.startswith("0") and len(digits) > 1:
            add(digits[1:])
            add(f"+{digits[1:]}")

    for candidate in (raw, digits):
        if not candidate:
            continue
        try:
            e164 = to_e164(candidate, region)
        except Exception:
            continue
        add(e164)
        if e164.startswith("+"):
            add(e164[1:])

    return variants


def _digits_only(value: str | None) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _resolve_user_by_digits_fallback(*, phone: str, exclude_user: User | None = None) -> User | None:
    input_digits = _digits_only(phone)
    if not input_digits:
        return None

    qs = User.objects.exclude(phone__isnull=True).exclude(phone="")
    if exclude_user:
        qs = qs.exclude(id=exclude_user.id)

    matches: list[User] = []
    for user in qs.only("id", "phone").order_by("id"):
        user_digits = _digits_only(getattr(user, "phone", ""))
        if not user_digits:
            continue
        if user_digits == input_digits or user_digits.endswith(input_digits) or input_digits.endswith(user_digits):
            matches.append(user)
            if len(matches) > 1:
                # Ambiguous fallback match, caller should require stricter input.
                return None

    return matches[0] if matches else None


def _resolve_user_by_phone(
    *,
    phone: str,
    country_hint: str | None = None,
    exclude_user: User | None = None,
) -> User | None:
    candidates = _normalized_phone_variants(phone, country_hint)
    if not candidates:
        return None

    digit_candidates = [_digits_only(value) for value in candidates]
    digit_candidates = [value for value in digit_candidates if value]
    base_qs = User.objects.filter(
        Q(phone__in=candidates) |
        Q(phone_number__in=digit_candidates)
    )
    preferred_qs = base_qs.exclude(id=exclude_user.id) if exclude_user else base_qs

    for candidate in candidates:
        user = preferred_qs.filter(phone=candidate).order_by("id").first()
        if user:
            return user

    for candidate in candidates:
        user = base_qs.filter(phone=candidate).order_by("id").first()
        if user:
            return user

    return _resolve_user_by_digits_fallback(phone=phone, exclude_user=exclude_user)


class IsFinanceAdmin(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if getattr(request.user, "is_superuser", False) or getattr(request.user, "is_staff", False):
            return True
        org_id = self._extract_organization_id(request, view)
        if not org_id:
            return False
        return self._check_organization_ownership(request.user, org_id)

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        if getattr(request.user, "is_superuser", False) or getattr(request.user, "is_staff", False):
            return True
        organization = getattr(obj, "organization", None)
        if isinstance(organization, HealthcareOrganization):
            if organization.owner_id == request.user.id:
                return True
            return self._user_is_profile_creator_for_org(request.user, organization.id)
        if organization:
            return self._check_organization_ownership(request.user, organization)
        return False


    def _extract_organization_id(self, request, view):
        org_id = request.query_params.get("organization")
        if org_id:
            return org_id
        data = getattr(request, "data", {}) or {}
        if isinstance(data, dict):
            org_id = data.get("organization") or data.get("organization_id")
            if org_id:
                return org_id
        kwargs = getattr(view, "kwargs", {})
        org_id = kwargs.get("organization") or kwargs.get("organization_id")
        if org_id:
            return org_id
        resolved = self._resolve_org_from_user_profiles(request.user)
        if resolved:
            return resolved
        owned = HealthcareOrganization.objects.filter(owner_id=request.user.id)
        return str(owned.values_list("id", flat=True).first()) if owned.exists() else None

    def _check_organization_ownership(self, user, org_id):
        qs = HealthcareOrganization.objects.filter(owner_id=user.id)
        try:
            if qs.filter(id=org_id).exists():
                return True
        except (ValueError, ValidationError):
            pass
        if qs.filter(slug=org_id).exists():
            return True
        if MedicalProfile.objects.filter(created_by=user).exists():
            return True
        if self._user_owns_institution(user, org_id):
            return True
        resolved_org_id = self._resolve_org_from_profile_slug(user, org_id)
        if resolved_org_id:
            if qs.filter(id=resolved_org_id).exists():
                return True
            return self._user_is_profile_creator_for_org(user, resolved_org_id)
        return False

    def _user_is_profile_creator_for_org(self, user, org_id):
        return MedicalProfile.objects.filter(created_by=user, organization_id=org_id).exists()

    def _resolve_org_from_user_profiles(self, user):
        profile = MedicalProfile.objects.filter(created_by=user).values_list("organization_id", flat=True).first()
        return str(profile) if profile else None

    def _resolve_org_from_profile_slug(self, user, identifier):
        try:
            profile = MedicalProfile.objects.filter(Q(id=identifier) | Q(slug=identifier)).first()
        except (ValueError, ValidationError):
            return None
        if profile and profile.organization_id:
            if profile.created_by_id == user.id:
                return str(profile.organization_id)
            if profile.organization and profile.organization.owner_id == user.id:
                return str(profile.organization_id)
        return None

    def _user_owns_institution(self, user, identifier):
        if not identifier:
            return False
        try:
            return MedicalProfile.objects.filter(created_by=user).filter(
                Q(id=identifier)
                | Q(slug=identifier)
                | Q(organization__slug=identifier)
                | Q(organization_id=identifier)
            ).exists()
        except (ValueError, ValidationError):
            return False


class ProfitabilityEntitlementCatalogView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(get_profitability_entitlement_catalog(user=request.user))


class ProfitabilityCommandCenterView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(get_profitability_command_center_summary(user=request.user))


class ProfitabilityLaunchGateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(get_profitability_launch_gate_summary(user=request.user))


class ProfitabilitySubscriptionLifecycleView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(get_profitability_subscription_lifecycle_summary(user=request.user))


class ProfitabilityRevenueOpsEvidenceView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response(get_revenue_ops_evidence_console_summary(user=request.user))


class ProfitabilityEvidenceWorkflowPlanView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response(get_revenue_evidence_workflow_plan(user=request.user))


class ProfitabilityRevenueReadinessView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response(get_revenue_launch_readiness_summary(user=request.user))


class ProfitabilityStagingProofWorkflowView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response(get_staging_monetization_proof_workflows(user=request.user))


class ProfitabilityProductionGoNoGoView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response(get_profitability_production_go_no_go_summary(user=request.user))


class ProfitabilityBetaLaunchPlanView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response(get_profitability_beta_launch_plan(user=request.user))


class ProfitabilityBetaOperationsView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response(get_profitability_beta_operations_summary(user=request.user))


class RevenueLaunchEvidenceRecordViewSet(viewsets.ModelViewSet):
    serializer_class = RevenueLaunchEvidenceRecordSerializer
    permission_classes = [IsAdminUser]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["area", "status", "owner_role"]
    ordering_fields = ["created_at", "updated_at", "expires_at"]
    ordering = ["-created_at"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return (
            RevenueLaunchEvidenceRecord.objects
            .filter(is_deleted=False)
            .select_related("created_by", "reviewer", "private_media_asset")
            .prefetch_related("audit_events__actor")
        )

    def perform_update(self, serializer):
        previous_media_asset_id = str(serializer.instance.private_media_asset_id or "")
        record = serializer.save()
        RevenueLaunchEvidenceAuditEvent.objects.create(
            evidence_record=record,
            event_type="evidence_record_updated",
            actor=self.request.user,
            redacted_detail={"area": record.area, "status": record.status},
        )
        next_media_asset_id = str(record.private_media_asset_id or "")
        if previous_media_asset_id != next_media_asset_id:
            RevenueLaunchEvidenceAuditEvent.objects.create(
                evidence_record=record,
                event_type="private_media_reference_added" if next_media_asset_id else "private_media_reference_removed",
                actor=self.request.user,
                redacted_detail={"private_media_asset_id": next_media_asset_id or previous_media_asset_id},
            )

    def _set_status(self, request, *, status_value: str, event_type: str):
        record = self.get_object()
        if status_value in {"approved", "rejected", "needs_changes", "revoked"} and not user_can_review_revenue_evidence(request.user, record.area):
            return Response(
                {
                    "detail": "Your staff account does not have the required revenue evidence reviewer role for this area.",
                    "code": "missing_revenue_reviewer_role",
                    "area": record.area,
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        record.status = status_value
        if status_value == "submitted":
            record.submitted_at = timezone.now()
        if status_value in {"approved", "rejected", "needs_changes", "revoked"}:
            record.reviewer = request.user
            record.reviewed_at = timezone.now()
        record.save(update_fields=["status", "submitted_at", "reviewer", "reviewed_at", "updated_at"])
        RevenueLaunchEvidenceAuditEvent.objects.create(
            evidence_record=record,
            event_type=event_type,
            actor=request.user,
            redacted_detail={"area": record.area, "status": record.status},
        )
        record = self.get_queryset().get(pk=record.pk)
        serializer = self.get_serializer(record)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="submit")
    def submit(self, request, pk=None):
        return self._set_status(request, status_value="submitted", event_type="evidence_record_submitted")

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        return self._set_status(request, status_value="approved", event_type="evidence_record_approved")

    @action(detail=True, methods=["post"], url_path="needs-changes")
    def needs_changes(self, request, pk=None):
        return self._set_status(request, status_value="needs_changes", event_type="evidence_record_reviewed")

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        return self._set_status(request, status_value="rejected", event_type="evidence_record_rejected")

    @action(detail=True, methods=["post"], url_path="revoke")
    def revoke(self, request, pk=None):
        return self._set_status(request, status_value="revoked", event_type="evidence_record_revoked")


def _current_subscription(user: User) -> Subscription | None:
    return Subscription.objects.filter(user=user, status=Subscription.STATUS_ACTIVE).select_related("tier").first()


def _calculate_proration(current: AccountTier, target: AccountTier, sub: Subscription) -> int:
    if not sub.ends_at or not sub.started_at:
        return 0
    total_seconds = max((sub.ends_at - sub.started_at).total_seconds(), 1)
    remaining_seconds = max((sub.ends_at - timezone.now()).total_seconds(), 0)
    if remaining_seconds <= 0:
        return 0
    diff = max(current.price_cents - target.price_cents, 0)
    return int(round(diff * (remaining_seconds / total_seconds)))


class WalletViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"], url_path="me")
    def me(self, request):
        wallet = get_wallet_account(request.user)
        credits = get_credit_account(request.user)
        credits_value_cents = credits_to_cents(credits.credits)
        features = [
            "instant_topup",
            "credits_conversion",
            "cash_conversion",
            "gifts_transfer",
            "promo_redemption",
            "auto_convert_rules",
            "spend_limits",
            "safety_lock",
            "tier_discounts",
            "receipt_history",
            "referral_rewards",
            "scheduled_topups",
        ]
        payload = {
            "wallet": WalletAccountSerializer(wallet).data,
            "credits": CreditAccountSerializer(credits).data,
            "credits_value_cents": credits_value_cents,
            "credits_value_usd": str(cents_to_usd(credits_value_cents)),
            "credits_value_usd_compact": cents_to_usd_compact(credits_value_cents),
            "features": features,
        }
        return Response(payload, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="ledger")
    def ledger(self, request):
        entries = WalletLedgerEntry.objects.filter(user=request.user, is_deleted=False).order_by("-created_at")[:200]
        serializer = WalletLedgerEntrySerializer(entries, many=True, context={"request": request})
        return Response({"results": serializer.data}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["delete"], url_path=r"ledger/(?P<entry_id>[^/.]+)")
    def ledger_delete(self, request, entry_id=None):
        entry = WalletLedgerEntry.objects.filter(
            user=request.user,
            id=entry_id,
            is_deleted=False,
        ).first()
        if not entry:
            return Response({"detail": "Ledger entry not found."}, status=status.HTTP_404_NOT_FOUND)
        entry.soft_delete()
        return Response({"detail": "Ledger entry deleted."}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="transactions")
    def transactions(self, request):
        entries = WalletTransaction.objects.filter(user=request.user, is_deleted=False).order_by("-created_at")[:100]
        entries = [finalize_stale_pending_wallet_transaction(tx) for tx in entries]
        serializer = WalletTransactionSerializer(entries, many=True, context={"request": request})
        return Response({"results": serializer.data}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["delete"], url_path=r"transactions/(?P<transaction_id>[^/.]+)")
    def transaction_delete(self, request, transaction_id=None):
        transaction_obj = WalletTransaction.objects.filter(
            user=request.user,
            id=transaction_id,
            is_deleted=False,
        ).first()
        if not transaction_obj:
            return Response({"detail": "Transaction not found."}, status=status.HTTP_404_NOT_FOUND)
        transaction_obj.soft_delete()
        return Response({"detail": "Transaction deleted."}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="subscription")
    def subscription(self, request):
        sub = _current_subscription(request.user)
        if not sub:
            return Response({"subscription": None}, status=status.HTTP_200_OK)
        sub = finalize_expired_subscription(sub)
        return Response(
            {"subscription": SubscriptionSerializer(sub).data},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="billing-history")
    def billing_history(self, request):
        ledger = WalletLedgerEntry.objects.filter(user=request.user, is_deleted=False).order_by("-created_at")[:50]
        ledger_data = WalletLedgerEntrySerializer(ledger, many=True, context={"request": request}).data
        transactions = WalletTransaction.objects.filter(user=request.user, is_deleted=False).order_by("-created_at")[:50]
        transactions = [finalize_stale_pending_wallet_transaction(tx) for tx in transactions]
        sub = _current_subscription(request.user)
        if sub:
            sub = finalize_expired_subscription(sub)
        transaction_data = WalletTransactionSerializer(transactions, many=True, context={"request": request}).data
        invoice_links = (None, None)
        if sub:
            invoice_links = build_invoice_urls(request, sub)
        return Response(
            {
                "ledger": ledger_data,
                "transactions": transaction_data,
                "subscription": SubscriptionSerializer(sub).data if sub else None,
                "invoice_url": invoice_links[0],
                "invoice_pdf_url": invoice_links[1],
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="subscription-cancel")
    def subscription_cancel(self, request):
        sub = _current_subscription(request.user)
        if not sub:
            return Response({"detail": "No active subscription."}, status=status.HTTP_400_BAD_REQUEST)
        immediate = bool(request.data.get("immediate"))
        if immediate:
            sub.status = Subscription.STATUS_CANCELLED
            sub.ends_at = timezone.now()
            sub.cancel_at_period_end = False
            sub.canceled_at = timezone.now()
            sub.pending_tier = None
            sub.save(update_fields=["status", "ends_at", "cancel_at_period_end", "canceled_at", "pending_tier", "updated_at"])
            free_tier = AccountTier.objects.filter(name__iexact="Free").first()
            if free_tier:
                request.user.tier = free_tier.name
                request.user.save(update_fields=["tier", "updated_at"])
                ensure_partner_profiles_for_user(request.user, free_tier.name)
        else:
            sub.cancel_at_period_end = True
            sub.canceled_at = timezone.now()
            if sub.ends_at:
                sub.grace_ends_at = sub.ends_at + timedelta(days=7)
            sub.save(update_fields=["cancel_at_period_end", "canceled_at", "grace_ends_at", "updated_at"])
        AuditLog.log(
            request.user,
            "billing.subscription.cancel",
            {
                "subscription_id": str(sub.id),
                "tier": sub.tier.name if sub.tier else None,
                "immediate": immediate,
            },
        )
        return Response({"subscription": SubscriptionSerializer(sub).data}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="subscription-resume")
    def subscription_resume(self, request):
        sub = _current_subscription(request.user)
        if not sub:
            return Response({"detail": "No active subscription."}, status=status.HTTP_400_BAD_REQUEST)
        sub.cancel_at_period_end = False
        sub.canceled_at = None
        sub.grace_ends_at = None
        sub.pending_tier = None
        sub.save(update_fields=["cancel_at_period_end", "canceled_at", "grace_ends_at", "pending_tier", "updated_at"])
        ensure_partner_profiles_for_user(request.user, sub.tier.name if sub.tier else None)
        AuditLog.log(
            request.user,
            "billing.subscription.resume",
            {
                "subscription_id": str(sub.id),
                "tier": sub.tier.name if sub.tier else None,
            },
        )
        return Response({"subscription": SubscriptionSerializer(sub).data}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="subscription-downgrade")
    def subscription_downgrade(self, request):
        sub = _current_subscription(request.user)
        if not sub:
            return Response({"detail": "No active subscription."}, status=status.HTTP_400_BAD_REQUEST)
        tier_id = request.data.get("tier")
        target = get_object_or_404(AccountTier, id=tier_id)
        if not sub.tier:
            return Response({"detail": "Current tier missing."}, status=status.HTTP_400_BAD_REQUEST)
        if _tier_rank(sub.tier.name) <= _tier_rank(target.name):
            return Response({"detail": "Downgrade requires a lower tier."}, status=status.HTTP_400_BAD_REQUEST)
        sub.pending_tier = target
        sub.cancel_at_period_end = True
        proration = _calculate_proration(sub.tier, target, sub)
        meta = sub.billing_meta or {}
        meta["downgrade_to"] = target.name
        meta["proration_credit_cents"] = proration
        sub.billing_meta = meta
        if sub.ends_at:
            sub.grace_ends_at = sub.ends_at + timedelta(days=7)
        sub.save(update_fields=["pending_tier", "cancel_at_period_end", "billing_meta", "grace_ends_at", "updated_at"])
        ensure_partner_profiles_for_user(request.user, target.name)
        AuditLog.log(
            request.user,
            "billing.subscription.downgrade",
            {
                "subscription_id": str(sub.id),
                "current_tier": sub.tier.name if sub.tier else None,
                "target_tier": target.name,
                "proration_credit_cents": proration,
            },
        )
        return Response(
            {
                "subscription": SubscriptionSerializer(sub).data,
                "proration_credit_cents": proration,
                "proration_credit_usd": str(cents_to_usd(proration)),
                "proration_credit_usd_compact": cents_to_usd_compact(proration),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="transaction-retry")
    def transaction_retry(self, request):
        tx_ref = request.data.get("tx_ref")
        transaction_id = request.data.get("transaction_id") or request.data.get("id")
        transaction_qs = WalletTransaction.objects.filter(user=request.user, is_deleted=False)
        if tx_ref:
            transaction_qs = transaction_qs.filter(tx_ref=tx_ref)
        elif transaction_id:
            transaction_qs = transaction_qs.filter(id=transaction_id)
        else:
            return Response({"detail": "transaction reference or id required."}, status=status.HTTP_400_BAD_REQUEST)
        transaction_obj = transaction_qs.first()
        if not transaction_obj:
            return Response({"detail": "Transaction not found."}, status=status.HTTP_404_NOT_FOUND)
        if transaction_obj.status == "success":
            return Response({"detail": "Transaction already completed."}, status=status.HTTP_400_BAD_REQUEST)
        intent = (transaction_obj.meta or {}).get("intent")
        if not intent:
            return Response({"detail": "Transaction cannot be retried."}, status=status.HTTP_400_BAD_REQUEST)

        new_tx_ref = f"kis_retry_{uuid.uuid4().hex}"
        retry_obj = WalletTransaction.objects.create(
            user=request.user,
            provider=transaction_obj.provider,
            method=transaction_obj.method,
            amount_cents=transaction_obj.amount_cents,
            currency=transaction_obj.currency,
            status="pending",
            tx_ref=new_tx_ref,
            meta={**(transaction_obj.meta or {}), "retry_of": transaction_obj.tx_ref},
        )
        AuditLog.log(
            request.user,
            "billing.transaction.retry",
            {"original_tx": tx_ref, "retry_tx": new_tx_ref},
        )
        try:
            _ensure_payments_ready()
            payload = {
                "tx_ref": new_tx_ref,
                "amount": retry_obj.amount_cents / 100,
                "currency": retry_obj.currency,
                "redirect_url": getattr(settings, "FLW_REDIRECT_URL", "https://kis.app/payments/complete"),
                "customer": {
                    "email": request.user.email or "user@kis.app",
                    "phonenumber": request.user.phone or "",
                    "name": request.user.display_name or "KIS User",
                },
                "customizations": {
                    "title": "KIS Payment Retry",
                    "description": "Retry payment",
                },
                "meta": retry_obj.meta,
            }
            response = _flutterwave_payment_link(payload)
            payment_url = response.get("data", {}).get("link")
            retry_obj.payment_url = payment_url or ""
            retry_obj.raw_payload = response
            retry_obj.save(update_fields=["payment_url", "raw_payload", "updated_at"])
            return Response(
                {"tx_ref": new_tx_ref, "status": "pending", "payment_url": payment_url},
                status=status.HTTP_200_OK,
            )
        except ValueError as exc:
            retry_obj.status = "failed"
            retry_obj.raw_payload = {"error": str(exc)}
            retry_obj.save(update_fields=["status", "raw_payload", "updated_at"])
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["get"], url_path="receipt")
    def receipt(self, request):
        tx_ref = request.query_params.get("tx_ref")
        if not tx_ref:
            return Response({"detail": "tx_ref required"}, status=status.HTTP_400_BAD_REQUEST)
        tx = WalletTransaction.objects.filter(user=request.user, tx_ref=tx_ref, is_deleted=False).first()
        if not tx:
            return Response({"detail": "Transaction not found"}, status=status.HTTP_404_NOT_FOUND)
        html_url, pdf_url = build_receipt_urls(request, tx)
        return Response({"receipt_url": html_url, "receipt_pdf_url": pdf_url}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="refund")
    def refund(self, request):
        tx_ref = request.data.get("tx_ref")
        reason = str(request.data.get("reason") or "Customer requested refund")[:255]
        if not tx_ref:
            return Response({"detail": "tx_ref required"}, status=status.HTTP_400_BAD_REQUEST)
        tx = WalletTransaction.objects.filter(user=request.user, tx_ref=tx_ref, is_deleted=False).first()
        if not tx:
            return Response({"detail": "Transaction not found"}, status=status.HTTP_404_NOT_FOUND)
        if tx.status != "success":
            return Response({"detail": "Only successful transactions can be refunded"}, status=status.HTTP_400_BAD_REQUEST)
        if not tx.provider_ref:
            return Response({"detail": "No provider reference — contact support"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            _ensure_payments_ready()
            resp = requests.post(
                f"{FLW_BASE_URL}/transactions/{tx.provider_ref}/refund",
                json={"amount": tx.amount_cents / 100, "comments": reason},
                headers=_flutterwave_headers(),
                timeout=30,
            )
            data = resp.json() if resp.content else {}
            if resp.status_code >= 300:
                raise ValueError(data.get("message") or "Refund failed")
            tx.status = "cancelled"
            tx.meta = {**tx.meta, "refund_reason": reason, "refund_response": data}
            tx.save(update_fields=["status", "meta", "updated_at"])

            reversal = None
            if (tx.meta or {}).get("intent") == "tier_upgrade":
                # Previously nothing happened here — a user could pay for a
                # tier, immediately self-refund via this endpoint, and keep
                # that tier active until its natural expiry.
                reversal = reverse_tier_upgrade_payment(
                    transaction_obj=tx, reason=reason, event_type="refund",
                )

            response_body = {"detail": "Refund initiated", "provider_response": data}
            if reversal is not None:
                response_body["subscription_reversal"] = reversal
            return Response(response_body)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response({"detail": "Refund service unavailable"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    @action(
        detail=False, methods=["post"], url_path="admin/reverse-payment",
        permission_classes=[IsAdminUser],
    )
    def admin_reverse_payment(self, request):
        """
        Staff-only equivalent of the self-service refund flow above, for
        processing a chargeback (or any other provider-side reversal)
        support learns about out-of-band. No Flutterwave chargeback webhook
        receiver exists in this codebase — there's no verified payload
        contract to build one against safely, so this is the documented
        interim path: for a chargeback the provider/bank has already
        reversed the payment, this only needs to sync KIS's own
        Subscription/ledger state, so unlike the self-service action it
        does not call any payment provider API.
        """
        tx_ref = request.data.get("tx_ref")
        reason = str(request.data.get("reason") or "Chargeback")[:255]
        event_type = str(request.data.get("event_type") or "chargeback")
        if event_type not in ("chargeback", "refund"):
            return Response(
                {"detail": "event_type must be 'chargeback' or 'refund'."}, status=status.HTTP_400_BAD_REQUEST,
            )
        if not tx_ref:
            return Response({"detail": "tx_ref required"}, status=status.HTTP_400_BAD_REQUEST)
        tx = WalletTransaction.objects.filter(tx_ref=tx_ref, is_deleted=False).first()
        if not tx:
            return Response({"detail": "Transaction not found"}, status=status.HTTP_404_NOT_FOUND)
        if tx.status != "success":
            return Response(
                {"detail": "Only successful transactions can be reversed."}, status=status.HTTP_400_BAD_REQUEST,
            )

        reversal = reverse_tier_upgrade_payment(transaction_obj=tx, reason=reason, event_type=event_type)
        AuditLog.log(
            request.user,
            "billing.admin.payment_reversed",
            {
                "transaction_id": str(tx.id),
                "tx_ref": tx.tx_ref,
                "target_user_id": str(tx.user_id),
                "event_type": event_type,
                "reason": reason,
            },
        )
        return Response({"detail": "Reversal processed", "reversal": reversal})

    @action(detail=False, methods=["get"], url_path="invoice")
    def invoice(self, request):
        sub_id = request.query_params.get("subscription_id")
        if not sub_id:
            return Response({"detail": "subscription_id required"}, status=status.HTTP_400_BAD_REQUEST)
        sub = Subscription.objects.filter(user=request.user, id=sub_id).select_related("tier").first()
        if not sub:
            return Response({"detail": "Subscription not found"}, status=status.HTTP_404_NOT_FOUND)
        html_url, pdf_url = build_invoice_urls(request, sub)
        return Response({"invoice_url": html_url, "invoice_pdf_url": pdf_url}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="deposit")
    def deposit(self, request):
        if not getattr(settings, "KIS_LEGACY_WALLET_DEPOSIT_ENABLED", False):
            return _legacy_financial_flow_disabled(
                "Wallet top-ups are disabled. KIS promotional credits cannot be bought."
            )
        try:
            amount = _parse_frontend_money_to_cents(request.data)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        provider = request.data.get("provider", "flutterwave")
        method = request.data.get("method")
        payment_meta: dict[str, Any] = {}
        mock = bool(request.data.get("mock")) or getattr(settings, "PAYMENTS_MOCK", False)
        force_direct = bool(request.data.get("direct") or request.data.get("virtual") or provider == "kis_wallet")
        if provider == "flutterwave" and not getattr(settings, "FLW_SECRET_KEY", None):
            logger = logging.getLogger(__name__)
            logger.warning("Flutterwave secret missing; falling back to direct deposit.")
            force_direct = True

        if amount <= 0:
            return Response({"detail": "Amount must be greater than 0"}, status=status.HTTP_400_BAD_REQUEST)

        tx_ref = f"kis_{uuid.uuid4().hex}"
        if not method:
            if provider == "mobilemoney_mtn":
                method = "mobilemoney"
                payment_meta["network"] = "MTN"
            elif provider == "mobilemoney_orange":
                method = "mobilemoney"
                payment_meta["network"] = "ORANGE"
            else:
                method = "card"

        transaction_obj = WalletTransaction.objects.create(
            user=request.user,
            provider=provider,
            method=method,
            amount_cents=amount,
            currency="USD",
            status="pending",
            tx_ref=tx_ref,
            meta={"intent": "wallet_topup", **payment_meta},
        )

        if force_direct:
            record_ledger(
                user=request.user,
                kind="deposit",
                amount_cents=amount,
                reference=tx_ref,
                meta={"provider": provider, "method": method, "direct": True},
            )
            transaction_obj.status = "success"
            transaction_obj.processed_at = timezone.now()
            transaction_obj.save(update_fields=["status", "processed_at", "updated_at"])
            return Response(
                {
                    "tx_ref": tx_ref,
                    "status": "success",
                    "payment_url": None,
                    "direct": True,
                },
                status=status.HTTP_200_OK,
            )

        if mock:
            record_ledger(
                user=request.user,
                kind="deposit",
                amount_cents=amount,
                reference=tx_ref,
                meta={"provider": "mock"},
            )
            transaction_obj.status = "success"
            transaction_obj.processed_at = timezone.now()
            transaction_obj.save(update_fields=["status", "processed_at", "updated_at"])
            return Response(
                {
                    "tx_ref": tx_ref,
                    "status": "success",
                    "payment_url": None,
                },
                status=status.HTTP_200_OK,
            )

        try:
            _ensure_payments_ready()
            payload = {
                "tx_ref": tx_ref,
                "amount": amount / 100,
                "currency": "USD",
                "redirect_url": getattr(settings, "FLW_REDIRECT_URL", "https://kis.app/payments/complete"),
                "customer": {
                    "email": request.user.email or "user@kis.app",
                    "phonenumber": request.user.phone or "",
                    "name": request.user.display_name or "KIS User",
                },
                "customizations": {
                    "title": "KIS Wallet Top Up",
                    "description": "Add funds to your KIS wallet",
                },
            }
            if method:
                payload["payment_options"] = method
            if payment_meta:
                payload["meta"] = payment_meta

            response = _flutterwave_payment_link(payload)
            payment_url = response.get("data", {}).get("link")
            transaction_obj.payment_url = payment_url or ""
            transaction_obj.raw_payload = response
            transaction_obj.save(update_fields=["payment_url", "raw_payload", "updated_at"])
            return Response(
                {
                    "tx_ref": tx_ref,
                    "status": "pending",
                    "payment_url": payment_url,
                },
                status=status.HTTP_200_OK,
            )
        except ValueError as exc:
            transaction_obj.status = "failed"
            transaction_obj.raw_payload = {"error": str(exc)}
            transaction_obj.save(update_fields=["status", "raw_payload", "updated_at"])
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["post"], url_path="convert")
    def convert(self, request):
        if not getattr(settings, "KIS_LEGACY_CASH_CREDIT_CONVERSION_ENABLED", False):
            return _legacy_financial_flow_disabled(
                "Cash/credit conversion is disabled. KIS promotional credits cannot be bought, sold, or converted to cash."
            )
        direction = request.data.get("direction")
        try:
            if direction == "cash_to_credits":
                amount_cents = _parse_frontend_money_to_cents(request.data)
                result = convert_cash_to_credits(request.user, amount_cents)
                return Response(
                    {
                        "direction": direction,
                        "amount_cents": result.amount_cents,
                        "amount_usd": str(cents_to_usd(result.amount_cents)),
                        "amount_usd_compact": cents_to_usd_compact(result.amount_cents),
                        "credits": result.credits,
                    },
                    status=status.HTTP_200_OK,
                )
            if direction == "credits_to_cash":
                credits = int(request.data.get("credits", 0))
                result = convert_credits_to_cash(request.user, credits)
                return Response(
                    {
                        "direction": direction,
                        "amount_cents": result.amount_cents,
                        "amount_usd": str(cents_to_usd(result.amount_cents)),
                        "amount_usd_compact": cents_to_usd_compact(result.amount_cents),
                        "credits": result.credits,
                    },
                    status=status.HTTP_200_OK,
                )
            return Response({"detail": "Invalid direction"}, status=status.HTTP_400_BAD_REQUEST)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["post"], url_path="transfer")
    def transfer(self, request):
        if not getattr(settings, "KIS_LEGACY_WALLET_TRANSFER_ENABLED", False):
            return _legacy_financial_flow_disabled(
                "Peer-to-peer wallet and promotional-credit transfers are disabled."
            )
        recipient_id = request.data.get("recipient_id")
        recipient_phone = request.data.get("recipient_phone")
        country_hint = request.data.get("country") or getattr(request.user, "country", None)

        try:
            amount_cents = _parse_frontend_money_to_cents(request.data)
            credits = _parse_int_field(request.data.get("credits"), "credits")
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if amount_cents > 0 and credits > 0:
            return Response(
                {"detail": "Provide either amount_cents or credits, not both."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if amount_cents <= 0 and credits <= 0:
            return Response(
                {"detail": "Amount or credits must be greater than 0."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        resolved_from_phone = None
        if recipient_phone:
            resolved_from_phone = _resolve_user_by_phone(
                phone=str(recipient_phone),
                country_hint=str(country_hint or ""),
                exclude_user=request.user,
            )
            if not resolved_from_phone:
                return Response({"detail": "Recipient phone is not registered."}, status=status.HTTP_400_BAD_REQUEST)

        recipient = None
        if recipient_id:
            recipient = User.objects.filter(id=recipient_id).first()
            if not recipient:
                return Response({"detail": "Invalid recipient_id"}, status=status.HTTP_400_BAD_REQUEST)

        if not recipient and not resolved_from_phone:
            return Response(
                {"detail": "recipient_id or recipient_phone is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if recipient and resolved_from_phone and recipient.id != resolved_from_phone.id:
            return Response(
                {"detail": "Recipient phone does not match recipient_id."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        recipient = recipient or resolved_from_phone
        if recipient and recipient.id == request.user.id:
            return Response(
                {"detail": "You cannot transfer to your own account."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            outbound, inbound = transfer_balance(
                sender=request.user,
                recipient=recipient,
                amount_cents=amount_cents,
                credits=credits,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
            "outbound": WalletLedgerEntrySerializer(outbound, context={"request": request}).data,
            "inbound": WalletLedgerEntrySerializer(inbound, context={"request": request}).data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="upgrade")
    def upgrade(self, request):
        tier_id = request.data.get("tier")
        tier = get_object_or_404(AccountTier, id=tier_id)
        payment_method = request.data.get("payment_method", "flutterwave")
        mock = bool(request.data.get("mock")) or getattr(settings, "PAYMENTS_MOCK", False)
        AuditLog.log(
            request.user,
            "billing.tier_upgrade.requested",
            {
                "tier_id": str(tier.id),
                "tier_name": tier.name,
                "payment_method": payment_method,
            },
        )
        current_sub = Subscription.objects.filter(
            user=request.user, status=Subscription.STATUS_ACTIVE,
        ).select_related("tier").first()
        current_tier = current_sub.tier if current_sub and current_sub.tier else AccountTier.objects.filter(
            name__iexact=request.user.tier
        ).first()
        if current_tier and _tier_rank(current_tier.name) >= _tier_rank(tier.name):
            return Response(
                {"detail": "Downgrades or same-tier upgrades are not supported yet."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if tier.price_cents <= 0:
            apply_tier_upgrade(user=request.user, tier=tier, source="free")
            return Response({"tier": tier.name, "status": "success"}, status=status.HTTP_200_OK)

        if payment_method == "credits":
            try:
                result = upgrade_with_credits(request.user, tier)
            except ValueError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            return Response(result, status=status.HTTP_200_OK)

        if payment_method in ("kisc", "wallet", "wallet_balance"):
            if not getattr(settings, "KIS_LEGACY_WALLET_UPGRADE_ENABLED", False):
                return _legacy_financial_flow_disabled(
                    "Wallet/KIS Coin upgrade payments are disabled. Use secure USD checkout; promotional credits may subsidize eligible upgrades."
                )
            tx_ref = f"kis_upgrade_{uuid.uuid4().hex}"
            wallet_account = get_wallet_account(request.user)
            if wallet_account.balance_cents < tier.price_cents:
                return Response(
                    {"detail": "Insufficient wallet balance for upgrade."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            transaction_obj = WalletTransaction.objects.create(
                user=request.user,
                provider="internal",
                method="wallet",
                amount_cents=tier.price_cents,
                currency="USD",
                status="pending",
                tx_ref=tx_ref,
                meta={"intent": "tier_upgrade", "tier_id": str(tier.id), "payment_method": payment_method},
            )
            apply_tier_upgrade(
                user=request.user,
                tier=tier,
                source="wallet",
                amount_cents=-tier.price_cents,
                reference=tx_ref,
                meta={"payment_method": payment_method},
            )
            transaction_obj.status = "success"
            transaction_obj.provider = "wallet"
            transaction_obj.payment_url = ""
            transaction_obj.processed_at = timezone.now()
            transaction_obj.save(update_fields=["status", "payment_url", "updated_at", "processed_at"])
            return Response(
                {"tier": tier.name, "status": "success", "payment_method": payment_method},
                status=status.HTTP_200_OK,
            )

        # KIS Coins may only subsidize a real external-provider charge — not
        # a payment already made from an internal balance (credits/wallet),
        # which is handled by the branches above and returns before this
        # point. apply_rewards is an intent flag only; the actual discount
        # is always computed server-side by calculate_redemption, never
        # trusted from the client.
        apply_rewards = bool(request.data.get("apply_rewards"))
        redemption_quote = None
        if apply_rewards:
            redemption_quote = calculate_redemption(request.user, tier.price_cents)

        payable_cents = redemption_quote.payable_cents if redemption_quote else tier.price_cents

        tx_ref = f"kis_upgrade_{uuid.uuid4().hex}"
        tx_meta = {"intent": "tier_upgrade", "tier_id": str(tier.id), "tier_name": tier.name}
        if redemption_quote:
            tx_meta.update({
                "gross_amount_cents": tier.price_cents,
                "coins_applied": redemption_quote.coins_to_spend,
                "discount_cents": redemption_quote.discount_cents,
            })
        transaction_obj = WalletTransaction.objects.create(
            user=request.user,
            provider="flutterwave",
            method="card",
            amount_cents=payable_cents,
            currency="USD",
            status="pending",
            tx_ref=tx_ref,
            meta=tx_meta,
        )

        reservation = None
        if redemption_quote and redemption_quote.coins_to_spend > 0:
            try:
                reservation = reserve_redemption(
                    request.user,
                    redemption_quote.coins_to_spend,
                    reference_type="wallet_transaction",
                    reference_id=transaction_obj.id,
                    idempotency_key=f"redemption:{tx_ref}",
                    description=f"Discount for {tier.name} upgrade",
                    metadata={"tx_ref": tx_ref, "tier_id": str(tier.id)},
                )
            except InsufficientRewardBalance:
                # Lost a race against a concurrent spend between the quote
                # (above) and this reservation — nothing was charged yet,
                # safe to discard and ask the client to retry.
                transaction_obj.delete()
                return Response(
                    {"detail": "Insufficient KIS Coins balance for this discount. Please retry."},
                    status=status.HTTP_409_CONFLICT,
                )
            if reservation:
                transaction_obj.meta = {**transaction_obj.meta, "redemption_entry_id": str(reservation.id)}
                transaction_obj.save(update_fields=["meta", "updated_at"])

        if mock:
            transaction_obj.status = "success"
            transaction_obj.processed_at = timezone.now()
            transaction_obj.save(update_fields=["status", "processed_at", "updated_at"])
            apply_tier_upgrade(
                user=request.user,
                tier=tier,
                source="mock",
                amount_cents=payable_cents,
                reference=tx_ref,
            )
            if reservation:
                confirm_redemption(reservation)
            # Referral qualification deliberately does NOT fire for "mock" —
            # only genuine external-provider success (QUALIFYING_PAYMENT_SOURCES)
            # represents real revenue to reward a referral against.
            return Response(
                {
                    "tx_ref": tx_ref, "status": "success", "payment_url": None,
                    "payable_cents": payable_cents,
                    "coins_applied": redemption_quote.coins_to_spend if redemption_quote else 0,
                    "discount_cents": redemption_quote.discount_cents if redemption_quote else 0,
                },
                status=status.HTTP_200_OK,
            )

        try:
            _ensure_payments_ready()
            payload = {
                "tx_ref": tx_ref,
                "amount": payable_cents / 100,
                "currency": "USD",
                "redirect_url": getattr(settings, "FLW_REDIRECT_URL", "https://kis.app/payments/complete"),
                "customer": {
                    "email": request.user.email or "user@kis.app",
                    "phonenumber": request.user.phone or "",
                    "name": request.user.display_name or "KIS User",
                },
                "customizations": {
                    "title": "KIS Account Upgrade",
                    "description": f"Upgrade to {tier.name}",
                },
                "meta": {"intent": "tier_upgrade", "tier_id": str(tier.id)},
            }
            response = _flutterwave_payment_link(payload)
            payment_url = response.get("data", {}).get("link")
            transaction_obj.payment_url = payment_url or ""
            transaction_obj.raw_payload = response
            transaction_obj.save(update_fields=["payment_url", "raw_payload", "updated_at"])
            return Response(
                {
                    "tx_ref": tx_ref, "status": "pending", "payment_url": payment_url,
                    "payable_cents": payable_cents,
                    "coins_applied": redemption_quote.coins_to_spend if redemption_quote else 0,
                    "discount_cents": redemption_quote.discount_cents if redemption_quote else 0,
                },
                status=status.HTTP_200_OK,
            )
        except ValueError as exc:
            transaction_obj.status = "failed"
            transaction_obj.raw_payload = {"error": str(exc)}
            transaction_obj.save(update_fields=["status", "raw_payload", "updated_at"])
            if reservation:
                release_redemption(reservation, reason="payment_link_creation_failed")
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["post"], url_path="redeem")
    def redeem(self, request):
        code = (request.data.get("code") or "").strip().upper()
        if not code:
            return Response({"detail": "Promo code required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = redeem_promo_code(request.user, code)
        except PromoCodeLegacyCashBonusBlocked as exc:
            return Response(
                {"detail": str(exc), "code": "legacy_financial_flow_disabled"},
                status=status.HTTP_403_FORBIDDEN,
            )
        except (PromoCodeNotFound, PromoCodeNotActive) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except (PromoCodeExpired, PromoCodeUsageLimitReached, PromoCodeAlreadyRedeemed) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "code": result.promo.code,
                "cash_bonus_cents": result.cash_bonus_cents,
                "cash_bonus_usd": str(cents_to_usd(result.cash_bonus_cents)),
                "cash_bonus_usd_compact": cents_to_usd_compact(result.cash_bonus_cents),
                "credit_bonus": result.credit_bonus,
                "legacy_cash_bonus_blocked": result.legacy_cash_bonus_blocked,
            }
        )


class BillingReconciliationViewSet(viewsets.ModelViewSet):
    queryset = BillingReconciliation.objects.select_related("organization", "transaction").all()
    serializer_class = BillingReconciliationSerializer
    permission_classes = [IsFinanceAdmin]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["status", "insurance_provider", "organization"]
    ordering_fields = ["created_at", "amount_cents"]

    @action(detail=True, methods=["post"])
    def reconcile(self, request, pk=None):
        reconciliation = self.get_object()
        reconciliation.status = BillingReconciliation.STATUS_RECONCILED
        reconciliation.reconciled_at = timezone.now()
        reconciliation.save(update_fields=["status", "reconciled_at", "updated_at"])
        AuditLog.log(
            request.user,
            "billing.reconciliation.reconciled",
            {"reconciliation_id": str(reconciliation.id)},
        )
        return Response(self.get_serializer(reconciliation).data)


class InsuranceClaimViewSet(viewsets.ModelViewSet):
    queryset = InsuranceClaim.objects.select_related("organization", "patient").all()
    serializer_class = InsuranceClaimSerializer
    permission_classes = [IsFinanceAdmin]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["status", "insurance_provider", "organization"]
    ordering_fields = ["submitted_at", "amount_cents"]

    @action(detail=True, methods=["post"])
    def update_status(self, request, pk=None):
        claim = self.get_object()
        next_status = request.data.get("status")
        notes = request.data.get("notes")
        valid_statuses = dict(InsuranceClaim.STATUS_CHOICES)
        if next_status not in valid_statuses:
            return Response({"detail": "Invalid status"}, status=status.HTTP_400_BAD_REQUEST)
        claim.status = next_status
        if notes:
            claim.notes = notes
        if next_status in (
            InsuranceClaim.STATUS_APPROVED,
            InsuranceClaim.STATUS_DENIED,
            InsuranceClaim.STATUS_PAID,
        ):
            claim.resolved_at = timezone.now()
        claim.save(
            update_fields=["status", "notes", "resolved_at", "updated_at"]
            if next_status in (
                InsuranceClaim.STATUS_APPROVED,
                InsuranceClaim.STATUS_DENIED,
                InsuranceClaim.STATUS_PAID,
            )
            else ["status", "notes", "updated_at"]
        )
        AuditLog.log(
            request.user,
            "billing.claim.update_status",
            {"claim_id": str(claim.id), "status": next_status},
        )
        return Response(self.get_serializer(claim).data)


class PaymentDisputeViewSet(viewsets.ModelViewSet):
    queryset = PaymentDispute.objects.select_related("wallet_transaction", "claim", "reported_by").all()
    serializer_class = PaymentDisputeSerializer
    permission_classes = [IsFinanceAdmin]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["status"]
    ordering_fields = ["created_at"]

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        dispute = self.get_object()
        resolution = request.data.get("resolution")
        dispute.mark_resolved(resolution)
        AuditLog.log(
            request.user,
            "billing.dispute.resolve",
            {"dispute_id": str(dispute.id), "resolution": resolution},
        )
        return Response(self.get_serializer(dispute).data)


class PricingInsightsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tiers = AccountTier.objects.order_by("price_cents")
        tier_data = PricingTierSerializer(tiers, many=True).data
        current_sub = _current_subscription(request.user)
        wallet = get_wallet_account(request.user)
        claims = InsuranceClaim.objects.values("status").annotate(total=Count("id"))
        disputes = PaymentDispute.objects.values("status").annotate(total=Count("id"))
        reconciliations = BillingReconciliation.objects.values("status").annotate(total=Count("id"))

        def summary(qs):
            return {item["status"]: item["total"] for item in qs}

        payload = {
            "tiers": tier_data,
            "current_subscription": SubscriptionSerializer(current_sub).data if current_sub else None,
            "wallet_balance_cents": wallet.balance_cents,
            "wallet_balance_usd": str(cents_to_usd(int(wallet.balance_cents or 0))),
            "wallet_balance_usd_compact": cents_to_usd_compact(int(wallet.balance_cents or 0)),
            "claims_summary": summary(claims),
            "dispute_summary": summary(disputes),
            "reconciliation_summary": summary(reconciliations),
        }
        return Response(payload, status=status.HTTP_200_OK)


def reconcile_wallet_flutterwave_event(*, payload: dict) -> Response:
    """Processes a Flutterwave event (webhook delivery OR an independently
    server-verified transaction — see PaymentStatusView) against
    WalletTransaction-based flows (tier upgrades, tips, channel membership,
    generic deposits). DirectPaymentIntent-based flows (commerce orders,
    service/education bookings, health billing) are handled separately by
    reconcile_direct_payment_callback — see the tx_ref-existence branch in
    both FlutterwaveWebhookView.post and PaymentStatusView.get.

    Extracted verbatim from FlutterwaveWebhookView.post (previously the
    entire body of that view) so the exact same idempotent, row-locked
    reconciliation logic runs whether the trigger is Flutterwave's webhook
    or PaymentStatusView's fallback verify-with-Flutterwave-API path — one
    implementation, not two, per this codebase's established convention."""
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    tx_ref = data.get("tx_ref")
    status_flag = (data.get("status") or "").lower()

    if not tx_ref:
        return Response({"detail": "tx_ref missing"}, status=status.HTTP_400_BAD_REQUEST)

    if DirectPaymentIntent.objects.filter(tx_ref=tx_ref).exists():
        # signature=FLW_WEBHOOK_SECRET: this function's own callers have
        # already established trust by this point — either a real webhook
        # signature check (FlutterwaveWebhookView) or an independent
        # server-to-server verify call against Flutterwave's own API
        # (PaymentStatusView) — so passing the correct secret here just
        # satisfies reconcile_direct_payment_callback's own internal check
        # rather than re-deriving trust a second time.
        ok, result, _intent = reconcile_direct_payment_callback(
            payload=payload, signature=getattr(settings, "FLW_WEBHOOK_SECRET", ""),
        )
        if not ok and result == "invalid_signature":
            return Response({"detail": "invalid signature"}, status=status.HTTP_403_FORBIDDEN)
        if not ok:
            return Response({"detail": result}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"status": "ok", "result": result})

    transaction_obj = WalletTransaction.objects.filter(tx_ref=tx_ref).first()
    if not transaction_obj:
        return Response({"detail": "unknown transaction"}, status=status.HTTP_404_NOT_FOUND)

    transaction_obj.raw_payload = payload
    if status_flag == "successful":
        # Re-fetch under a row lock and re-check the status guard inside
        # it — the previous plain read-then-write here had a real race
        # window: two genuinely concurrent redeliveries of the same
        # webhook event could both read status != "success" before
        # either commits, and both call apply_tier_upgrade for the same
        # payment. select_for_update() serializes them so the second
        # one to arrive sees the first one's committed "success" status
        # and correctly no-ops.
        with transaction.atomic():
            transaction_obj = WalletTransaction.objects.select_for_update().get(id=transaction_obj.id)
            already_processed = transaction_obj.status == "success"
            transaction_obj.raw_payload = payload
            if not already_processed:
                transaction_obj.status = "success"
                transaction_obj.provider_ref = data.get("id", "")
                transaction_obj.processed_at = timezone.now()
            transaction_obj.save(
                update_fields=["status", "provider_ref", "processed_at", "raw_payload", "updated_at"],
            )
            if already_processed:
                return Response({"status": "ok", "note": "already processed"})

        intent = (transaction_obj.meta or {}).get("intent")
        if intent == "tier_upgrade":
            tier_id = (transaction_obj.meta or {}).get("tier_id")
            tier = AccountTier.objects.filter(id=tier_id).first()
            if tier:
                new_subscription = apply_tier_upgrade(
                    user=transaction_obj.user,
                    tier=tier,
                    source="flutterwave",
                    amount_cents=transaction_obj.amount_cents,
                    reference=transaction_obj.tx_ref,
                    meta={"provider": "flutterwave"},
                )

                redemption_entry_id = (transaction_obj.meta or {}).get("redemption_entry_id")
                if redemption_entry_id:
                    from apps.rewards.models import RewardLedgerEntry as _RewardLedgerEntry
                    reservation = _RewardLedgerEntry.objects.filter(id=redemption_entry_id).first()
                    if reservation:
                        confirm_redemption(reservation)

                # "flutterwave" is a genuine QUALIFYING_PAYMENT_SOURCES
                # value — this is real external revenue, the only kind
                # that should ever mature a referral reward. Deferred
                # import: apps.referrals.services already imports from
                # apps.billing.services (adjust_points), so a top-level
                # import here would be circular — matches this
                # codebase's established convention for exactly this
                # situation (e.g. apps.otp.views importing
                # apps.accounts.views locally).
                from apps.referrals.services import qualify_referral
                qualify_referral(
                    transaction_obj.user,
                    new_subscription,
                    net_amount_cents=transaction_obj.amount_cents,
                )
        else:
            record_ledger(
                user=transaction_obj.user,
                kind="deposit",
                amount_cents=transaction_obj.amount_cents,
                reference=transaction_obj.tx_ref,
                meta={"provider": "flutterwave"},
            )
        # Activate channel membership if payment metadata indicates it
        _meta = transaction_obj.meta or {}
        if _meta.get("target_type") == "channel_membership":
            _mem_id = _meta.get("target_id")
            _user_id = _meta.get("user_id")
            if _mem_id and _user_id:
                try:
                    from apps.broadcasts.models import ChannelMembership
                    ChannelMembership.objects.filter(
                        id=_mem_id, user_id=_user_id, status="pending_payment"
                    ).update(status=ChannelMembership.Status.ACTIVE, payment_reference=str(tx_ref or ""))
                except Exception as _exc:
                    logger.warning("[FLW webhook] membership activation failed: %s", _exc)
        # Send payment receipt email
        try:
            user_id = getattr(transaction_obj.user, "id", None) if transaction_obj.user else None
            amount = transaction_obj.amount_cents
            currency = str(data.get("currency") or "USD")
            from django.contrib.auth import get_user_model as _get_user_model
            _User = _get_user_model()
            _user_obj = _User.objects.filter(id=str(user_id or "")).first() if user_id else None
            if _user_obj and getattr(_user_obj, "email", None):
                from apps.notifications.email_service import send_payment_receipt_email
                if not send_payment_receipt_email(
                    to_email=_user_obj.email,
                    amount=str(amount or ""),
                    currency=str(currency or "USD"),
                    tx_ref=str(tx_ref or ""),
                ):
                    logger.warning("[FLW webhook] payment receipt email failed for tx_ref=%s", tx_ref)
                    AuditLog.log(actor=_user_obj, action="email.payment_receipt.failed", meta={"tx_ref": str(tx_ref or "")})
        except Exception as _exc:
            logger.warning("[FLW webhook] payment receipt email raised: %s", _exc.__class__.__name__)
    elif status_flag in ("failed", "cancelled"):
        # Phase 6: this branch previously had none of the successful
        # branch's protections — no row lock, no re-check against a
        # status already committed by a concurrent/earlier delivery.
        # Two concrete bugs that produced: (1) a late/out-of-order
        # "failed" event arriving after a genuine "successful" one had
        # already completed would still run this whole branch and
        # incorrectly release_redemption() an already-REDEEMED
        # reservation, clawing back coins from a completed upgrade —
        # payment providers do not guarantee webhook delivery order;
        # (2) two concurrent duplicate "failed" deliveries for the same
        # tx_ref could both reach release_redemption() on the same
        # PENDING entry, and the loser raised an unhandled ValueError
        # instead of no-opping (also hardened at the source in
        # apps.rewards.services.reverse_ledger_entry). Locking and
        # re-checking status here mirrors the successful branch exactly.
        with transaction.atomic():
            transaction_obj = WalletTransaction.objects.select_for_update().get(id=transaction_obj.id)
            transaction_obj.raw_payload = payload
            if transaction_obj.status != "pending":
                # Already "success" (a late failure event must never
                # undo a completed payment) or already "failed"/
                # "cancelled" (duplicate delivery) — nothing to do.
                transaction_obj.save(update_fields=["raw_payload", "updated_at"])
                return Response({"status": "ok", "note": "already processed"})

            transaction_obj.status = "failed" if status_flag == "failed" else "cancelled"
            meta = transaction_obj.meta or {}
            if meta.get("intent") == "tier_upgrade":
                retry_count = int(meta.get("retry_count", 0)) + 1
                meta["retry_count"] = retry_count
                if retry_count <= 3:
                    meta["next_retry_at"] = (timezone.now() + timedelta(days=retry_count)).isoformat()
                # Give any reserved coins back — the discount they were
                # locked for never materialized. release_redemption is
                # idempotent (see apps.rewards.services.reverse_ledger_entry);
                # a genuinely abandoned checkout that never gets a
                # failed/cancelled webhook at all (the user just closes
                # the browser tab) is NOT covered here — that's the
                # scheduled cleanup job's job (Phase 11), not this
                # handler's.
                redemption_entry_id = meta.get("redemption_entry_id")
                if redemption_entry_id:
                    from apps.rewards.models import RewardLedgerEntry as _RewardLedgerEntry
                    reservation = _RewardLedgerEntry.objects.filter(id=redemption_entry_id).first()
                    if reservation:
                        release_redemption(reservation, reason=f"payment_{status_flag}")
            transaction_obj.meta = meta
            transaction_obj.save(update_fields=["status", "raw_payload", "meta", "updated_at"])
    return Response({"status": "ok"})


@method_decorator(csrf_exempt, name="dispatch")
class FlutterwaveWebhookView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, *args, **kwargs):
        secret = getattr(settings, "FLW_WEBHOOK_SECRET", "")
        signature = request.headers.get("verif-hash")
        if not secret or signature != secret:
            return Response({"detail": "invalid signature"}, status=status.HTTP_403_FORBIDDEN)

        payload = request.data if isinstance(request.data, dict) else {}
        return reconcile_wallet_flutterwave_event(payload=payload)


_WALLET_TX_STATUS_MAP = {"success": "paid", "failed": "failed", "cancelled": "cancelled", "pending": "pending"}
_DIRECT_INTENT_STATUS_MAP = {
    DirectPaymentIntent.STATUS_PAID: "paid",
    DirectPaymentIntent.STATUS_FAILED: "failed",
    DirectPaymentIntent.STATUS_CANCELLED: "cancelled",
    DirectPaymentIntent.STATUS_PENDING: "pending",
}


def _describe_payment_status(*, intent: DirectPaymentIntent | None, wallet_tx: WalletTransaction | None) -> dict:
    if intent:
        return {
            "found": True,
            "status": _DIRECT_INTENT_STATUS_MAP.get(intent.status, "pending"),
            "kind": intent.target_type,
        }
    meta = wallet_tx.meta or {}
    kind = str(meta.get("intent") or meta.get("target_type") or "wallet_payment")
    result = {
        "found": True,
        "status": _WALLET_TX_STATUS_MAP.get(wallet_tx.status, "pending"),
        "kind": kind,
    }
    if kind == "tier_upgrade":
        result["tier_name"] = str(meta.get("tier_name") or "")
    return result


@method_decorator(csrf_exempt, name="dispatch")
class PaymentStatusView(APIView):
    """GET /api/v1/billing/payments/status/?tx_ref=...&transaction_id=...

    Public (unauthenticated) endpoint for the payments/complete redirect
    landing page (see FLW_REDIRECT_URL / kingdomimpactventures.org — a
    plain marketing website, not the authenticated mobile app) to confirm
    what actually happened to a checkout. Deliberately never derives the
    verdict from the status/tx_ref/transaction_id query params the
    Flutterwave redirect itself supplies — those are client-controlled URL
    parameters a user could edit by hand, and trusting "status=successful"
    from a redirect URL would let anyone grant themselves a free tier
    upgrade. The verdict always comes from either our own DB (already
    reconciled by a real webhook delivery) or a fresh, secret-key-
    authenticated call to Flutterwave's own verify endpoint — see
    apps.billing.direct_payments.verify_flutterwave_transaction.

    This also self-heals the case that motivated it: a webhook that never
    arrived (misconfigured endpoint, dropped delivery, provider outage)
    left a real, successful payment stuck showing "pending" with the
    user's tier never upgraded. Landing on this page (or its own retry)
    now completes the same reconciliation the webhook would have.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "payment_status"

    def get(self, request):
        tx_ref = str(request.query_params.get("tx_ref") or "").strip()
        transaction_id = str(request.query_params.get("transaction_id") or "").strip()
        if not tx_ref:
            return Response({"found": False, "status": "unknown", "detail": "tx_ref is required."}, status=status.HTTP_400_BAD_REQUEST)

        intent = DirectPaymentIntent.objects.filter(tx_ref=tx_ref).first()
        wallet_tx = None if intent else WalletTransaction.objects.filter(tx_ref=tx_ref).first()
        if not intent and not wallet_tx:
            return Response({"found": False, "status": "unknown"}, status=status.HTTP_404_NOT_FOUND)

        result = _describe_payment_status(intent=intent, wallet_tx=wallet_tx)
        if result["status"] != "pending" or not transaction_id:
            return Response(result, status=status.HTTP_200_OK)

        # Still pending in our own DB — the webhook may simply not have
        # landed yet. Verify directly with Flutterwave (server-to-server,
        # our own secret key) rather than telling the page to just keep
        # polling forever.
        try:
            verified = verify_flutterwave_transaction(transaction_id)
        except Exception as exc:
            logger.warning("PaymentStatusView: verify_flutterwave_transaction failed for tx_ref=%s: %s", tx_ref, exc)
            return Response(result, status=status.HTTP_200_OK)

        if str(verified.get("tx_ref") or "") != tx_ref:
            # The supplied transaction_id doesn't actually belong to this
            # tx_ref — never let a mismatched id reconcile the wrong
            # record. Report the pre-verification status, unchanged.
            logger.warning(
                "PaymentStatusView: transaction_id/tx_ref mismatch — expected tx_ref=%s got=%s",
                tx_ref, verified.get("tx_ref"),
            )
            return Response(result, status=status.HTTP_200_OK)

        verified_status = str(verified.get("status") or "").strip().lower()
        if verified_status in {"successful", "success", "failed", "cancelled"}:
            reconcile_wallet_flutterwave_event(payload={"data": verified})
            if intent:
                intent.refresh_from_db()
            else:
                wallet_tx.refresh_from_db()
            result = _describe_payment_status(intent=intent, wallet_tx=wallet_tx)

        return Response(result, status=status.HTTP_200_OK)


class DirectPaymentIntentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        target_type = str(request.data.get("target_type") or request.data.get("targetType") or "").strip()
        target_id = request.data.get("target_id") or request.data.get("targetId")
        provider = str(request.data.get("provider") or "flutterwave").strip().lower()
        idempotency_key = str(
            request.headers.get("Idempotency-Key")
            or request.data.get("idempotency_key")
            or request.data.get("idempotencyKey")
            or ""
        ).strip()
        metadata = request.data.get("metadata") if isinstance(request.data.get("metadata"), dict) else {}
        if not target_type or not target_id:
            return Response({"detail": "target_type and target_id are required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            intent = create_direct_payment_intent(
                user=request.user,
                target_type=target_type,
                target_id=target_id,
                provider=provider,
                idempotency_key=idempotency_key,
                metadata=metadata,
            )
        except PermissionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        data = DirectPaymentIntentSerializer(intent).data
        return Response(
            {
                "intent": data,
                "direct_payment_intent_id": data.get("direct_payment_intent_id") or data.get("id"),
                "payment_reference": data.get("payment_reference") or data.get("tx_ref"),
                "payment_url": data.get("payment_url") or "",
                "payment_status": data.get("payment_status") or data.get("status"),
                "payment_provider": data.get("payment_provider") or data.get("provider"),
            },
            status=status.HTTP_201_CREATED,
        )


@method_decorator(csrf_exempt, name="dispatch")
class DirectPaymentFlutterwaveWebhookView(APIView):
    """
    Flutterwave only supports one configured webhook URL per account, but
    this codebase has two payment systems (DirectPaymentIntent for
    commerce/booking/health payments, WalletTransaction for tier upgrades/
    tips/deposits) historically split across two separate webhook views. If
    the dashboard is (or ever becomes) pointed at THIS url while a real
    WalletTransaction-based event arrives, reconcile_direct_payment_callback
    correctly finds no matching DirectPaymentIntent and used to just drop
    the event with a 404 -- silently leaving a genuinely successful payment
    unreconciled and the user's account never upgraded. Falls back to the
    same unified handling FlutterwaveWebhookView uses so a correctly-signed
    event is never dropped based on which of the two URLs it happened to
    land on.
    """
    authentication_classes = []
    permission_classes = []

    def post(self, request, *args, **kwargs):
        signature = request.headers.get("verif-hash") or ""
        payload = request.data if isinstance(request.data, dict) else {}
        ok, result, intent = reconcile_direct_payment_callback(payload=payload, signature=signature)
        if not ok and result == "invalid_signature":
            return Response({"detail": "invalid signature"}, status=status.HTTP_403_FORBIDDEN)
        if not ok and result == "missing_tx_ref":
            return Response({"detail": "tx_ref missing"}, status=status.HTTP_400_BAD_REQUEST)
        if not ok and result == "unmatched":
            # Signature already verified inside reconcile_direct_payment_callback
            # before it returns "unmatched" -- safe to reuse the same payload.
            return reconcile_wallet_flutterwave_event(payload=payload)
        return Response({"status": "ok", "result": result, "intent_id": str(intent.id) if intent else None})


class DirectPaymentAuditEventViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAdminUser]
    serializer_class = DirectPaymentAuditEventSerializer
    queryset = DirectPaymentAuditEvent.objects.select_related("intent", "actor").order_by("-created_at")
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["event", "provider", "target_type", "status"]
    ordering_fields = ["created_at"]


class WalletAdminViewSet(viewsets.ViewSet):
    permission_classes = [IsAdminUser]

    @action(detail=False, methods=["post"], url_path="adjust")
    def adjust(self, request):
        user_id = request.data.get("user_id")
        cash_cents = int(request.data.get("cash_cents", 0))
        credits = int(request.data.get("credits", 0))
        points = int(request.data.get("points", 0))
        reason = request.data.get("reason", "admin_adjust")

        if not user_id:
            return Response({"detail": "user_id required"}, status=status.HTTP_400_BAD_REQUEST)
        user = get_object_or_404(User, id=user_id)

        if cash_cents or credits:
            record_ledger(
                user=user,
                kind="admin_adjust",
                amount_cents=cash_cents,
                credits_delta=credits,
                reference=f"admin:{request.user.id}",
                meta={"reason": reason},
            )
        if points:
            adjust_points(user, points, reason)
        return Response({"detail": "adjusted"}, status=status.HTTP_200_OK)


class PromoCodeViewSet(viewsets.ModelViewSet):
    queryset = PromoCode.objects.all()
    serializer_class = PromoCodeSerializer
    permission_classes = [IsAdminUser]

    def get_permissions(self):
        if self.action in ("validate", "redeem_code", "public"):
            return [IsAuthenticated()]
        return super().get_permissions()

    @action(detail=False, methods=["get"], url_path="public")
    def public(self, request):
        active = PromoCode.objects.filter(is_active=True).order_by("-created_at")[:50]
        return Response({"results": PromoCodeSerializer(active, many=True).data}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="validate")
    def validate(self, request):
        """Public-facing endpoint for authenticated users to look up a promo code by code value."""
        code_value = request.query_params.get("code", "").strip().upper()
        if not code_value:
            return Response({"detail": "Provide a code query parameter."}, status=status.HTTP_400_BAD_REQUEST)
        from django.utils import timezone as tz
        try:
            promo = PromoCode.objects.get(code=code_value, is_active=True)
        except PromoCode.DoesNotExist:
            return Response({"detail": "Code not found or already used."}, status=status.HTTP_404_NOT_FOUND)
        now = tz.now()
        if promo.starts_at and promo.starts_at > now:
            return Response({"detail": "Code is not yet active."}, status=status.HTTP_400_BAD_REQUEST)
        if promo.ends_at and promo.ends_at < now:
            return Response({"detail": "Code has expired."}, status=status.HTTP_400_BAD_REQUEST)
        if promo.usage_limit is not None and promo.used_count >= promo.usage_limit:
            return Response({"detail": "Code has reached its usage limit."}, status=status.HTTP_400_BAD_REQUEST)
        already_redeemed = PromoRedemption.objects.filter(user=request.user, promo=promo).exists()
        return Response({
            "id": str(promo.id),
            "code": promo.code,
            "description": promo.description,
            "cash_bonus_cents": promo.cash_bonus_cents,
            "credit_bonus": promo.credit_bonus,
            "ends_at": promo.ends_at,
            "is_active": promo.is_active,
            "already_redeemed": already_redeemed,
            "status": "redeemed" if already_redeemed else "active",
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="redeem-code")
    def redeem_code(self, request):
        """Authenticated users redeem a promo code to claim its bonus."""
        code_value = str(request.data.get("code", "")).strip().upper()
        if not code_value:
            return Response({"detail": "Provide a code."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = redeem_promo_code(request.user, code_value)
        except (PromoCodeNotFound, PromoCodeNotActive) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except PromoCodeAlreadyRedeemed as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except PromoCodeLegacyCashBonusBlocked as exc:
            return Response(
                {"detail": str(exc), "code": "legacy_financial_flow_disabled"},
                status=status.HTTP_403_FORBIDDEN,
            )
        except (PromoCodeExpired, PromoCodeUsageLimitReached) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "detail": "Code redeemed successfully.",
            "code": result.promo.code,
            "credit_bonus": result.credit_bonus,
            "cash_bonus_cents": result.cash_bonus_cents,
            "legacy_cash_bonus_blocked": result.legacy_cash_bonus_blocked,
        }, status=status.HTTP_200_OK)


def _sync_stripe_connect_account_status(account_obj: dict) -> None:
    """account.updated is the sync TRIGGER for Stripe Connect status, but
    not the source of truth for its fields — it's the legacy v1 webhook
    shape (flat charges_enabled/payouts_enabled/details_submitted
    booleans), and accounts here are created via the v2 Accounts API
    with a "recipient" configuration (see apps.billing.stripe_connect),
    whose real capability status lives in a differently-shaped
    configuration.recipient.capabilities tree. Whether Stripe backfills
    those legacy v1 fields meaningfully for a v2-native recipient account
    isn't something to rely on, so this only takes the account id off the
    webhook payload and re-fetches authoritative status via
    refresh_account_status (the same v2 GET call the manual "Refresh
    status" UI action uses) rather than trusting the payload's own
    fields — matching how verify_flutterwave_transaction is the only
    trusted source for Flutterwave, never a callback's own claim.
    account_obj.id (`acct_...`) is looked up across all four
    payout-holder tables since the webhook itself carries no
    target_type/target_id metadata (Stripe doesn't attach ours to
    Account objects) — each table's stripe_account_id is unique enough
    in practice that at most one row across all four will ever match a
    given account id."""
    account_id = str(account_obj.get("id") or "")
    if not account_id:
        return

    from apps.broadcasts.models import BroadcastChannel, EducationInstitution
    from apps.commerce.models import Shop
    from apps.health_ops.models import HealthInstitution

    entity = None
    model_name = ""
    for model in (Shop, EducationInstitution, HealthInstitution, BroadcastChannel):
        entity = model.objects.filter(stripe_account_id=account_id).first()
        if entity is not None:
            model_name = model.__name__
            break
    if entity is None:
        return

    from apps.billing.stripe_connect import refresh_account_status

    try:
        fields = refresh_account_status(account_id)
    except Exception:
        logger.exception("[Stripe] account.updated: status refresh failed for account_id=%s", account_id)
        return

    for field_name, value in fields.items():
        setattr(entity, field_name, value)
    entity.save(update_fields=[*fields.keys(), "updated_at"])
    logger.info(
        "[Stripe] account.updated synced %s id=%s charges_enabled=%s payouts_enabled=%s",
        model_name, entity.id, fields["stripe_charges_enabled"], fields["stripe_payouts_enabled"],
    )


class StripeWebhookView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        from apps.billing.stripe_payments import verify_webhook
        sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
        event = verify_webhook(request.body, sig_header)
        if event is None:
            return Response({"error": "Invalid signature"}, status=400)

        event_type = event.get("type", "")
        data_obj = (event.get("data") or {}).get("object") or {}
        metadata = data_obj.get("metadata") if isinstance(data_obj.get("metadata"), dict) else {}

        if event_type in {"checkout.session.completed", "payment_intent.succeeded"} and (
            metadata.get("intent_id") or metadata.get("tx_ref")
        ):
            ok, result, intent = reconcile_stripe_direct_payment_event(event)
            if not ok and result == "unmatched":
                return Response({"detail": "unknown direct payment intent"}, status=status.HTTP_404_NOT_FOUND)
            if not ok:
                return Response({"detail": result}, status=status.HTTP_400_BAD_REQUEST)
            return Response({"received": True, "result": result, "intent_id": str(intent.id) if intent else None})

        if event_type == "payment_intent.succeeded":
            intent_id = data_obj.get("id") or ""
            target_type = metadata.get("target_type", "")
            target_id = metadata.get("target_id", "")
            user_id = metadata.get("user_id", "")
            amount = int(data_obj.get("amount") or 0)
            currency = str(data_obj.get("currency") or "usd").upper()
            logger.info(
                "[Stripe] payment_intent.succeeded intent=%s target=%s/%s user=%s amount=%s %s",
                intent_id, target_type, target_id, user_id, amount, currency,
            )
            # Activate channel membership if this was a membership payment
            if target_type == "channel_membership" and target_id and user_id:
                try:
                    from apps.broadcasts.models import ChannelMembership
                    ChannelMembership.objects.filter(
                        id=target_id,
                        user_id=user_id,
                        status="pending_payment",
                    ).update(
                        status=ChannelMembership.Status.ACTIVE,
                        payment_reference=intent_id,
                    )
                    # Send confirmation email
                    from django.contrib.auth import get_user_model
                    _User = get_user_model()
                    user_obj = _User.objects.filter(id=user_id).first()
                    if user_obj and getattr(user_obj, "email", None):
                        try:
                            membership = ChannelMembership.objects.select_related("tier__channel").filter(id=target_id).first()
                            if membership:
                                from apps.notifications.email_service import send_membership_email
                                if not send_membership_email(
                                    to_email=user_obj.email,
                                    tier_title=membership.tier.title,
                                    channel_name=membership.tier.channel.name,
                                ):
                                    logger.warning("[Stripe] membership email failed for user_id=%s", user_id)
                                    AuditLog.log(actor=user_obj, action="email.membership.failed", meta={"membership_id": str(target_id)})
                        except Exception as _email_exc:
                            logger.warning("[Stripe] membership email raised: %s", _email_exc.__class__.__name__)
                            AuditLog.log(
                                actor=user_obj, action="email.membership.failed",
                                meta={"membership_id": str(target_id), "error": _email_exc.__class__.__name__},
                            )
                except Exception as exc:
                    logger.warning("[Stripe] membership activation failed: %s", exc)
            # Send payment receipt email
            try:
                from django.contrib.auth import get_user_model
                _User = get_user_model()
                user_obj = _User.objects.filter(id=user_id).first()
                if user_obj and getattr(user_obj, "email", None):
                    from apps.notifications.email_service import send_payment_receipt_email
                    if not send_payment_receipt_email(
                        to_email=user_obj.email,
                        amount=f"{amount / 100:.2f}",
                        currency=currency,
                        tx_ref=intent_id,
                    ):
                        logger.warning("[Stripe] payment receipt email failed for tx_ref=%s", intent_id)
                        AuditLog.log(actor=user_obj, action="email.payment_receipt.failed", meta={"tx_ref": str(intent_id or "")})
            except Exception as _exc:
                logger.warning("[Stripe] payment receipt email raised: %s", _exc.__class__.__name__)

        elif event_type == "checkout.session.completed":
            session_id = data_obj.get("id") or ""
            logger.info("[Stripe] checkout.session.completed session=%s meta=%s", session_id, metadata)

        elif event_type == "account.updated":
            _sync_stripe_connect_account_status(data_obj)

        return Response({"received": True})
