from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.accounts.models import BaseEntity


class RewardLedgerEntry(BaseEntity):
    """
    The single source of truth for KIS Coins. Balance is always derived by
    summing `amount` for a user's CONFIRMED/REDEEMED rows — there is no
    separate mutable balance field anywhere. See get_reward_balance() in
    services.py.

    Reversing or expiring a row that has already reached CONFIRMED/REDEEMED
    never mutates that row (see save() below) — it creates a new row with
    `reversal_of` pointing back and an opposite-signed amount, mirroring
    apps.billing.services.reverse_tier_upgrade_payment's existing doctrine:
    "never edit a settled financial row, write a compensating entry."
    Reversing a still-PENDING row (one that never contributed to the
    balance) is a direct status flip instead, since there is nothing to
    compensate for.
    """

    TYPE_ACHIEVEMENT = "achievement"
    TYPE_REPEATABLE = "repeatable"
    TYPE_REFERRAL = "referral"
    TYPE_PROMO = "promo"
    TYPE_REDEMPTION = "redemption"
    TYPE_ADMIN_ADJUSTMENT = "admin_adjustment"
    TYPE_REVERSAL = "reversal"
    TYPE_EXPIRATION = "expiration"
    TYPE_CHOICES = [
        (TYPE_ACHIEVEMENT, "Achievement"),
        (TYPE_REPEATABLE, "Repeatable"),
        (TYPE_REFERRAL, "Referral"),
        (TYPE_PROMO, "Promo"),
        (TYPE_REDEMPTION, "Redemption"),
        (TYPE_ADMIN_ADJUSTMENT, "Admin adjustment"),
        (TYPE_REVERSAL, "Reversal"),
        (TYPE_EXPIRATION, "Expiration"),
    ]

    STATUS_PENDING = "pending"
    STATUS_CONFIRMED = "confirmed"
    STATUS_REDEEMED = "redeemed"
    STATUS_REVERSED = "reversed"
    STATUS_EXPIRED = "expired"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_CONFIRMED, "Confirmed"),
        (STATUS_REDEEMED, "Redeemed"),
        (STATUS_REVERSED, "Reversed"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    # Statuses that count toward a user's spendable balance. Nothing else
    # (including CONFIRMED/REDEEMED rows that have since been reversed via a
    # compensating TYPE_REVERSAL/TYPE_EXPIRATION row) should ever be added
    # here without updating get_reward_balance() to match.
    BALANCE_STATUSES = (STATUS_CONFIRMED, STATUS_REDEEMED)

    # Fields that become immutable once a row leaves PENDING. `status` and
    # `metadata`/`description`/`updated_at` are deliberately excluded — those
    # are the only things allowed to change post-settlement, and only via
    # the controlled transitions in services.py, never a bare .save().
    _IMMUTABLE_AFTER_SETTLEMENT = (
        "user_id", "type", "source", "amount",
        "reference_type", "reference_id", "idempotency_key",
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reward_ledger_entries",
    )
    type = models.CharField(max_length=32, choices=TYPE_CHOICES, db_index=True)
    # Free-text-ish but conventionally a stable slug (e.g. "profile_completion",
    # "referral", "subscription_discount") — the STRUCTURED type/source/
    # reference_type/reference_id combination is what answers "why does this
    # user have this balance," never the human-readable `description` field.
    source = models.CharField(max_length=100, db_index=True)
    amount = models.IntegerField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)

    reference_type = models.CharField(max_length=64, blank=True, default="")
    reference_id = models.UUIDField(null=True, blank=True)

    reversal_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversals",
    )

    idempotency_key = models.CharField(max_length=200, null=True, blank=True)
    description = models.CharField(max_length=255, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    effective_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "type", "status"]),
            models.Index(fields=["status", "expires_at"]),
            models.Index(fields=["reference_type", "reference_id"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["idempotency_key"],
                name="uniq_reward_ledger_idempotency_key",
                condition=models.Q(idempotency_key__isnull=False),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.type}:{self.source} {self.amount:+d} ({self.status}) user={self.user_id}"

    def save(self, *args, **kwargs):
        if self.pk:
            existing = RewardLedgerEntry.objects.filter(pk=self.pk).first()
            if existing is not None and existing.status != self.STATUS_PENDING:
                for field_name in self._IMMUTABLE_AFTER_SETTLEMENT:
                    if getattr(existing, field_name) != getattr(self, field_name):
                        raise ValueError(
                            f"RewardLedgerEntry.{field_name} is immutable once the entry "
                            f"has left PENDING (current status={existing.status!r})."
                        )
        super().save(*args, **kwargs)


class AchievementDefinition(BaseEntity):
    """
    Admin-configurable catalog of one-time rewards (e.g. profile completion,
    email verification). Earning is idempotent per (user, code) — see
    grant_achievement() in services.py — enforced via the ledger's
    idempotency_key uniqueness, not a separate tracking table.
    """
    code = models.CharField(max_length=100, unique=True)
    title = models.CharField(max_length=200)
    coin_amount = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return self.code


class RepeatableRewardRule(BaseEntity):
    FREQUENCY_DAILY = "daily"
    FREQUENCY_WEEKLY = "weekly"
    FREQUENCY_MONTHLY = "monthly"
    FREQUENCY_PER_EVENT = "per_event"
    FREQUENCY_CHOICES = [
        (FREQUENCY_DAILY, "Daily"),
        (FREQUENCY_WEEKLY, "Weekly"),
        (FREQUENCY_MONTHLY, "Monthly"),
        (FREQUENCY_PER_EVENT, "Per event"),
    ]

    code = models.CharField(max_length=100, unique=True)
    title = models.CharField(max_length=200)
    coin_amount = models.PositiveIntegerField()
    frequency = models.CharField(max_length=16, choices=FREQUENCY_CHOICES)
    max_per_period = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return self.code


class RedemptionPolicy(BaseEntity):
    """
    Configurable ceilings for using KIS Coins as a discount. One row per
    redemption context (today: "subscription_upgrade" only). Read live by
    the redemption engine (Phase 5) — never hard-coded, so ceilings can be
    changed without a deploy.
    """
    context = models.CharField(max_length=64, unique=True, default="subscription_upgrade")
    normal_max_discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("40.00"))
    absolute_max_discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("60.00"))
    min_cash_contribution_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("20.00"))
    # How many cents one KIS Coin is worth when applied as a subscription
    # discount. Decimal("1.0000") is an explicit PLACEHOLDER pending a real
    # business decision — see the Phase 1 design report, §16 item 5.
    coin_value_cents = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal("1.0000"))
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"RedemptionPolicy({self.context})"
