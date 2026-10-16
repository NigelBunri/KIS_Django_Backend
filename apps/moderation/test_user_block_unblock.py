"""
Regression coverage: UserBlockViewSet.create() only ever handled
*creating* a block. The mobile app's unblockContact()
(BlockedContactsScreen.tsx) has always POSTed
{blocked: userId, action: 'unblock'} to this same endpoint, but `action`
was never read here - an "unblock" request silently fell through to
get_or_create() and just found (or left) the existing block row, with no
way to ever actually remove it server-side. Every OTHER consumer of
UserBlock across the codebase (checkBlockedAmong for call invites,
broadcasts/communities/statuses visibility, etc.) queries the server
directly and has no concept of the app's local AsyncStorage block list,
so those stayed permanently blocked even after the user "unblocked"
someone through the app.

Run:
  python3 manage.py test apps.moderation.test_user_block_unblock --keepdb -v 2
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from . import models

User = get_user_model()

URL = "/api/v1/user-blocks/"


class UserBlockUnblockTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.alice = User.objects.create_user(phone="+2348400000001", password="pw123456", country="NG")
        self.bob = User.objects.create_user(phone="+2348400000002", password="pw123456", country="NG")
        self.client.force_authenticate(self.alice)

    def test_blocking_creates_a_user_block_row(self):
        res = self.client.post(URL, {"blocked": str(self.bob.id)}, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertTrue(
            models.UserBlock.objects.filter(blocker=self.alice, blocked=self.bob).exists(),
        )

    def test_unblocking_actually_removes_the_block_row(self):
        models.UserBlock.objects.create(blocker=self.alice, blocked=self.bob)

        res = self.client.post(URL, {"blocked": str(self.bob.id), "action": "unblock"}, format="json")

        self.assertEqual(res.status_code, 200)
        self.assertFalse(
            models.UserBlock.objects.filter(blocker=self.alice, blocked=self.bob).exists(),
        )

    def test_unblocking_someone_never_blocked_is_a_harmless_no_op(self):
        res = self.client.post(URL, {"blocked": str(self.bob.id), "action": "unblock"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(
            models.UserBlock.objects.filter(blocker=self.alice, blocked=self.bob).exists(),
        )

    def test_re_blocking_after_unblocking_works_cleanly(self):
        models.UserBlock.objects.create(blocker=self.alice, blocked=self.bob)
        self.client.post(URL, {"blocked": str(self.bob.id), "action": "unblock"}, format="json")

        res = self.client.post(URL, {"blocked": str(self.bob.id)}, format="json")

        self.assertEqual(res.status_code, 201)
        self.assertTrue(
            models.UserBlock.objects.filter(blocker=self.alice, blocked=self.bob).exists(),
        )

    def test_unblocking_only_ever_affects_the_caller_as_blocker_not_the_reverse_direction(self):
        # Bob independently blocked Alice too - Alice unblocking Bob must
        # never touch Bob's own, separate block of Alice.
        models.UserBlock.objects.create(blocker=self.alice, blocked=self.bob)
        models.UserBlock.objects.create(blocker=self.bob, blocked=self.alice)

        self.client.post(URL, {"blocked": str(self.bob.id), "action": "unblock"}, format="json")

        self.assertFalse(models.UserBlock.objects.filter(blocker=self.alice, blocked=self.bob).exists())
        self.assertTrue(models.UserBlock.objects.filter(blocker=self.bob, blocked=self.alice).exists())
