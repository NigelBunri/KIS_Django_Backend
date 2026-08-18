from __future__ import annotations

import re

from rest_framework import serializers

from .documents import build_receipt_urls
from .models import (
    BillingReconciliation,
    CreditAccount,
    InsuranceClaim,
    DirectPaymentAuditEvent,
    DirectPaymentIntent,
    PaymentDispute,
    PromoCode,
    RevenueLaunchEvidenceAuditEvent,
    RevenueLaunchEvidenceRecord,
    WalletAccount,
    WalletLedgerEntry,
    WalletTransaction,
)
from .services import cents_to_usd, cents_to_usd_compact
from .promotional_credits import (
    PROMOTIONAL_CREDIT_POLICY,
    promotional_credit_label_from_cents,
    credit_delta_label,
)
from apps.core.models import HealthcareOrganization, PatientMasterRecord
from apps.accounts.models import AccountTier, User
from apps.media.models import MediaAsset
from .profitability_revenue_readiness import evidence_record_is_expired, REVIEWER_ROLE_BY_AREA


REVENUE_EVIDENCE_BLOCKED_KEYS = {
    "raw_provider_payload",
    "provider_payload",
    "raw_callback",
    "payment_card_data",
    "card_number",
    "bank_account_data",
    "private_health_record",
    "verification_document_bytes",
    "document_bytes",
    "raw_storage_path",
    "secret",
    "secret_key",
    "api_key",
    "token",
}


class WalletAccountSerializer(serializers.ModelSerializer):
    balance_usd = serializers.SerializerMethodField()
    balance_usd_compact = serializers.SerializerMethodField()
    balance_micro = serializers.SerializerMethodField()
    balance_kisc_label = serializers.SerializerMethodField()
    balance_usd_label = serializers.SerializerMethodField()
    promotional_credit_label = serializers.SerializerMethodField()
    promotional_credit_policy = serializers.SerializerMethodField()
    can_buy_promotional_credits = serializers.SerializerMethodField()
    can_transfer_promotional_credits = serializers.SerializerMethodField()
    can_convert_promotional_credits_to_cash = serializers.SerializerMethodField()

    class Meta:
        model = WalletAccount
        fields = [
            "id",
            "balance_cents",
            "balance_micro",
            "balance_usd",
            "balance_usd_compact",
            "balance_kisc_label",
            "balance_usd_label",
            "promotional_credit_label",
            "promotional_credit_policy",
            "can_buy_promotional_credits",
            "can_transfer_promotional_credits",
            "can_convert_promotional_credits_to_cash",
            "currency",
            "status",
            "metadata",
            "created_at",
        ]

    def get_balance_usd(self, obj):
        return str(cents_to_usd(int(obj.balance_cents or 0)))

    def get_balance_usd_compact(self, obj):
        return cents_to_usd_compact(int(obj.balance_cents or 0))

    def get_balance_micro(self, obj):
        return int((obj.balance_cents or 0) * 10)

    def get_balance_kisc_label(self, obj):
        # Backward-compatible field name; value is intentionally reframed.
        return promotional_credit_label_from_cents(int(obj.balance_cents or 0))

    def get_balance_usd_label(self, obj):
        # Backward-compatible field name retained without implying an exchange rate.
        return None

    def get_promotional_credit_label(self, obj):
        return promotional_credit_label_from_cents(int(obj.balance_cents or 0))

    def get_promotional_credit_policy(self, obj):
        return PROMOTIONAL_CREDIT_POLICY

    def get_can_buy_promotional_credits(self, obj):
        return False

    def get_can_transfer_promotional_credits(self, obj):
        return False

    def get_can_convert_promotional_credits_to_cash(self, obj):
        return False


