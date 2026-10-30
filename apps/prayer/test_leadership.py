import datetime
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.groups.models import GroupMembership, GroupRole
from apps.groups.services import reactivate_group_membership
from apps.partners.seed import GO_EMAIL, GO_PHONE

from . import services
from .models import PrayerDailySession, PrayerLeaderKind, PrayerLeadershipEvent, PrayerPointSubmission, PrayerSessionStatus, PrayerWatchGroup
from .provisioning import provision_prayer_watches

_counter = 0


def _make_user(**kw) -> User:
    global _counter
    _counter += 1
    phone = kw.pop("phone", f"+23765101{_counter:04d}")
    country = kw.pop("country", "CM")
    return User.objects.create_user(phone=phone, password="x", country=country, **kw)


def _make_go() -> User:
    return User.objects.create_user(phone=GO_PHONE, password="x", country="CM", email=GO_EMAIL)


def _tomorrow_at(hour: int, *, minus_minutes: int = 5) -> datetime.datetime:
    """A deterministic anchor time guaranteed to fall just BEFORE a given
    watch's next occurrence, regardless of what the real wall-clock time
    is when the suite happens to run — using bare timezone.now() against
    e.g. hour=0 is flaky (it's only 'fresh' during the literal first hour
    of the UTC day; this test suite genuinely hit that exact flake during
    development, hours after midnight UTC).

    Clamped so the buffer never crosses below hour:00 on the SAME base
    date — get_or_create_session_for_watch always derives session_date
    from `at.date()` itself, so for hour=0 subtracting any buffer at all
    rolls `at` onto the PREVIOUS date, whose own hour-0 window (also
    [00:00, 01:00)) has then already long passed by 23:5x — that's not a
    bug in the service, it's a real property of hour 0 specifically
    (there is no "before midnight on the same date" instant to anchor
    SCHEDULED to); the fix here is simply never offering a buffer hour 0
    can't have, not forcing a SCHEDULED state that hour can't be in."""
    now = timezone.now()
    base = (now + datetime.timedelta(days=1)).date()
    start = datetime.datetime(base.year, base.month, base.day, hour, 0, 0, tzinfo=datetime.timezone.utc)
    safe_minutes = min(minus_minutes, hour * 60)
    return start - datetime.timedelta(minutes=safe_minutes)


class PrayerTestBase(TestCase):
    def setUp(self):
        self.go = _make_go()
        with patch("apps.prayer.provisioning.send_system_message_as_user", return_value={"ok": True}):
            provision_prayer_watches()
        self.watch0 = PrayerWatchGroup.objects.select_related("group").get(hour=0)

    def _session_for(self, watch_group, *, at=None):
        return services.get_or_create_session_for_watch(watch_group, at=at or _tomorrow_at(watch_group.hour))


# --- Stage transitions (section 19: "Stage transitions follow the exact
# 30/15/5/5/5 schedule", "Reconnecting users see the correct current stage") ---

class StageTransitionTests(PrayerTestBase):
    def test_full_stage_schedule(self):
        session = self._session_for(self.watch0)
        start = session.scheduled_start
        cases = [
            (0, "praying_in_the_spirit"), (29, "praying_in_the_spirit"),
            (30, "members_prayer_points"), (44, "members_prayer_points"),
            (45, "prayer_for_kis"), (49, "prayer_for_kis"),
            (50, "thanksgiving"), (54, "thanksgiving"),
            (55, "declarations_and_blessings"), (59, "declarations_and_blessings"),
        ]
        for minutes, expected_key in cases:
            info = services.compute_stage(session, at=start + datetime.timedelta(minutes=minutes))
            self.assertEqual(info.stage_key, expected_key, f"at +{minutes}min")

    def test_session_completed_at_60_minutes(self):
        session = self._session_for(self.watch0)
        info = services.compute_stage(session, at=session.scheduled_start + datetime.timedelta(minutes=60))
        self.assertEqual(info.session_status, PrayerSessionStatus.COMPLETED)

    def test_reconnecting_midway_sees_correct_stage_not_a_restart(self):
        session = self._session_for(self.watch0)
        mid_session_time = session.scheduled_start + datetime.timedelta(minutes=47)
        # Simulate "reconnecting" by just calling compute_stage again fresh.
        info_a = services.compute_stage(session, at=mid_session_time)
        info_b = services.compute_stage(session, at=mid_session_time)
        self.assertEqual(info_a.stage_key, info_b.stage_key)
        self.assertEqual(info_a.stage_key, "prayer_for_kis")

    def test_delayed_task_jumps_straight_to_correct_stage_without_replay(self):
        watch_group = self.watch0
        future_date = (timezone.now() + datetime.timedelta(days=2)).date()
        # 40 minutes into watch0's (hour 0) window -> members_prayer_points
        # stage (30-45min) - simulates a scheduler task that was delayed by
        # two days and a bit, landing mid-stage rather than at a boundary.
        far_future = datetime.datetime(future_date.year, future_date.month, future_date.day, 0, 40, tzinfo=datetime.timezone.utc)
        session = services.get_or_create_session_for_watch(watch_group, at=far_future)
        self.assertEqual(session.session_date, far_future.date())
        info = services.compute_stage(session, at=far_future)
        self.assertEqual(info.stage_key, "members_prayer_points")


