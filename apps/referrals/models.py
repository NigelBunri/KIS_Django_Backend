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
