from __future__ import annotations

import secrets
import string

from django.conf import settings
from django.db import models

from apps.accounts.models import BaseEntity

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


class Referral(BaseEntity):
    """
    One row per referred user — the OneToOneField on referred_user means a
    person can be the *referred* party at most once, ever, enforced at the
    DB level regardless of how many times they re-register or which code
    they try to use later.
    """
    STATUS_PENDING = "pending"
    STATUS_REWARDED = "rewarded"
    STATUS_BLOCKED = "blocked"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_REWARDED, "Rewarded"),
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
    rewarded_at = models.DateTimeField(null=True, blank=True)
    block_reason = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["referrer", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.referrer_id} -> {self.referred_user_id} ({self.status})"
