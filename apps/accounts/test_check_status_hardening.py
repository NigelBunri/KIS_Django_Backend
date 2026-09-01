"""
UserViewSet.check_status stays AllowAny/unauthenticated deliberately (a
device needs it before it has a session), but it used to be unthrottled
(THROTTLE_USER_CHECK_STATUS was defined in settings but never actually
wired onto the view) and returned phone/status/is_active/the full
verification blob for any phone number a caller supplied - an unlimited
anonymous phone-number-to-account-existence oracle. It also silently
ignored the `phone` param and returned the caller's own record when the
caller happened to be authenticated. See the SECURITY comment on
check_status in views.py.

Run:
  python3 manage.py test apps.accounts.test_check_status_hardening --keepdb -v 2
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()

URL = "/api/v1/users/check-status/"


class CheckStatusHardeningTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.target = User.objects.create_user(phone="+2348400000001", password="pw123456", country="NG")

    def test_missing_phone_is_a_400_not_a_lookup(self):
        res = self.client.get(URL)
        self.assertEqual(res.status_code, 400)

    def test_unknown_phone_returns_404_without_leaking_fields(self):
        res = self.client.get(URL, {"phone": "+2348400099999"})
        self.assertEqual(res.status_code, 404)

    def test_known_phone_returns_only_the_id_no_other_pii(self):
        res = self.client.get(URL, {"phone": self.target.phone})

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["success"], True)
        self.assertEqual(set(res.data["user"].keys()), {"id"})
        self.assertEqual(str(res.data["user"]["id"]), str(self.target.id))

    def test_an_authenticated_caller_still_gets_the_requested_phones_status_not_their_own(self):
        caller = User.objects.create_user(phone="+2348400000002", password="pw123456", country="NG")
        self.client.force_authenticate(caller)

        res = self.client.get(URL, {"phone": self.target.phone})

        self.assertEqual(res.status_code, 200)
        self.assertEqual(str(res.data["user"]["id"]), str(self.target.id))
        self.assertNotEqual(str(res.data["user"]["id"]), str(caller.id))

    def test_the_endpoint_actually_rejects_once_the_configured_rate_is_exceeded(self):
        # The original bug wasn't just "no throttling" - a
        # user_check_status rate was already defined in
        # DEFAULT_THROTTLE_RATES, but no view ever referenced it via
        # throttle_scope, so it was silent dead configuration. This proves
        # the scope is actually wired onto the view by exercising real
        # throttling behavior end-to-end, not by introspecting DRF internals.
        # Drive the real rate down to something a unit test can exhaust
        # quickly, rather than trusting the numeric config value alone.
        from rest_framework.throttling import ScopedRateThrottle

        original_rates = dict(ScopedRateThrottle.THROTTLE_RATES)
        ScopedRateThrottle.THROTTLE_RATES["user_check_status"] = "3/min"
        try:
            statuses = [
                self.client.get(URL, {"phone": self.target.phone}).status_code
                for _ in range(6)
            ]
        finally:
            ScopedRateThrottle.THROTTLE_RATES.clear()
            ScopedRateThrottle.THROTTLE_RATES.update(original_rates)

        self.assertIn(429, statuses)
