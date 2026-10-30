from unittest.mock import patch

from django.test import TestCase

from apps.accounts.models import User
from apps.groups.models import GroupJoinPolicy, GroupMembership, GroupRole
from apps.partners.seed import GO_EMAIL, GO_PHONE

from .models import PrayerWatchGroup
from .provisioning import ProvisioningError, get_kis_system_user, provision_prayer_watches
from .watch_names import WATCH_NAMES


def _make_go_user() -> User:
    return User.objects.create_user(phone=GO_PHONE, password="x", country="CM", email=GO_EMAIL)


class KisAnnouncerIdentityTests(TestCase):
    """Regression test for a real bug found during this project: GO and
    KIS must be two genuinely distinct accounts. apps.partners.seed's
    own ensure_go_identity() reassigns the KCAN Partner's ownership to
    GO the instant GO exists — so naively reading
    Partner.objects.get(slug__in=LEGACY_DEFAULT_PARTNER_SLUGS).owner
    (what an earlier version of this code did) silently returns GO
    itself once GO has ever logged in, not a separate announcer."""

    def test_kis_announcer_is_not_the_go_account(self):
        go = _make_go_user()
        kis_user = get_kis_system_user()
        self.assertNotEqual(kis_user.id, go.id)
        self.assertEqual(kis_user.display_name, "KIS")
        self.assertFalse(kis_user.has_usable_password())  # can never be logged into

    def test_kis_announcer_lookup_is_idempotent(self):
        first = get_kis_system_user()
        second = get_kis_system_user()
        self.assertEqual(first.id, second.id)


class ProvisioningPreconditionTests(TestCase):
    def test_raises_a_clear_error_when_go_has_never_logged_in(self):
        with self.assertRaises(ProvisioningError):
            provision_prayer_watches(post_welcome_messages=False)


class ProvisioningTests(TestCase):
    def setUp(self):
        self.go_user = _make_go_user()

    @patch("apps.prayer.provisioning.send_system_message_as_user", return_value={"ok": True})
    def test_creates_exactly_24_watch_groups(self, mock_send):
        report = provision_prayer_watches()
        self.assertEqual(PrayerWatchGroup.objects.count(), 24)
        self.assertEqual(len(report["created"]), 24)
        self.assertEqual(sorted(h for h, _ in WATCH_NAMES), list(range(24)))

    @patch("apps.prayer.provisioning.send_system_message_as_user", return_value={"ok": True})
    def test_exact_names_and_open_join_policy(self, mock_send):
        provision_prayer_watches()
        for hour, name in WATCH_NAMES:
            watch = PrayerWatchGroup.objects.select_related("group").get(hour=hour)
            self.assertEqual(watch.name, name)
            self.assertEqual(watch.group.name, name)
            self.assertEqual(watch.group.join_policy, GroupJoinPolicy.OPEN)
            self.assertEqual(watch.group.owner_id, self.go_user.id)

    @patch("apps.prayer.provisioning.send_system_message_as_user", return_value={"ok": True})
    def test_go_and_kis_system_user_are_both_real_group_members(self, mock_send):
        provision_prayer_watches()
        kis_user = get_kis_system_user()
        self.assertNotEqual(kis_user.id, self.go_user.id, "KIS announcer must be a distinct account from GO")
        watch = PrayerWatchGroup.objects.select_related("group").get(hour=0)
        self.assertTrue(
            GroupMembership.objects.filter(group=watch.group, user=self.go_user, role=GroupRole.OWNER, left_at__isnull=True).exists()
        )
        self.assertTrue(
            GroupMembership.objects.filter(group=watch.group, user=kis_user, left_at__isnull=True).exists()
        )
        self.assertFalse(
            GroupMembership.objects.filter(group=watch.group, user=kis_user, role=GroupRole.OWNER).exists(),
            "KIS announcer must never be the group owner — only its announcer member",
        )

    @patch("apps.prayer.provisioning.send_system_message_as_user", return_value={"ok": True})
    def test_running_twice_creates_no_duplicates(self, mock_send):
        provision_prayer_watches()
        first_group_ids = set(PrayerWatchGroup.objects.values_list("group_id", flat=True))

        report = provision_prayer_watches()

        self.assertEqual(PrayerWatchGroup.objects.count(), 24)
        self.assertEqual(len(report["created"]), 0)
        self.assertEqual(len(report["already_existed"]), 24)
        second_group_ids = set(PrayerWatchGroup.objects.values_list("group_id", flat=True))
        self.assertEqual(first_group_ids, second_group_ids)

    @patch("apps.prayer.provisioning.send_system_message_as_user", return_value={"ok": True})
    def test_welcome_message_sent_with_deterministic_client_id_and_kis_sender(self, mock_send):
        provision_prayer_watches()
        kis_user = get_kis_system_user()
        watch = PrayerWatchGroup.objects.select_related("group").get(hour=0)

        matching_calls = [
            call for call in mock_send.call_args_list
            if call.kwargs["conversation_id"] == str(watch.group.conversation_id)
        ]
        self.assertEqual(len(matching_calls), 1)
        self.assertEqual(matching_calls[0].kwargs["sender_id"], str(kis_user.id))
        self.assertEqual(matching_calls[0].kwargs["client_id"], f"prayer-watch-welcome:{watch.group.conversation_id}")

    def test_welcome_message_failure_does_not_block_group_provisioning(self):
        with patch("apps.prayer.provisioning.send_system_message_as_user", side_effect=RuntimeError("Nest unreachable")):
            report = provision_prayer_watches()
        self.assertEqual(PrayerWatchGroup.objects.count(), 24)
        self.assertEqual(len(report["welcome_failed"]), 24)
        self.assertEqual(len(report["welcome_sent"]), 0)

    @patch("apps.prayer.provisioning.send_system_message_as_user", return_value={"ok": True})
    def test_skip_welcome_messages_flag(self, mock_send):
        provision_prayer_watches(post_welcome_messages=False)
        mock_send.assert_not_called()
        self.assertEqual(PrayerWatchGroup.objects.count(), 24)


