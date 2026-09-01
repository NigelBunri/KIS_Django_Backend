# otp/tasks.py
#
# Thin @shared_task wrapper around a real, independently testable
# function - matching the established house pattern (apps.rewards.tasks /
# apps.billing.tasks). Registered in CELERY_BEAT_SCHEDULE,
# config/settings/base.py.
from __future__ import annotations

import datetime

from celery import shared_task
from django.utils import timezone

from .models import PhoneOTP

# Grace window past expires_at before a row is purged, rather than
# deleting the instant it expires - keeps a short buffer for any
# last-moment verification race or fraud-pattern review, without
# retaining phone numbers and OTP attempt history indefinitely for codes
# that have had no legitimate purpose since they expired.
OTP_PURGE_GRACE_HOURS = 24


def purge_expired_otp_codes() -> int:
    cutoff = timezone.now() - datetime.timedelta(hours=OTP_PURGE_GRACE_HOURS)
    deleted_count, _ = PhoneOTP.objects.filter(expires_at__lt=cutoff).delete()
    return deleted_count


@shared_task
def purge_expired_otp_codes_task():
    return {"deleted": purge_expired_otp_codes()}
