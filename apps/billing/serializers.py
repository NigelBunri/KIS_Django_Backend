from __future__ import annotations

import re

from decimal import Decimal, ROUND_HALF_UP
from rest_framework import serializers

from .documents import build_receipt_urls
from .models import (
    BillingReconciliation,
    CreditAccount,
    InsuranceClaim,
    PaymentDispute,
    PromoCode,
    WalletAccount,
    WalletLedgerEntry,
    WalletTransaction,
)
from .services import cents_to_usd, cents_to_usd_compact
from apps.core.models import HealthcareOrganization, PatientMasterRecord
from apps.accounts.models import AccountTier, User


class WalletAccountSerializer(serializers.ModelSerializer):
    balance_usd = serializers.SerializerMethodField()
    balance_usd_compact = serializers.SerializerMethodField()
    balance_micro = serializers.SerializerMethodField()
    balance_kisc_label = serializers.SerializerMethodField()
    balance_usd_label = serializers.SerializerMethodField()

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
        cents = int(obj.balance_cents or 0)
        kisc = Decimal(cents) / Decimal("10000")
        quantized = kisc.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        text = format(quantized, "f")
        return f"{text} KISC"

    def get_balance_usd_label(self, obj):
        usd = cents_to_usd(int(obj.balance_cents or 0))
        quantized = usd.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        text = format(quantized, "f")
        return f"${text}"


class CreditAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = CreditAccount
        fields = ["id", "credits", "locked_credits", "metadata", "created_at"]


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
            "credits_delta",
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
                if tx:
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
            "tx_ref",
            "provider_ref",
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
            cache[key] = build_receipt_urls(request, obj)
        return cache[key]

    def get_receipt_url(self, obj: WalletTransaction) -> str | None:
        return self._receipt_urls(obj)[0]

    def get_receipt_pdf_url(self, obj: WalletTransaction) -> str | None:
        return self._receipt_urls(obj)[1]

    def get_amount_usd(self, obj: WalletTransaction) -> str:
        return str(cents_to_usd(int(obj.amount_cents or 0)))

    def get_amount_usd_compact(self, obj: WalletTransaction) -> str:
        return cents_to_usd_compact(int(obj.amount_cents or 0))


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
