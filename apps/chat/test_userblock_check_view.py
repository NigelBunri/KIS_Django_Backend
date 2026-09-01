"""
UserBlockCheckView — the interpersonal-block check for standalone calls
(`standalone:<callId>`), which have no Django conversation record and so
skip ws_perms()/assertMember() entirely. Without this, a blocked user could
still ring the person who blocked them via NestJS's call.offer socket
handler even though blocking stops them everywhere else in chat. See
apps/chat/views.py ws_perms() for the equivalent check on real
conversations, and the docstring on UserBlockCheckView itself.

Run:
  python3 manage.py test apps.chat.test_userblock_check_view --keepdb -v 2
"""
import os
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.moderation.models import UserBlock

URL = "/api/v1/chat/internal/blocked-among/"


class UserBlockCheckViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(phone="+2348200000001", password="pw123456", country="NG")
        self.peer_a = User.objects.create_user(phone="+2348200000002", password="pw123456", country="NG")
        self.peer_b = User.objects.create_user(phone="+2348200000003", password="pw123456", country="NG")
        self._env_patch = patch.dict(
            os.environ, {"DJANGO_INTERNAL_TOKEN": "test-internal-token", "INTERNAL_SIGNATURE_REQUIRED": "0"},
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def _headers(self):
        return {"HTTP_X_INTERNAL_AUTH": "test-internal-token"}

    def test_returns_empty_when_no_block_exists(self):
        res = self.client.get(
            URL,
            {"userId": str(self.user.id), "otherUserIds": f"{self.peer_a.id},{self.peer_b.id}"},
            **self._headers(),
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["blockedUserIds"], [])

    def test_reports_a_user_the_caller_blocked(self):
        UserBlock.objects.create(blocker=self.user, blocked=self.peer_a)

        res = self.client.get(
            URL,
            {"userId": str(self.user.id), "otherUserIds": f"{self.peer_a.id},{self.peer_b.id}"},
            **self._headers(),
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["blockedUserIds"], [str(self.peer_a.id)])

    def test_reports_a_user_who_blocked_the_caller(self):
        UserBlock.objects.create(blocker=self.peer_a, blocked=self.user)

        res = self.client.get(
            URL,
            {"userId": str(self.user.id), "otherUserIds": f"{self.peer_a.id},{self.peer_b.id}"},
            **self._headers(),
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["blockedUserIds"], [str(self.peer_a.id)])

    def test_a_block_between_two_other_users_is_not_reported(self):
        UserBlock.objects.create(blocker=self.peer_a, blocked=self.peer_b)

        res = self.client.get(
            URL,
            {"userId": str(self.user.id), "otherUserIds": f"{self.peer_a.id},{self.peer_b.id}"},
            **self._headers(),
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["blockedUserIds"], [])

    def test_missing_params_return_empty_rather_than_error(self):
        res = self.client.get(URL, {"userId": str(self.user.id)}, **self._headers())

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["blockedUserIds"], [])

    def test_requires_internal_auth(self):
        res = self.client.get(
            URL, {"userId": str(self.user.id), "otherUserIds": str(self.peer_a.id)},
        )

        self.assertEqual(res.status_code, 401)