# --- Leadership claim rules ---

class LeadershipClaimTests(PrayerTestBase):
    def test_member_can_claim_available_leadership(self):
        user = _make_user()
        session = self._session_for(self.watch0)
        result = services.claim_leadership(session_id=session.id, user=user)
        self.assertEqual(result.leader_id, user.id)
        self.assertEqual(result.leader_kind, PrayerLeaderKind.MEMBER)

    def test_second_member_cannot_claim_the_same_session(self):
        session = self._session_for(self.watch0)
        services.claim_leadership(session_id=session.id, user=_make_user())
        with self.assertRaises(services.LeadershipError) as ctx:
            services.claim_leadership(session_id=session.id, user=_make_user())
        self.assertEqual(ctx.exception.code, "already_taken")

    def test_reclaiming_your_own_session_is_a_safe_noop(self):
        user = _make_user()
        session = self._session_for(self.watch0)
        services.claim_leadership(session_id=session.id, user=user)
        result = services.claim_leadership(session_id=session.id, user=user)
        self.assertEqual(result.leader_id, user.id)

    def test_fourth_monthly_session_allowed_fifth_rejected(self):
        user = _make_user()
        for hour in [1, 2, 3, 4]:
            watch = PrayerWatchGroup.objects.get(hour=hour)
            session = self._session_for(watch)  # each uses its own safe per-hour anchor, same "tomorrow" date
            services.claim_leadership(session_id=session.id, user=user)

        fifth_watch = PrayerWatchGroup.objects.get(hour=5)
        fifth_session = self._session_for(fifth_watch)
        with self.assertRaises(services.LeadershipError) as ctx:
            services.claim_leadership(session_id=fifth_session.id, user=user)
        self.assertEqual(ctx.exception.code, "monthly_limit_reached")

    def test_monthly_limit_resets_in_a_new_month(self):
        user = _make_user()
        this_month_date = timezone.now().date().replace(day=1)
        for hour in [1, 2, 3, 4]:
            watch = PrayerWatchGroup.objects.get(hour=hour)
            at = datetime.datetime(this_month_date.year, this_month_date.month, this_month_date.day, hour, 0, tzinfo=datetime.timezone.utc)
            session = self._session_for(watch, at=at)
            services.claim_leadership(session_id=session.id, user=user)

        if this_month_date.month == 12:
            next_month_date = this_month_date.replace(year=this_month_date.year + 1, month=1)
        else:
            next_month_date = this_month_date.replace(month=this_month_date.month + 1)
        watch5 = PrayerWatchGroup.objects.get(hour=5)
        next_month_at = datetime.datetime(next_month_date.year, next_month_date.month, next_month_date.day, 5, 0, tzinfo=datetime.timezone.utc)
        session5 = self._session_for(watch5, at=next_month_at)
        result = services.claim_leadership(session_id=session5.id, user=user)
        self.assertEqual(result.leader_id, user.id)

    def test_cancelled_release_before_start_does_not_consume_a_monthly_slot(self):
        user = _make_user()
        watch1 = PrayerWatchGroup.objects.get(hour=1)
        session1 = self._session_for(watch1)
        services.claim_leadership(session_id=session1.id, user=user)
        services.release_leadership(session_id=session1.id, user=user)

        self.assertEqual(services.monthly_leadership_count(user, at=session1.scheduled_start), 0)
        # And the slot is now claimable again by someone else or re-claimed.
        result = services.claim_leadership(session_id=session1.id, user=user)
        self.assertEqual(result.leader_id, user.id)

    def test_cannot_release_after_session_has_started(self):
        user = _make_user()
        session = self._session_for(self.watch0)
        services.claim_leadership(session_id=session.id, user=user)
        # Force it active.
        session.status = PrayerSessionStatus.ACTIVE
        session.save(update_fields=["status"])
        with self.assertRaises(services.LeadershipError) as ctx:
            services.release_leadership(session_id=session.id, user=user)
        self.assertEqual(ctx.exception.code, "session_already_started")

    def test_closed_session_cannot_be_claimed(self):
        session = self._session_for(self.watch0)
        session.status = PrayerSessionStatus.COMPLETED
        session.save(update_fields=["status"])
        with self.assertRaises(services.LeadershipError) as ctx:
            services.claim_leadership(session_id=session.id, user=_make_user())
        self.assertEqual(ctx.exception.code, "session_closed")

    def test_failed_attempt_does_not_consume_a_monthly_slot(self):
        user = _make_user()
        watch1 = PrayerWatchGroup.objects.get(hour=1)
        session1 = self._session_for(watch1)
        services.claim_leadership(session_id=session1.id, user=_make_user())  # someone else wins it

        with self.assertRaises(services.LeadershipError):
            services.claim_leadership(session_id=session1.id, user=user)  # user's attempt fails

        self.assertEqual(services.monthly_leadership_count(user, at=session1.scheduled_start), 0)


