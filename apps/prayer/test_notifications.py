import datetime
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.groups.models import GroupRole
from apps.groups.services import reactivate_group_membership
from apps.notifications.models import Notification
from apps.partners.seed import GO_EMAIL, GO_PHONE

from . import services
from .models import PrayerWatchGroup
from .provisioning import provision_prayer_watches
from .tasks import send_upcoming_watch_reminders

_counter = 0


def _make_user(**kw) -> User:
    global _counter
    _counter += 1
    phone = kw.pop("phone", f"+23765102{_counter:04d}")
    return User.objects.create_user(phone=phone, password="x", country="CM", **kw)


def _make_go() -> User:
    return User.objects.create_user(phone=GO_PHONE, password="x", country="CM", email=GO_EMAIL)


def _tomorrow_midnight() -> datetime.datetime:
    """Exactly midnight UTC tomorrow — a safe anchor for watch0 (hour=0)
    specifically. `timezone.now() + timedelta(days=1)` preserves the
    current time-of-day, which for any hour earlier than right now (e.g.
    hour=0 checked at 10am UTC) lands AFTER that watch's window has
    already closed on that date - this suite hit that exact flake during
    development."""
    tomorrow = (timezone.now() + datetime.timedelta(days=1)).date()
    return datetime.datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, tzinfo=datetime.timezone.utc)


class NotificationWiringTests(TestCase):
    def setUp(self):
        self.go = _make_go()
        with patch("apps.prayer.provisioning.send_system_message_as_user", return_value={"ok": True}):
            provision_prayer_watches()
        self.watch0 = PrayerWatchGroup.objects.select_related("group").get(hour=0)

    def test_claiming_leadership_notifies_the_claimer(self):
        user = _make_user()
        at = _tomorrow_midnight()
        session = services.get_or_create_session_for_watch(self.watch0, at=at)
        services.claim_leadership(session_id=session.id, user=user)
        self.assertTrue(
            Notification.objects.filter(user_id=user.id, type="prayer.leadership_claimed").exists()
        )

    def test_share_request_notifies_the_member_leader(self):
        member = _make_user()
        admin = _make_user()
        reactivate_group_membership(self.watch0.group, admin, role=GroupRole.ADMIN)
        at = _tomorrow_midnight()
        session = services.get_or_create_session_for_watch(self.watch0, at=at)
        services.claim_leadership(session_id=session.id, user=member)
        services.request_admin_share(session_id=session.id, admin_user=admin)
        self.assertTrue(
            Notification.objects.filter(user_id=member.id, type="prayer.share_requested").exists()
        )

    def test_share_response_notifies_the_requesting_admin(self):
        member = _make_user()
        admin = _make_user()
        reactivate_group_membership(self.watch0.group, admin, role=GroupRole.ADMIN)
        at = _tomorrow_midnight()
        session = services.get_or_create_session_for_watch(self.watch0, at=at)
        services.claim_leadership(session_id=session.id, user=member)
        services.request_admin_share(session_id=session.id, admin_user=admin)
        services.respond_admin_share(session_id=session.id, member_user=member, accept=True)
        self.assertTrue(
            Notification.objects.filter(user_id=admin.id, type="prayer.share_responded").exists()
        )

    def test_go_override_notifies_the_new_leader(self):
        new_leader = _make_user()
        at = _tomorrow_midnight()
        session = services.get_or_create_session_for_watch(self.watch0, at=at)
        services.go_override_leader(session_id=session.id, go_user=self.go, new_leader=new_leader)
        self.assertTrue(
            Notification.objects.filter(user_id=new_leader.id, type="prayer.go_override").exists()
        )

    def test_a_notification_failure_never_breaks_the_leadership_transition(self):
        user = _make_user()
        at = _tomorrow_midnight()
        session = services.get_or_create_session_for_watch(self.watch0, at=at)
        with patch("apps.notifications.services.create_notification", side_effect=RuntimeError("notif down")):
            result = services.claim_leadership(session_id=session.id, user=user)
        self.assertEqual(result.leader_id, user.id)


class UpcomingWatchReminderTaskTests(TestCase):
    def setUp(self):
        self.go = _make_go()
        with patch("apps.prayer.provisioning.send_system_message_as_user", return_value={"ok": True}):
            provision_prayer_watches()

    def test_reminder_sent_to_unmuted_joined_members_within_window(self):
        watch5 = PrayerWatchGroup.objects.get(hour=5)
        member = _make_user()
        reactivate_group_membership(watch5.group, member)

        now = timezone.now()
        # Force watch5's session to start 7 minutes from "now" (inside the
        # 10-minute reminder window) by directly creating the row at the
        # right scheduled_start rather than depending on real wall-clock
        # alignment to an exact hour boundary.
        from .models import PrayerDailySession
        future_start = now + datetime.timedelta(minutes=7)
        PrayerDailySession.objects.create(
            watch_group=watch5, session_date=future_start.date(),
            scheduled_start=future_start, scheduled_end=future_start + datetime.timedelta(hours=1),
        )

        with patch("apps.prayer.tasks.timezone.now", return_value=now):
            result = send_upcoming_watch_reminders()

        self.assertIn(5, result["watches_matched"])
        self.assertTrue(
            Notification.objects.filter(user_id=member.id, type="prayer.watch_starting_soon").exists()
        )

    def test_muted_member_does_not_get_a_reminder(self):
        watch6 = PrayerWatchGroup.objects.get(hour=6)
        member = _make_user()
        membership, _ = reactivate_group_membership(watch6.group, member)
        membership.is_muted = True
        membership.save(update_fields=["is_muted"])

        now = timezone.now()
        from .models import PrayerDailySession
        future_start = now + datetime.timedelta(minutes=3)
        PrayerDailySession.objects.create(
            watch_group=watch6, session_date=future_start.date(),
            scheduled_start=future_start, scheduled_end=future_start + datetime.timedelta(hours=1),
        )

        with patch("apps.prayer.tasks.timezone.now", return_value=now):
            send_upcoming_watch_reminders()

        self.assertFalse(
            Notification.objects.filter(user_id=member.id, type="prayer.watch_starting_soon").exists()
        )

    def test_reminder_task_is_idempotent_across_repeated_runs(self):
        watch7 = PrayerWatchGroup.objects.get(hour=7)
        member = _make_user()
        reactivate_group_membership(watch7.group, member)

        now = timezone.now()
        from .models import PrayerDailySession
        future_start = now + datetime.timedelta(minutes=2)
        PrayerDailySession.objects.create(
            watch_group=watch7, session_date=future_start.date(),
            scheduled_start=future_start, scheduled_end=future_start + datetime.timedelta(hours=1),
        )

        with patch("apps.prayer.tasks.timezone.now", return_value=now):
            send_upcoming_watch_reminders()
            send_upcoming_watch_reminders()

        self.assertEqual(
            Notification.objects.filter(user_id=member.id, type="prayer.watch_starting_soon").count(), 1,
        )
