"""
apps.accounts.tasks.purge_accounts_past_grace_period — the daily sweep that
actually hard-deletes accounts once their grace period (see
apps.accounts.views.schedule_account_deletion) has elapsed.

Run:
  python3 manage.py test apps.accounts.test_account_purge --keepdb -v 2
"""
import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import GDPRRequest
from apps.accounts.tasks import _purge_nest_chat_data, purge_accounts_past_grace_period

User = get_user_model()


class PurgeNestChatDataTests(TestCase):
    """Pure unit tests for the Django->Nest notification helper - no
    User.delete() cascade involved, so these stay green regardless of
    unrelated model/migration state elsewhere in the app graph."""

    def test_calls_post_to_nest_with_the_purge_messages_path(self):
        with patch("apps.chat.tasks._post_to_nest") as mock_post:
            _purge_nest_chat_data("user-123")

        mock_post.assert_called_once_with("users/user-123/purge-messages", {})

    def test_swallows_exceptions_from_the_nest_call(self):
        with patch("apps.chat.tasks._post_to_nest", side_effect=Exception("nest is down")):
            _purge_nest_chat_data("user-123")  # must not raise


class PurgeAccountsPastGracePeriodTests(TestCase):
    def _create_pending(self, phone, *, scheduled_for):
        user = User.objects.create_user(phone=phone, password="Whatever123!", country="CM")
        user.is_active = False
        user.is_deleted = True
        user.save(update_fields=["is_active", "is_deleted"])
        gdpr_request = GDPRRequest.objects.create(
            user=user, type="account_deletion", status="pending", scheduled_for=scheduled_for,
        )
        return user, gdpr_request

    def test_purges_accounts_whose_grace_period_has_elapsed(self):
        user, gdpr_request = self._create_pending(
            "+237670001111", scheduled_for=timezone.now() - datetime.timedelta(seconds=1),
        )

        result = purge_accounts_past_grace_period()

        self.assertEqual(result["purged"], 1)
        self.assertFalse(User.objects.filter(id=user.id).exists())
        # GDPRRequest.user is CASCADE, so the row is gone along with the
        # user it belonged to - the durable record of the purge is the
        # AuditLog "security.account.deletion_purged" event instead
        # (actor_id is a plain UUID there, not an FK, so it survives).
        self.assertFalse(GDPRRequest.objects.filter(id=gdpr_request.id).exists())

    def test_does_not_purge_accounts_still_within_grace_period(self):
        user, _ = self._create_pending(
            "+237670002222", scheduled_for=timezone.now() + datetime.timedelta(days=5),
        )

        result = purge_accounts_past_grace_period()

        self.assertEqual(result["purged"], 0)
        self.assertTrue(User.objects.filter(id=user.id).exists())

    def test_does_not_purge_cancelled_requests(self):
        user, gdpr_request = self._create_pending(
            "+237670003333", scheduled_for=timezone.now() - datetime.timedelta(seconds=1),
        )
        gdpr_request.status = "cancelled"
        gdpr_request.save(update_fields=["status"])

        result = purge_accounts_past_grace_period()

        self.assertEqual(result["purged"], 0)
        self.assertTrue(User.objects.filter(id=user.id).exists())

    def test_notifies_nest_to_purge_the_users_chat_data(self):
        user, _ = self._create_pending(
            "+237670006666", scheduled_for=timezone.now() - datetime.timedelta(seconds=1),
        )
        user_id = str(user.id)

        with patch("apps.chat.tasks._post_to_nest") as mock_post:
            purge_accounts_past_grace_period()

        mock_post.assert_called_once_with(f"users/{user_id}/purge-messages", {})

    def test_a_nest_notification_failure_does_not_block_the_purge(self):
        user, _ = self._create_pending(
            "+237670012121", scheduled_for=timezone.now() - datetime.timedelta(seconds=1),
        )

        with patch("apps.chat.tasks._post_to_nest", side_effect=Exception("nest is down")):
            result = purge_accounts_past_grace_period()

        self.assertEqual(result["purged"], 1)
        self.assertFalse(User.objects.filter(id=user.id).exists())

    def test_leaves_unrelated_active_accounts_alone(self):
        active_user = User.objects.create_user(
            phone="+237670004444", password="Whatever123!", country="CM",
        )
        due_user, _ = self._create_pending(
            "+237670005555", scheduled_for=timezone.now() - datetime.timedelta(seconds=1),
        )

        purge_accounts_past_grace_period()

        self.assertTrue(User.objects.filter(id=active_user.id).exists())
        self.assertFalse(User.objects.filter(id=due_user.id).exists())