class ConcurrentLeadershipClaimTests(TransactionTestCase):
    def setUp(self):
        self.go = _make_go()
        with patch("apps.prayer.provisioning.send_system_message_as_user", return_value={"ok": True}):
            provision_prayer_watches()

    def test_concurrent_claims_appoint_exactly_one_leader(self):
        import threading

        watch = PrayerWatchGroup.objects.get(hour=0)
        session = services.get_or_create_session_for_watch(watch, at=_tomorrow_at(0))
        users = [_make_user() for _ in range(5)]
        results = []
        errors = []

        def _attempt(u):
            try:
                results.append(services.claim_leadership(session_id=session.id, user=u))
            except services.LeadershipError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_attempt, args=(u,)) for u in users]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 1, "exactly one concurrent claim should succeed")
        self.assertEqual(len(errors), 4)
        final = PrayerDailySession.objects.get(pk=session.id)
        self.assertIn(final.leader_id, [u.id for u in users])


# --- Admin leadership rules ---

class AdminLeadershipTests(PrayerTestBase):
    def _make_group_admin(self, watch_group):
        admin = _make_user()
        membership, _ = reactivate_group_membership(watch_group.group, admin, role=GroupRole.ADMIN)
        return admin

    def test_admin_can_lead_when_no_volunteer(self):
        admin = self._make_group_admin(self.watch0)
        session = self._session_for(self.watch0)
        result = services.claim_admin_solo_leadership(session_id=session.id, admin_user=admin)
        self.assertEqual(result.leader_id, admin.id)
        self.assertEqual(result.leader_kind, PrayerLeaderKind.ADMIN)

    def test_non_admin_cannot_claim_admin_solo_leadership(self):
        session = self._session_for(self.watch0)
        with self.assertRaises(services.LeadershipError) as ctx:
            services.claim_admin_solo_leadership(session_id=session.id, admin_user=_make_user())
        self.assertEqual(ctx.exception.code, "not_authorized")

    def test_go_can_always_claim_admin_solo_leadership(self):
        session = self._session_for(self.watch0)
        result = services.claim_admin_solo_leadership(session_id=session.id, admin_user=self.go)
        self.assertEqual(result.leader_id, self.go.id)

    def test_second_admin_cannot_create_a_competing_main_leader_slot(self):
        admin1 = self._make_group_admin(self.watch0)
        admin2 = self._make_group_admin(self.watch0)
        session = self._session_for(self.watch0)
        services.claim_admin_solo_leadership(session_id=session.id, admin_user=admin1)
        with self.assertRaises(services.LeadershipError) as ctx:
            services.claim_admin_solo_leadership(session_id=session.id, admin_user=admin2)
        self.assertEqual(ctx.exception.code, "already_taken")

    def test_admin_cannot_claim_when_a_member_already_leads(self):
        member = _make_user()
        admin = self._make_group_admin(self.watch0)
        session = self._session_for(self.watch0)
        services.claim_leadership(session_id=session.id, user=member)
        with self.assertRaises(services.LeadershipError) as ctx:
            services.claim_admin_solo_leadership(session_id=session.id, admin_user=admin)
        self.assertEqual(ctx.exception.code, "already_taken")

    def test_admin_solo_leadership_does_not_count_toward_member_monthly_limit(self):
        admin = self._make_group_admin(self.watch0)
        last_session = None
        for hour in range(5):
            watch = PrayerWatchGroup.objects.get(hour=hour)
            reactivate_group_membership(watch.group, admin, role=GroupRole.ADMIN)
            session = self._session_for(watch)
            services.claim_admin_solo_leadership(session_id=session.id, admin_user=admin)
            last_session = session
        self.assertEqual(services.monthly_leadership_count(admin, at=last_session.scheduled_start), 0)


