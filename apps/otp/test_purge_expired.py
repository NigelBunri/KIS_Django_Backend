"""
purge_expired_otp_codes_task - PhoneOTP rows (phone number + attempt
count, tied to a hashed but still purpose-identifying OTP code) had no
cleanup of any kind before this; every code ever issued stayed in the
table forever, well past the point it serves any purpose.

Run:
  python3 manage.py test apps.otp.test_purge_expired --keepdb -v 2
"""
import datetime

from django.test import TestCase
from django.utils import timezone

from .models import PhoneOTP
from .tasks import OTP_PURGE_GRACE_HOURS, purge_expired_otp_codes


class PurgeExpiredOtpCodesTests(TestCase):
    def _make(self, expires_at):
        return PhoneOTP.objects.create(
            phone="+2348700000001", purpose="login", code_hash="x", expires_at=expires_at,
        )

    def test_deletes_codes_past_the_grace_window(self):
        self._make(timezone.now() - datetime.timedelta(hours=OTP_PURGE_GRACE_HOURS + 1))

        deleted = purge_expired_otp_codes()

        self.assertEqual(deleted, 1)
        self.assertEqual(PhoneOTP.objects.count(), 0)

    def test_leaves_recently_expired_codes_within_the_grace_window(self):
        self._make(timezone.now() - datetime.timedelta(hours=1))

        deleted = purge_expired_otp_codes()

        self.assertEqual(deleted, 0)
        self.assertEqual(PhoneOTP.objects.count(), 1)

    def test_leaves_codes_not_yet_expired(self):
        self._make(timezone.now() + datetime.timedelta(minutes=5))

        deleted = purge_expired_otp_codes()

        self.assertEqual(deleted, 0)
        self.assertEqual(PhoneOTP.objects.count(), 1)
