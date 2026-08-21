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
        ("downgrade_credit", "Downgrade proration credit"),
        ("subscription_reversal", "Subscription payment reversal (refund/chargeback)"),
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


class DirectPaymentIntent(BaseEntity):
    STATUS_PENDING = "pending"
    STATUS_PAID = "paid"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PAID, "Paid"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    TARGET_MARKETPLACE_ORDER = "marketplace_order"
    TARGET_SERVICE_BOOKING_PAYMENT = "service_booking_payment"
    TARGET_EDUCATION_BOOKING = "education_booking"
    TARGET_HEALTH_BILLING_SESSION = "health_billing_session"
    TARGET_CHANNEL_MEMBERSHIP = "channel_membership"
    TARGET_CHANNEL_TIP = "channel_tip"

    TARGET_CHOICES = [
        (TARGET_MARKETPLACE_ORDER, "Marketplace order"),
        (TARGET_SERVICE_BOOKING_PAYMENT, "Service booking payment"),
        (TARGET_EDUCATION_BOOKING, "Education booking"),
        (TARGET_HEALTH_BILLING_SESSION, "Health billing session"),
        (TARGET_CHANNEL_MEMBERSHIP, "Channel membership"),
        (TARGET_CHANNEL_TIP, "Channel tip"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="direct_payment_intents",
    )
    provider = models.CharField(max_length=64, default="flutterwave")
    target_type = models.CharField(max_length=64, choices=TARGET_CHOICES)
    target_id = models.UUIDField(db_index=True)
    amount_cents = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=8, default="USD")
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    tx_ref = models.CharField(max_length=255, unique=True)
    provider_ref = models.CharField(max_length=255, blank=True)
    payment_url = models.TextField(blank=True, default="")
    idempotency_key = models.CharField(max_length=255, blank=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    provider_payload = models.JSONField(default=dict, blank=True)
    raw_callback = models.JSONField(default=dict, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["target_type", "target_id"]),
            models.Index(fields=["tx_ref"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["target_type", "target_id", "status"],
                condition=models.Q(status="pending"),
                name="uniq_pending_direct_payment_target",
            )
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.tx_ref}:{self.status}"


class DirectPaymentAuditEvent(BaseEntity):
    intent = models.ForeignKey(
        DirectPaymentIntent,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    event = models.CharField(max_length=80)
    provider = models.CharField(max_length=64, default="flutterwave")
    tx_ref = models.CharField(max_length=255, blank=True, db_index=True)
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.UUIDField(null=True, blank=True)
    status = models.CharField(max_length=32, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="direct_payment_audit_events",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["event", "created_at"]),
            models.Index(fields=["tx_ref"]),
            models.Index(fields=["target_type", "target_id"]),
        ]


class RevenueLaunchEvidenceRecord(BaseEntity):
    AREA_CHOICES = [
        ("legal_review", "Legal review"),
        ("pastoral_child_safety_review", "Pastoral and child-safety review"),
        ("tax_accounting_review", "Tax and accounting review"),
        ("flutterwave_sandbox_proof", "Flutterwave sandbox proof"),
        ("invoice_receipt_proof", "Invoice and receipt proof"),
        ("refund_support_proof", "Refund and support proof"),
        ("entitlement_grace_policy", "Entitlement grace policy"),
        ("promotion_sponsored_label_policy", "Promotion sponsored-label policy"),
        ("verification_fee_policy", "Verification fee policy"),
        ("enterprise_contract_policy", "Enterprise contract policy"),
        ("privacy_analytics_policy", "Privacy analytics policy"),
        ("rollback_proof", "Rollback proof"),
    ]
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("submitted", "Submitted"),
        ("needs_changes", "Needs changes"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("expired", "Expired"),
        ("revoked", "Revoked"),
    ]

    area = models.CharField(max_length=80, choices=AREA_CHOICES, db_index=True)
    title = models.CharField(max_length=180)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default="draft", db_index=True)
    owner_role = models.CharField(max_length=80, blank=True)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="revenue_evidence_reviews",
    )
    private_media_asset = models.ForeignKey(
        "media.MediaAsset",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="revenue_launch_evidence_records",
    )
    redacted_summary = models.TextField(blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="revenue_evidence_records_created",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["area", "status"]),
            models.Index(fields=["status", "expires_at"]),
            models.Index(fields=["created_by", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.area}:{self.title}:{self.status}"


class RevenueLaunchEvidenceAuditEvent(BaseEntity):
    EVENT_CHOICES = [
        ("evidence_record_created", "Evidence record created"),
        ("evidence_record_updated", "Evidence record updated"),
        ("evidence_record_submitted", "Evidence record submitted"),
        ("evidence_record_reviewed", "Evidence record reviewed"),
        ("evidence_record_approved", "Evidence record approved"),
        ("evidence_record_rejected", "Evidence record rejected"),
        ("evidence_record_revoked", "Evidence record revoked"),
        ("evidence_record_expired", "Evidence record expired"),
        ("private_media_reference_added", "Private media reference added"),
        ("private_media_reference_removed", "Private media reference removed"),
    ]

    evidence_record = models.ForeignKey(
        RevenueLaunchEvidenceRecord,
        on_delete=models.CASCADE,
        related_name="audit_events",
    )
    event_type = models.CharField(max_length=80, choices=EVENT_CHOICES, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="revenue_evidence_audit_events",
    )
    redacted_detail = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["event_type", "created_at"]),
            models.Index(fields=["evidence_record", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        if self.pk and RevenueLaunchEvidenceAuditEvent.objects.filter(pk=self.pk).exists():
            raise ValueError("Revenue launch evidence audit events are immutable.")
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.event_type}:{self.evidence_record_id}"


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