class DirectPaymentIntentSerializer(serializers.ModelSerializer):
    amount_usd_label = serializers.SerializerMethodField()
    direct_payment_intent_id = serializers.SerializerMethodField()
    payment_reference = serializers.SerializerMethodField()
    payment_status = serializers.SerializerMethodField()
    payment_provider = serializers.SerializerMethodField()

    class Meta:
        model = DirectPaymentIntent
        fields = [
            "id",
            "provider",
            "target_type",
            "target_id",
            "amount_cents",
            "amount_usd_label",
            "currency",
            "status",
            "payment_status",
            "tx_ref",
            "payment_reference",
            "provider_ref",
            "payment_provider",
            "direct_payment_intent_id",
            "payment_url",
            "metadata",
            "processed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_amount_usd_label(self, obj):
        return f"${(int(obj.amount_cents or 0) / 100):,.2f}"

    def get_direct_payment_intent_id(self, obj):
        return str(obj.id)

    def get_payment_reference(self, obj):
        return str(obj.tx_ref or "")

    def get_payment_status(self, obj):
        return str(obj.status or "")

    def get_payment_provider(self, obj):
        return str(obj.provider or "")


class DirectPaymentAuditEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = DirectPaymentAuditEvent
        fields = [
            "id",
            "intent",
            "event",
            "provider",
            "tx_ref",
            "target_type",
            "target_id",
            "status",
            "actor",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields


class RevenueLaunchEvidenceAuditEventSerializer(serializers.ModelSerializer):
    actor_display = serializers.SerializerMethodField()

    class Meta:
        model = RevenueLaunchEvidenceAuditEvent
        fields = [
            "id",
            "event_type",
            "actor",
            "actor_display",
            "redacted_detail",
            "created_at",
        ]
        read_only_fields = fields

    def get_actor_display(self, obj):
        actor = getattr(obj, "actor", None)
        if not actor:
            return ""
        return (
            str(getattr(actor, "display_name", "") or "").strip()
            or str(getattr(actor, "username", "") or "").strip()
            or "KIS staff"
        )


class RevenueLaunchEvidenceRecordSerializer(serializers.ModelSerializer):
    private_media_asset_id = serializers.PrimaryKeyRelatedField(
        source="private_media_asset",
        queryset=MediaAsset.objects.filter(is_deleted=False),
        required=False,
        allow_null=True,
    )
    reviewer_display = serializers.SerializerMethodField()
    created_by_display = serializers.SerializerMethodField()
    is_expired = serializers.SerializerMethodField()
    required_reviewer_role = serializers.SerializerMethodField()
    audit_events = RevenueLaunchEvidenceAuditEventSerializer(many=True, read_only=True)

    class Meta:
        model = RevenueLaunchEvidenceRecord
        fields = [
            "id",
            "area",
            "title",
            "status",
            "owner_role",
            "reviewer",
            "reviewer_display",
            "required_reviewer_role",
            "private_media_asset_id",
            "redacted_summary",
            "expires_at",
            "is_expired",
            "submitted_at",
            "reviewed_at",
            "created_by",
            "created_by_display",
            "audit_events",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "reviewer",
            "reviewer_display",
            "required_reviewer_role",
            "submitted_at",
            "is_expired",
            "reviewed_at",
            "created_by",
            "created_by_display",
            "audit_events",
            "created_at",
            "updated_at",
        ]

    def _display_user(self, user):
        if not user:
            return ""
        return (
            str(getattr(user, "display_name", "") or "").strip()
            or str(getattr(user, "username", "") or "").strip()
            or "KIS staff"
        )

    def get_reviewer_display(self, obj):
        return self._display_user(getattr(obj, "reviewer", None))

    def get_created_by_display(self, obj):
        return self._display_user(getattr(obj, "created_by", None))

    def get_is_expired(self, obj):
        return evidence_record_is_expired(obj)

    def get_required_reviewer_role(self, obj):
        return REVIEWER_ROLE_BY_AREA.get(getattr(obj, "area", ""), "")

    def to_internal_value(self, data):
        if isinstance(data, dict):
            blocked = sorted(
                str(key)
                for key in data.keys()
                if str(key).strip().lower() in REVENUE_EVIDENCE_BLOCKED_KEYS
            )
            if blocked:
                raise serializers.ValidationError(
                    {
                        "detail": "Revenue evidence stores redacted summaries and private media references only.",
                        "blocked_fields": blocked,
                    }
                )
        return super().to_internal_value(data)

    def validate(self, attrs):
        blocked_seen: list[str] = []

        def scan(value, path=""):
            if isinstance(value, dict):
                for key, nested in value.items():
                    normalized = str(key).strip().lower()
                    if normalized in REVENUE_EVIDENCE_BLOCKED_KEYS:
                        blocked_seen.append(path + str(key))
                    scan(nested, f"{path}{key}.")
            elif isinstance(value, list):
                for idx, nested in enumerate(value):
                    scan(nested, f"{path}{idx}.")

        scan(attrs)
        if blocked_seen:
            raise serializers.ValidationError(
                {
                    "detail": "Revenue evidence stores redacted summaries and private media references only.",
                    "blocked_fields": sorted(set(blocked_seen)),
                }
            )
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        record = RevenueLaunchEvidenceRecord.objects.create(
            **validated_data,
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
        RevenueLaunchEvidenceAuditEvent.objects.create(
            evidence_record=record,
            event_type="evidence_record_created",
            actor=user if getattr(user, "is_authenticated", False) else None,
            redacted_detail={"area": record.area, "status": record.status},
        )
        if record.private_media_asset_id:
            RevenueLaunchEvidenceAuditEvent.objects.create(
                evidence_record=record,
                event_type="private_media_reference_added",
                actor=user if getattr(user, "is_authenticated", False) else None,
                redacted_detail={"private_media_asset_id": str(record.private_media_asset_id)},
            )
        return record


class CreditAccountSerializer(serializers.ModelSerializer):
    promotional_credit_label = serializers.SerializerMethodField()
    promotional_credit_policy = serializers.SerializerMethodField()

    class Meta:
        model = CreditAccount
        fields = [
            "id",
            "credits",
            "locked_credits",
            "promotional_credit_label",
            "promotional_credit_policy",
            "metadata",
            "created_at",
        ]

    def get_promotional_credit_label(self, obj):
        return f"{int(obj.credits or 0)} promotional credits"

    def get_promotional_credit_policy(self, obj):
        return PROMOTIONAL_CREDIT_POLICY


class WalletLedgerEntrySerializer(serializers.ModelSerializer):
    amount_usd = serializers.SerializerMethodField()
    amount_usd_compact = serializers.SerializerMethodField()
    balance_after_usd = serializers.SerializerMethodField()
    balance_after_usd_compact = serializers.SerializerMethodField()
    counterparty_user_id = serializers.SerializerMethodField()
    counterparty_name = serializers.SerializerMethodField()
    counterparty_phone = serializers.SerializerMethodField()
    receipt_url = serializers.SerializerMethodField()
    receipt_pdf_url = serializers.SerializerMethodField()
    amount_promotional_credit_label = serializers.SerializerMethodField()
    credits_delta_label = serializers.SerializerMethodField()

    _REFERENCE_COUNTERPARTY_UUID_PATTERN = re.compile(
        r"^(?:transfer_to|transfer_from|credit_transfer_to|credit_transfer_from):"
        r"(?P<user_id>[0-9a-fA-F-]{36})$"
    )

    class Meta:
        model = WalletLedgerEntry
        fields = [
            "id",
            "kind",
            "amount_cents",
            "amount_usd",
            "amount_usd_compact",
            "amount_promotional_credit_label",
            "credits_delta",
            "credits_delta_label",
            "balance_after_cents",
            "balance_after_usd",
            "balance_after_usd_compact",
            "credits_after",
            "status",
            "reference",
            "counterparty_user_id",
            "counterparty_name",
            "counterparty_phone",
            "meta",
            "receipt_url",
            "receipt_pdf_url",
            "created_at",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._counterparty_cache: dict[str, dict[str, str]] = {}
        self._receipt_cache: dict[str, tuple[str | None, str | None]] = {}

    def _user_counterparty(self, user_id: str) -> dict[str, str]:
        cached = self._counterparty_cache.get(user_id)
        if cached is not None:
            return cached

        payload: dict[str, str] = {"user_id": "", "name": "", "phone": ""}
        user = User.objects.filter(id=user_id).only(
            "id",
            "display_name",
            "username",
            "email",
            "phone",
            "phone_country_code",
            "phone_number",
        ).first()
        if user:
            name = (
                str(getattr(user, "display_name", "") or "").strip()
                or str(getattr(user, "username", "") or "").strip()
                or str(getattr(user, "email", "") or "").strip()
                or "KIS user"
            )
            phone = str(getattr(user, "phone", "") or "").strip()
            if not phone:
                code = str(getattr(user, "phone_country_code", "") or "").strip()
                number = str(getattr(user, "phone_number", "") or "").strip()
                if code and number:
                    phone = f"{code}{number}"
                else:
                    phone = number
            payload = {
                "user_id": str(user.id),
                "name": name,
                "phone": phone,
            }
        self._counterparty_cache[user_id] = payload
        return payload

    def _resolve_counterparty(self, obj: WalletLedgerEntry) -> dict[str, str]:
        raw_meta = obj.meta if isinstance(obj.meta, dict) else {}
        counterparty = raw_meta.get("counterparty") if isinstance(raw_meta, dict) else None
        if isinstance(counterparty, dict):
            user_id = str(counterparty.get("user_id") or "").strip()
            name = str(counterparty.get("name") or "").strip()
            phone = str(counterparty.get("phone") or "").strip()
            if user_id or name or phone:
                return {
                    "user_id": user_id,
                    "name": name,
                    "phone": phone,
                }

        reference = str(obj.reference or "").strip()
        match = self._REFERENCE_COUNTERPARTY_UUID_PATTERN.match(reference)
        if match:
            return self._user_counterparty(match.group("user_id"))

        if ":" in reference:
            _, raw_value = reference.split(":", 1)
            value = raw_value.strip()
            if value:
                if re.fullmatch(r"^\+?\d[\d\s().-]*$", value):
                    return {"user_id": "", "name": "", "phone": value}
                return {"user_id": "", "name": value, "phone": ""}

        return {"user_id": "", "name": "", "phone": ""}

    def _receipt_urls(self, obj: WalletLedgerEntry) -> tuple[str | None, str | None]:
        request = self.context.get("request")
        if not request:
            return None, None
        key = str(obj.id)
        if key not in self._receipt_cache:
            urls: tuple[str | None, str | None] = (None, None)
            tx_ref = str(obj.reference or "").strip()
            if tx_ref:
                tx = WalletTransaction.objects.filter(
                    user=obj.user,
                    tx_ref=tx_ref,
                    is_deleted=False,
                ).first()
                if tx and tx.status == "success":
                    try:
                        urls = build_receipt_urls(request, tx)
                    except Exception:
                        urls = (None, None)
            self._receipt_cache[key] = urls
        return self._receipt_cache[key]

    def get_amount_usd(self, obj):
        return str(cents_to_usd(int(obj.amount_cents or 0)))

    def get_amount_usd_compact(self, obj):
        return cents_to_usd_compact(int(obj.amount_cents or 0))

    def get_amount_promotional_credit_label(self, obj):
        return promotional_credit_label_from_cents(int(obj.amount_cents or 0))

    def get_credits_delta_label(self, obj):
        return credit_delta_label(int(obj.credits_delta or 0))

    def get_balance_after_usd(self, obj):
        return str(cents_to_usd(int(obj.balance_after_cents or 0)))

    def get_balance_after_usd_compact(self, obj):
        return cents_to_usd_compact(int(obj.balance_after_cents or 0))

    def get_counterparty_user_id(self, obj):
        return self._resolve_counterparty(obj).get("user_id")

    def get_counterparty_name(self, obj):
        return self._resolve_counterparty(obj).get("name")

    def get_counterparty_phone(self, obj):
        return self._resolve_counterparty(obj).get("phone")

    def get_receipt_url(self, obj: WalletLedgerEntry) -> str | None:
        return self._receipt_urls(obj)[0]

    def get_receipt_pdf_url(self, obj: WalletLedgerEntry) -> str | None:
        return self._receipt_urls(obj)[1]


class WalletTransactionSerializer(serializers.ModelSerializer):
    receipt_url = serializers.SerializerMethodField()
    receipt_pdf_url = serializers.SerializerMethodField()
    amount_usd = serializers.SerializerMethodField()
    amount_usd_compact = serializers.SerializerMethodField()
    payment_status = serializers.SerializerMethodField()
    payment_reference = serializers.SerializerMethodField()
    payment_provider = serializers.SerializerMethodField()
    direct_payment_intent_id = serializers.SerializerMethodField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._receipt_cache: dict[int, tuple[str | None, str | None]] = {}

    class Meta:
        model = WalletTransaction
        fields = [
            "id",
            "provider",
            "method",
            "amount_cents",
            "amount_usd",
            "amount_usd_compact",
            "currency",
            "status",
            "payment_status",
            "tx_ref",
            "payment_reference",
            "provider_ref",
            "payment_provider",
            "direct_payment_intent_id",
            "payment_url",
            "meta",
            "created_at",
            "receipt_url",
            "receipt_pdf_url",
        ]

    def _receipt_urls(self, obj: WalletTransaction) -> tuple[str | None, str | None]:
        request = self.context.get("request")
        if not request:
            return None, None
        cache = self._receipt_cache
        key = obj.id
        if key not in cache:
            if obj.status == "success":
                try:
                    cache[key] = build_receipt_urls(request, obj)
                except Exception:
                    # A transient storage failure must never break the
                    # whole billing-history response — the receipt just
                    # isn't available on this request; it'll retry the
                    # next time this is read.
                    cache[key] = (None, None)
            else:
                cache[key] = (None, None)
        return cache[key]

    def get_receipt_url(self, obj: WalletTransaction) -> str | None:
        return self._receipt_urls(obj)[0]

    def get_receipt_pdf_url(self, obj: WalletTransaction) -> str | None:
        return self._receipt_urls(obj)[1]

    def get_amount_usd(self, obj: WalletTransaction) -> str:
        return str(cents_to_usd(int(obj.amount_cents or 0)))

    def get_amount_usd_compact(self, obj: WalletTransaction) -> str:
        return cents_to_usd_compact(int(obj.amount_cents or 0))

    def get_payment_status(self, obj: WalletTransaction) -> str:
        return str(obj.status or "")

    def get_payment_reference(self, obj: WalletTransaction) -> str:
        return str(obj.tx_ref or "")

    def get_payment_provider(self, obj: WalletTransaction) -> str:
        return str(obj.provider or "")

    def get_direct_payment_intent_id(self, obj: WalletTransaction) -> str:
        return str(obj.id)


class PromoCodeSerializer(serializers.ModelSerializer):
    cash_bonus_usd = serializers.SerializerMethodField()
    cash_bonus_usd_compact = serializers.SerializerMethodField()

    class Meta:
        model = PromoCode
        fields = [
            "id",
            "code",
            "description",
            "cash_bonus_cents",
            "cash_bonus_usd",
            "cash_bonus_usd_compact",
            "credit_bonus",
            "usage_limit",
            "used_count",
            "starts_at",
            "ends_at",
            "is_active",
            "metadata",
        ]

    def get_cash_bonus_usd(self, obj):
        return str(cents_to_usd(int(obj.cash_bonus_cents or 0)))

    def get_cash_bonus_usd_compact(self, obj):
        return cents_to_usd_compact(int(obj.cash_bonus_cents or 0))


class BillingReconciliationSerializer(serializers.ModelSerializer):
    amount_usd = serializers.SerializerMethodField()
    amount_usd_compact = serializers.SerializerMethodField()

    organization = serializers.PrimaryKeyRelatedField(
        queryset=HealthcareOrganization.objects.all(),
        allow_null=True,
        required=False,
    )
    transaction = serializers.PrimaryKeyRelatedField(
        queryset=WalletTransaction.objects.all(),
        allow_null=True,
        required=False,
    )

    class Meta:
        model = BillingReconciliation
        fields = [
            "id",
            "organization",
            "transaction",
            "insurance_provider",
            "amount_cents",
            "amount_usd",
            "amount_usd_compact",
            "status",
            "reconciled_at",
            "note",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")

    def get_amount_usd(self, obj):
        return str(cents_to_usd(int(obj.amount_cents or 0)))

    def get_amount_usd_compact(self, obj):
        return cents_to_usd_compact(int(obj.amount_cents or 0))


class InsuranceClaimSerializer(serializers.ModelSerializer):
    amount_usd = serializers.SerializerMethodField()
    amount_usd_compact = serializers.SerializerMethodField()
    paid_amount_usd = serializers.SerializerMethodField()
    paid_amount_usd_compact = serializers.SerializerMethodField()

    organization = serializers.PrimaryKeyRelatedField(
        queryset=HealthcareOrganization.objects.all(),
        allow_null=True,
        required=False,
    )
    patient = serializers.PrimaryKeyRelatedField(
        queryset=PatientMasterRecord.objects.all(),
        allow_null=True,
        required=False,
    )

    class Meta:
        model = InsuranceClaim
        fields = [
            "id",
            "organization",
            "patient",
            "insurance_provider",
            "service_code",
            "claim_reference",
            "amount_cents",
            "amount_usd",
            "amount_usd_compact",
            "paid_amount_cents",
            "paid_amount_usd",
            "paid_amount_usd_compact",
            "status",
            "submitted_at",
            "resolved_at",
            "notes",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")

    def get_amount_usd(self, obj):
        return str(cents_to_usd(int(obj.amount_cents or 0)))

    def get_amount_usd_compact(self, obj):
        return cents_to_usd_compact(int(obj.amount_cents or 0))

    def get_paid_amount_usd(self, obj):
        return str(cents_to_usd(int(obj.paid_amount_cents or 0)))

    def get_paid_amount_usd_compact(self, obj):
        return cents_to_usd_compact(int(obj.paid_amount_cents or 0))


class PaymentDisputeSerializer(serializers.ModelSerializer):
    wallet_transaction = serializers.PrimaryKeyRelatedField(queryset=WalletTransaction.objects.all())
    claim = serializers.PrimaryKeyRelatedField(
        queryset=InsuranceClaim.objects.all(),
        allow_null=True,
        required=False,
    )
    reported_by = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        allow_null=True,
        required=False,
    )

    class Meta:
        model = PaymentDispute
        fields = [
            "id",
            "wallet_transaction",
            "claim",
            "reported_by",
            "dispute_reason",
            "resolution",
            "status",
            "resolved_at",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")


class PricingTierSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccountTier
        fields = ["id", "name", "price_cents", "features_json"]
