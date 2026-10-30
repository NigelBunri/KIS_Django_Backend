from __future__ import annotations

import secrets
import string

from django.conf import settings
from django.db import models

from apps.accounts.models import AccountTier, BaseEntity, Subscription

# Excludes visually ambiguous characters (0/O, 1/I/L) so a code is easy to
# read aloud or retype from a screenshot.
_CODE_ALPHABET = "".join(sorted(set(string.ascii_uppercase + string.digits) - set("0O1IL")))
_CODE_LENGTH = 8


def generate_referral_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


class ReferralCode(BaseEntity):
    """
    One stable, shareable code per user. Created lazily (get_or_create) on
    first access — deliberately not a field on User itself, so this feature
    needs no migration against the User table.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referral_code_record",
    )
    code = models.CharField(max_length=16, unique=True, db_index=True)

    def __str__(self) -> str:
        return self.code

    @classmethod
    def get_or_create_for_user(cls, user) -> "ReferralCode":
        existing = cls.objects.filter(user=user).first()
        if existing:
            return existing
        # Collision is astronomically unlikely at this alphabet/length, but
        # retry a few times rather than letting a rare IntegrityError surface.
        for _ in range(5):
            code = generate_referral_code()
            if not cls.objects.filter(code=code).exists():
                return cls.objects.create(user=user, code=code)
        raise RuntimeError("Could not generate a unique referral code.")


class ReferralRateConfig(BaseEntity):
    """
    Admin-editable referral commission rate per AccountTier — no code deploy
    needed to change a rate. Looked up and snapshotted onto a Referral at
    QUALIFICATION time only (see Referral.reward_rate_percent_snapshot);
    changing a rate here never retroactively affects an already-qualified
    referral.
    """
    tier = models.OneToOneField(AccountTier, on_delete=models.CASCADE, related_name="referral_rate_config")
    rate_percent = models.DecimalField(max_digits=5, decimal_places=2)
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"{self.tier.name}: {self.rate_percent}%"


class Referral(BaseEntity):
    """
    One row per referred user — the OneToOneField on referred_user means a
    person can be the *referred* party at most once, ever, enforced at the
    DB level regardless of how many times they re-register or which code
    they try to use later.

    Lifecycle (see apps.referrals.services for the transition functions):
      PENDING (registered, no qualifying payment yet)
        -> QUALIFIED (a qualifying subscription payment succeeded; a PENDING
           RewardLedgerEntry now exists)
        -> REWARDED (settlement window elapsed; the ledger entry is CONFIRMED)
      Any of PENDING/QUALIFIED/REWARDED -> REVERSED (refund/chargeback on the
        qualifying payment)
      PENDING -> BLOCKED (anti-farming, at registration time — unchanged)

    STATUS_PENDING/STATUS_REWARDED/STATUS_BLOCKED are unchanged in meaning
    from before this phase — the legacy apply_referral_reward_if_pending
    path (flat 200 points, granted at account activation) still exists
    and still transitions PENDING -> REWARDED directly on its own. This new
    qualification-based path is additive, not wired to any live caller yet
    (Phase 5 connects it to the real payment webhook and retires the legacy
    path's reward-granting role).
    """
    STATUS_PENDING = "pending"
    STATUS_QUALIFIED = "qualified"
    STATUS_REWARDED = "rewarded"
    STATUS_REVERSED = "reversed"
    STATUS_BLOCKED = "blocked"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_QUALIFIED, "Qualified"),
        (STATUS_REWARDED, "Rewarded"),
        (STATUS_REVERSED, "Reversed"),
        (STATUS_BLOCKED, "Blocked"),
    ]

    referrer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referrals_made",
    )
    referred_user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referred_by_record",
    )
    referral_code_used = models.CharField(max_length=16)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    reward_points_awarded = models.PositiveIntegerField(default=0)
    # Set once, at the PENDING -> QUALIFIED transition (apps.referrals.services
    # .qualify_referral). Distinct from updated_at (which any later, unrelated
    # save() also bumps) specifically so the Phase 11 settlement sweep has a
    # timestamp that only ever means "when this referral qualified."
    qualified_at = models.DateTimeField(null=True, blank=True)
    rewarded_at = models.DateTimeField(null=True, blank=True)
    block_reason = models.CharField(max_length=120, blank=True, default="")

    qualifying_subscription = models.ForeignKey(
        Subscription, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    qualifying_net_amount_cents = models.BigIntegerField(null=True, blank=True)
    reward_rate_percent_snapshot = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    reward_ledger_entry = models.ForeignKey(
        "rewards.RewardLedgerEntry", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        indexes = [
            models.Index(fields=["referrer", "status"]),
            models.Index(fields=["status", "qualified_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.referrer_id} -> {self.referred_user_id} ({self.status})"


class ReferralPayoutAccount(BaseEntity):
    """
    One per user — where their lifetime referral cash commissions
    (ReferralCommission, below) get paid out. A sibling of the per-domain
    payout fields on Shop/HealthInstitution/BroadcastChannel/
    EducationInstitution (those models keep both Flutterwave and Stripe
    connected simultaneously, split-at-charge-time); referral payouts are
    independent, after-the-fact transfers instead (see
    apps.billing.referral_payouts' module docstring for why), so this
    model asks the user to pick exactly ONE active provider rather than
    supporting both at once — unambiguous about where a settled
    commission actually gets sent.

    Flutterwave uses a Beneficiary (apps.billing.referral_payouts), not a
    subaccount — only the provider-issued beneficiary id is ever
    persisted here, never a raw bank account number (payout_bank_last4 is
    captured once, at connect time, for display only).
    """
    PROVIDER_FLUTTERWAVE = "flutterwave"
    PROVIDER_STRIPE = "stripe"
    PROVIDER_CHOICES = [
        (PROVIDER_FLUTTERWAVE, "Flutterwave"),
        (PROVIDER_STRIPE, "Stripe"),
    ]

    STATUS_NOT_CONNECTED = "not_connected"
    STATUS_PENDING = "pending"
    STATUS_ACTIVE = "active"
    STATUS_CHOICES = [
        (STATUS_NOT_CONNECTED, "Not connected"),
        (STATUS_PENDING, "Pending"),
        (STATUS_ACTIVE, "Active"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referral_payout_account",
    )
    provider = models.CharField(max_length=16, choices=PROVIDER_CHOICES, blank=True, default="")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_NOT_CONNECTED)

    flutterwave_beneficiary_id = models.CharField(max_length=128, blank=True, default="")
    payout_account_name = models.CharField(max_length=255, blank=True, default="")
    payout_bank_last4 = models.CharField(max_length=8, blank=True, default="")
    payout_country = models.CharField(max_length=2, blank=True, default="")

    stripe_account_id = models.CharField(max_length=128, blank=True, default="")
    stripe_charges_enabled = models.BooleanField(default=False)
    stripe_payouts_enabled = models.BooleanField(default=False)
    stripe_details_submitted = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"{self.user_id} ({self.provider or 'none'}:{self.status})"


class ReferralCommission(BaseEntity):
    """
    One row per qualifying account-upgrade payment made by a referred
    user, paying their referrer a flat REFERRAL_COMMISSION_RATE_PERCENT
    (apps.referrals.commission_services — currently 24%) of that
    payment's amount, for the lifetime of the referred account. This is a
    deliberately SEPARATE reward track from Referral/ReferralRateConfig
    above: that system pays a one-time, tier-rate KIS-Coins bonus at the
    FIRST qualifying payment only (Referral.referred_user is a
    OneToOneField — it can qualify exactly once, ever); this system pays
    real cash, every time, with no limit and no expiry. Both read the
    same permanent referrer relationship (the Referral row created once
    at registration by apps.referrals.services.register_referral) but
    progress completely independently — reversing one does not reverse
    the other, and a referral BLOCKED for the points system (anti-farming
    at registration) is also excluded here, since that block is about the
    relationship itself, not which reward track is paying out.

    One row per qualifying PAYMENT, not per referred user — a referred
    user who upgrades five times over several years has five
    ReferralCommission rows, each independently progressing
    PENDING -> APPROVED -> PAID (or REVERSED / FAILED_PAYOUT).
    source_payment_reference is unique per provider transaction
    (WalletTransaction.tx_ref) — the idempotency guard against webhook
    retries creating a duplicate commission for the same payment.
    """
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_PAID = "paid"
    STATUS_REVERSED = "reversed"
    STATUS_FAILED_PAYOUT = "failed_payout"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_PAID, "Paid"),
        (STATUS_REVERSED, "Reversed"),
        (STATUS_FAILED_PAYOUT, "Failed payout"),
    ]

    referrer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referral_commissions_earned",
    )
    referred_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referral_commissions_generated",
    )
    referral = models.ForeignKey(Referral, on_delete=models.CASCADE, related_name="commissions")
    subscription = models.ForeignKey(
        Subscription, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    source_payment_reference = models.CharField(max_length=255, unique=True)
    source_provider = models.CharField(max_length=32)

    gross_amount_cents = models.BigIntegerField()
    commission_rate_percent = models.DecimalField(max_digits=5, decimal_places=2)
    commission_amount_cents = models.BigIntegerField()
    currency = models.CharField(max_length=8, default="USD")

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_reason = models.CharField(max_length=255, blank=True, default="")

    payout_provider = models.CharField(max_length=16, blank=True, default="")
    # The provider's OWN transfer id, captured only once a provider call
    # returns successfully — may legitimately stay empty forever if the
    # HTTP response was lost even though the provider processed the
    # transfer (see payout_attempt_reference below for why that's still
    # recoverable).
    payout_reference = models.CharField(max_length=255, blank=True, default="")
    # OUR deterministic reference for the current/most recent payout
    # attempt (f"referral_commission:{id}:{payout_attempts}") — persisted
    # BEFORE the provider is ever called, in its own short transaction,
    # specifically so a crash between "provider accepted the transfer"
    # and "we saved payout_reference" still leaves durable evidence of
    # which reference may be in flight. Used to: (a) reuse the SAME
    # reference (and provider-side idempotency key) on a retry rather
    # than generating a fresh one that could create a real duplicate
    # transfer, (b) look the transfer up directly with the provider
    # before retrying, and (c) correlate an incoming webhook by the
    # reference WE sent rather than only by payout_reference, which the
    # webhook case above may have no value for. See
    # apps.referrals.commission_services.initiate_commission_payout and
    # docs/referrals/REFERRAL_COMMISSION_SYSTEM.md for the full reasoning.
    payout_attempt_reference = models.CharField(max_length=255, blank=True, default="")
    payout_failure_reason = models.CharField(max_length=255, blank=True, default="")
    payout_attempts = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["referrer", "status"]),
            models.Index(fields=["status", "approved_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.referrer_id} earns {self.commission_amount_cents}c from {self.referred_user_id} ({self.status})"
