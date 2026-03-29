from __future__ import annotations

from django.db import models
from django.conf import settings
from django.utils import timezone

from apps.accounts.models import BaseEntity
from apps.core.models import HealthcareOrganization, PatientMasterRecord


class WalletAccount(BaseEntity):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wallet_account",
    )
    balance_cents = models.BigIntegerField(default=0)
    locked_cents = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=8, default="USD")
    status = models.CharField(max_length=30, default="active")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "status"]) ]

    def __str__(self) -> str:
        return f"Wallet({self.user_id}) {self.balance_cents} {self.currency}"


class CreditAccount(BaseEntity):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="credit_account",
    )
    credits = models.IntegerField(default=0)
    locked_credits = models.IntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user"]) ]

    def __str__(self) -> str:
        return f"Credits({self.user_id}) {self.credits}"


class WalletLedgerEntry(BaseEntity):
    KIND_CHOICES = [
        ("deposit", "Deposit"),
        ("conversion_cash_to_credits", "Convert cash to credits"),
        ("conversion_credits_to_cash", "Convert credits to cash"),
        ("transfer_in", "Transfer in"),
        ("transfer_out", "Transfer out"),
        ("tier_upgrade", "Tier upgrade"),
        ("promo", "Promo"),
        ("admin_adjust", "Admin adjustment"),
        ("purchase", "Purchase"),
        ("service_payout", "Service payout"),
        ("refund", "Refund"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wallet_ledger",
    )
    kind = models.CharField(max_length=64, choices=KIND_CHOICES)
    amount_cents = models.BigIntegerField(default=0)
    credits_delta = models.IntegerField(default=0)
    balance_after_cents = models.BigIntegerField(default=0)
    credits_after = models.IntegerField(default=0)
    status = models.CharField(max_length=32, default="posted")
    reference = models.CharField(max_length=255, blank=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "kind"]),
            models.Index(fields=["created_at"]),
        ]


class WalletTransaction(BaseEntity):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("success", "Success"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wallet_transactions",
    )
    provider = models.CharField(max_length=100, default="flutterwave")
    method = models.CharField(max_length=100, blank=True)
    amount_cents = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=8, default="USD")
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="pending")
    tx_ref = models.CharField(max_length=255, unique=True)
    provider_ref = models.CharField(max_length=255, blank=True)
    payment_url = models.URLField(blank=True)
    meta = models.JSONField(default=dict, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["tx_ref"]),
        ]


class PromoCode(BaseEntity):
    code = models.CharField(max_length=64, unique=True)
    description = models.TextField(blank=True)
    cash_bonus_cents = models.BigIntegerField(default=0)
    credit_bonus = models.IntegerField(default=0)
    usage_limit = models.IntegerField(null=True, blank=True)
    used_count = models.IntegerField(default=0)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["code"]) ]


class PromoRedemption(BaseEntity):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="promo_redemptions",
    )
    promo = models.ForeignKey(PromoCode, on_delete=models.CASCADE, related_name="redemptions")
    redeemed_at = models.DateTimeField(default=timezone.now)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("user", "promo")


class BillingReconciliation(BaseEntity):
    STATUS_PENDING = "pending"
    STATUS_RECONCILED = "reconciled"
    STATUS_REQUIRES_ACTION = "requires_action"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RECONCILED, "Reconciled"),
        (STATUS_REQUIRES_ACTION, "Requires action"),
    ]

    organization = models.ForeignKey(
        HealthcareOrganization,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="billing_reconciliations",
    )
    transaction = models.ForeignKey(
        WalletTransaction,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="billing_reconciliations",
    )
    insurance_provider = models.CharField(max_length=128, blank=True)
    amount_cents = models.BigIntegerField(default=0)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    reconciled_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["organization"]),
        ]

    def __str__(self):
        return f"Reconciliation {self.id} · {self.status}"


class InsuranceClaim(BaseEntity):
    STATUS_SUBMITTED = "submitted"
    STATUS_IN_REVIEW = "in_review"
    STATUS_APPROVED = "approved"
    STATUS_DENIED = "denied"
    STATUS_PAID = "paid"

    STATUS_CHOICES = [
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_IN_REVIEW, "In review"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_DENIED, "Denied"),
        (STATUS_PAID, "Paid"),
    ]

    patient = models.ForeignKey(
        PatientMasterRecord,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="insurance_claims",
    )
    organization = models.ForeignKey(
        HealthcareOrganization,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="insurance_claims",
    )
    insurance_provider = models.CharField(max_length=128, blank=True)
    service_code = models.CharField(max_length=64, blank=True)
    claim_reference = models.CharField(max_length=128, blank=True)
    amount_cents = models.BigIntegerField(default=0)
    paid_amount_cents = models.BigIntegerField(default=0)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_SUBMITTED)
    submitted_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["insurance_provider"]),
        ]

    def mark_resolved(self):
        self.resolved_at = timezone.now()
        self.save(update_fields=["resolved_at", "updated_at"])

    def __str__(self):
        return f"Claim {self.service_code or self.claim_reference} ({self.status})"


class PaymentDispute(BaseEntity):
    STATUS_OPEN = "open"
    STATUS_INVESTIGATING = "investigating"
    STATUS_RESOLVED = "resolved"

    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_INVESTIGATING, "Investigating"),
        (STATUS_RESOLVED, "Resolved"),
    ]

    wallet_transaction = models.ForeignKey(
        WalletTransaction,
        on_delete=models.CASCADE,
        related_name="disputes",
    )
    claim = models.ForeignKey(
        InsuranceClaim,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="disputes",
    )
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="payment_disputes",
    )
    dispute_reason = models.TextField(blank=True)
    resolution = models.TextField(blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_OPEN)
    resolved_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status"])]

    def mark_resolved(self, note: str | None = None):
        self.status = self.STATUS_RESOLVED
        self.resolved_at = timezone.now()
        if note:
            self.resolution = note
        self.save(update_fields=["status", "resolved_at", "resolution", "updated_at"])

    def __str__(self):
        return f"Dispute {self.id} · {self.status}"