class AdminShareTests(PrayerTestBase):
    def setUp(self):
        super().setUp()
        self.member = _make_user()
        self.admin = _make_user()
        reactivate_group_membership(self.watch0.group, self.admin, role=GroupRole.ADMIN)
        self.session = self._session_for(self.watch0)
        services.claim_leadership(session_id=self.session.id, user=self.member)

    def test_share_request_then_accept_sets_sharing_admin(self):
        services.request_admin_share(session_id=self.session.id, admin_user=self.admin)
        result = services.respond_admin_share(session_id=self.session.id, member_user=self.member, accept=True)
        self.assertEqual(result.sharing_admin_id, self.admin.id)
        self.assertIsNotNone(result.sharing_admin_accepted_at)

    def test_share_request_then_decline_leaves_sharing_admin_unset(self):
        services.request_admin_share(session_id=self.session.id, admin_user=self.admin)
        result = services.respond_admin_share(session_id=self.session.id, member_user=self.member, accept=False)
        self.assertIsNone(result.sharing_admin_id)

    def test_cannot_respond_without_a_pending_request(self):
        with self.assertRaises(services.LeadershipError) as ctx:
            services.respond_admin_share(session_id=self.session.id, member_user=self.member, accept=True)
        self.assertEqual(ctx.exception.code, "no_pending_request")

    def test_cannot_share_request_when_leader_is_already_an_admin(self):
        watch1 = PrayerWatchGroup.objects.get(hour=1)
        session1 = self._session_for(watch1)
        reactivate_group_membership(watch1.group, self.admin, role=GroupRole.ADMIN)
        services.claim_admin_solo_leadership(session_id=session1.id, admin_user=self.admin)
        another_admin = _make_user()
        reactivate_group_membership(watch1.group, another_admin, role=GroupRole.ADMIN)
        with self.assertRaises(services.LeadershipError) as ctx:
            services.request_admin_share(session_id=session1.id, admin_user=another_admin)
        self.assertEqual(ctx.exception.code, "no_member_leader")

    def test_leadership_events_record_the_full_handover_audit_trail(self):
        services.request_admin_share(session_id=self.session.id, admin_user=self.admin)
        services.respond_admin_share(session_id=self.session.id, member_user=self.member, accept=True)
        kinds = list(
            PrayerLeadershipEvent.objects.filter(session=self.session).order_by("created_at").values_list("kind", flat=True)
        )
        self.assertEqual(kinds, ["claimed", "share_requested", "share_accepted"])


class GoOverrideTests(PrayerTestBase):
    def test_go_can_override_leadership(self):
        session = self._session_for(self.watch0)
        services.claim_leadership(session_id=session.id, user=_make_user())
        new_leader = _make_user()
        result = services.go_override_leader(
            session_id=session.id, go_user=self.go, new_leader=new_leader, note="original leader unreachable",
        )
        self.assertEqual(result.leader_id, new_leader.id)

    def test_non_go_cannot_override(self):
        session = self._session_for(self.watch0)
        with self.assertRaises(services.LeadershipError) as ctx:
            services.go_override_leader(session_id=session.id, go_user=_make_user(), new_leader=None)
        self.assertEqual(ctx.exception.code, "not_authorized")


# --- Prayer points ---

class PrayerPointTests(PrayerTestBase):
    def test_submit_edit_and_delete_before_session_starts(self):
        user = _make_user()
        session = self._session_for(self.watch0)
        point = services.submit_prayer_point(session_id=session.id, user=user, text="Pray for my family")
        self.assertEqual(point.text, "Pray for my family")

        updated = services.submit_prayer_point(session_id=session.id, user=user, text="Pray for healing")
        self.assertEqual(updated.id, point.id)
        self.assertEqual(updated.text, "Pray for healing")

        services.delete_prayer_point(session_id=session.id, user=user)
        self.assertFalse(PrayerPointSubmission.objects.filter(session=session, user=user).exists())

    def test_submissions_closed_once_session_starts(self):
        # The submission window is judged against real wall-clock time vs
        # scheduled_start (prayer_points_window_open), not the cached
        # `status` field — so to genuinely close it this session's
        # scheduled_start must actually be in the past, not just have its
        # status field forced.
        session = self._session_for(self.watch0)
        session.scheduled_start = timezone.now() - datetime.timedelta(minutes=10)
        session.status = PrayerSessionStatus.ACTIVE
        session.save(update_fields=["scheduled_start", "status"])
        with self.assertRaises(services.LeadershipError) as ctx:
            services.submit_prayer_point(session_id=session.id, user=_make_user(), text="Too late")
        self.assertEqual(ctx.exception.code, "submissions_closed")

    def test_prayer_points_are_scoped_to_the_correct_watch_and_session(self):
        watch1 = PrayerWatchGroup.objects.get(hour=1)
        session0 = self._session_for(self.watch0)
        session1 = self._session_for(watch1)
        user = _make_user()
        services.submit_prayer_point(session_id=session0.id, user=user, text="For watch 0")
        services.submit_prayer_point(session_id=session1.id, user=user, text="For watch 1")

        self.assertEqual(PrayerPointSubmission.objects.filter(session=session0).count(), 1)
        self.assertEqual(PrayerPointSubmission.objects.filter(session=session1).count(), 1)
        self.assertEqual(PrayerPointSubmission.objects.get(session=session0).text, "For watch 0")

    def test_one_prayer_point_per_user_per_session_prevents_spam(self):
        user = _make_user()
        session = self._session_for(self.watch0)
        services.submit_prayer_point(session_id=session.id, user=user, text="First")
        services.submit_prayer_point(session_id=session.id, user=user, text="Second")
        self.assertEqual(PrayerPointSubmission.objects.filter(session=session, user=user).count(), 1)


# --- API-level auth/permission checks ---

class PrayerApiPermissionTests(PrayerTestBase):
    def test_official_call_mode_cannot_be_set_by_a_non_leader(self):
        session = self._session_for(self.watch0)
        services.claim_leadership(session_id=session.id, user=_make_user())
        outsider = _make_user()
        with self.assertRaises(services.LeadershipError) as ctx:
            services.set_participation_mode(session_id=session.id, user=outsider, mode="voice")
        self.assertEqual(ctx.exception.code, "not_authorized")

    def test_leader_can_set_participation_mode(self):
        leader = _make_user()
        session = self._session_for(self.watch0)
        services.claim_leadership(session_id=session.id, user=leader)
        result = services.set_participation_mode(session_id=session.id, user=leader, mode="video")
        self.assertEqual(result.participation_mode, "video")

    def test_watches_endpoint_requires_authentication(self):
        client = APIClient()
        response = client.get("/api/v1/prayer/watches/")
        self.assertEqual(response.status_code, 401)

    def test_watches_list_returns_all_24(self):
        client = APIClient()
        client.force_authenticate(user=_make_user())
        response = client.get("/api/v1/prayer/watches/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 24)

    def test_watch_admins_endpoint_is_go_only(self):
        client = APIClient()
        non_go = _make_user()
        client.force_authenticate(user=non_go)
        response = client.post(f"/api/v1/prayer/watches/0/admins/", {"user_id": str(non_go.id)}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_go_can_assign_a_prayer_watch_admin(self):
        client = APIClient()
        client.force_authenticate(user=self.go)
        target = _make_user()
        response = client.post("/api/v1/prayer/watches/0/admins/", {"user_id": str(target.id)}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            GroupMembership.objects.filter(group=self.watch0.group, user=target, role=GroupRole.ADMIN).exists()
        )

    def test_kis_announcer_identity_cannot_be_impersonated_via_display_name_change(self):
        # The announcer identity is resolved server-side by a fixed
        # email/username lookup (apps.prayer.provisioning.
        # get_kis_system_user) — an ordinary user changing their own
        # display_name to "KIS" has no effect on which account is
        # actually used to post welcome messages or on anything this API
        # returns as the announcer.
        impostor = _make_user(display_name="KIS")
        from .provisioning import get_kis_system_user
        real_kis = get_kis_system_user()
        self.assertNotEqual(impostor.id, real_kis.id)
