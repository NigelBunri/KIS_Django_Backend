# apps/prayer/services.py
"""Backend-authoritative scheduling, leadership, and prayer-point logic
for the Prayer Calendar. Every important rule here is enforced with a
database transaction + row lock, never a client-side check — see each
function's own docstring for exactly what it guards against.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import AuditLog, User
from apps.groups.models import Group, GroupMembership, GroupRole
from apps.partners.permissions import is_platform_go

from .models import (
    PrayerDailySession,
    PrayerLeaderKind,
    PrayerLeadershipEvent,
    PrayerLeadershipEventKind,
    PrayerParticipationMode,
    PrayerPointSubmission,
    PrayerSessionStatus,
    PrayerWatchGroup,
)

MAX_MEMBER_LEADERSHIP_SESSIONS_PER_MONTH = 4

# Fixed, non-negotiable session structure — (key, label, display_text,
# start_offset_minutes, end_offset_minutes). Ordinary members and prayer
# leaders cannot modify this; only reflected here and in the mobile
# client's rendering of it.
PRAYER_STAGES = [
    (
        "praying_in_the_spirit",
        "Praying in the Spirit",
        "The first 30 minutes are dedicated to praying in the Spirit, including praying in tongues.",
        0, 30,
    ),
    (
        "members_prayer_points",
        "Members' Prayer Points",
        "The next 15 minutes are dedicated to specific prayer points submitted by members.",
        30, 45,
    ),
    (
        "prayer_for_kis",
        "Prayer for KIS",
        "Let us pray for Kingdom Impact Social, its leadership, members, mission, protection, growth and impact.",
        45, 50,
    ),
    (
        "thanksgiving",
        "Thanksgiving",
        "Let us give thanks to God for His goodness, faithfulness and answered prayers.",
        50, 55,
    ),
    (
        "declarations_and_blessings",
        "Declarations and Blessings",
        "Let us speak words of faith, blessing, encouragement and the promises of God's Word.",
        55, 60,
    ),
]
SESSION_DURATION_MINUTES = 60


class LeadershipError(Exception):
    """Base for every leadership-claim/share/release rejection — callers
    (views) translate `.code` into the exact button-state the mobile spec
    asks for (Already Taken / Monthly limit reached / Session closed /
    etc.) rather than a generic error string."""
    code = "leadership_error"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code


def utc_now() -> datetime.datetime:
    return timezone.now()


def watch_session_window(watch_group: PrayerWatchGroup, session_date: datetime.date) -> tuple[datetime.datetime, datetime.datetime]:
    """The one-hour UTC window for this watch on this UTC calendar date —
    the single authoritative source every stage/status calculation reads
    from. Never derived from a device's local clock."""
    start = datetime.datetime(
        session_date.year, session_date.month, session_date.day, watch_group.hour, 0, 0,
        tzinfo=datetime.timezone.utc,
    )
    end = start + datetime.timedelta(minutes=SESSION_DURATION_MINUTES)
    return start, end


def get_or_create_session_for_watch(watch_group: PrayerWatchGroup, *, at: datetime.datetime | None = None) -> PrayerDailySession:
    """Idempotently ensures today's (UTC) session row exists for this
    watch and opportunistically advances its cached status/timestamps to
    match what the schedule says `at` should be — never by replaying
    every missed transition, just by comparing `at` against
    scheduled_start/scheduled_end directly. Safe to call on every read;
    get_or_create + the unique_together(watch_group, session_date)
    constraint make concurrent calls (two requests arriving at the same
    moment) create at most one row."""
    at = at or utc_now()
    session_date = at.date()
    start, end = watch_session_window(watch_group, session_date)
    session, _created = PrayerDailySession.objects.get_or_create(
        watch_group=watch_group,
        session_date=session_date,
        defaults={"scheduled_start": start, "scheduled_end": end},
    )

    if session.status == PrayerSessionStatus.INTERRUPTED:
        # A human administrative decision — never silently overridden by
        # the schedule continuing to tick forward underneath it.
        return session

    update_fields = []
    if at >= start and session.status == PrayerSessionStatus.SCHEDULED:
        session.status = PrayerSessionStatus.ACTIVE
        session.actual_started_at = session.actual_started_at or at
        update_fields += ["status", "actual_started_at"]
    if at >= end and session.status in (PrayerSessionStatus.SCHEDULED, PrayerSessionStatus.ACTIVE):
        session.status = PrayerSessionStatus.COMPLETED
        session.completed_at = session.completed_at or end
        update_fields += ["status", "completed_at"]
    if update_fields:
        session.save(update_fields=[*{*update_fields}, "updated_at"])
    return session


@dataclass
class StageInfo:
    session: PrayerDailySession
    session_status: str
    stage_key: str | None
    stage_label: str | None
    stage_display_text: str | None
    stage_started_at: datetime.datetime | None
    stage_ends_at: datetime.datetime | None
    seconds_remaining_in_stage: int | None
    seconds_remaining_in_session: int | None


def compute_stage(session: PrayerDailySession, *, at: datetime.datetime | None = None) -> StageInfo:
    """Pure function: given a session row and the current authoritative
    time, returns exactly which of the five fixed stages is active (or
    None if the session hasn't started yet or has already ended) plus
    its start/end timestamps. The client renders a countdown FROM these
    timestamps — it never advances the stage itself, and reconnecting
    mid-stage (or opening the app for the first time mid-stage) always
    recomputes the correct answer from here rather than resuming
    anything client-side."""
    at = at or utc_now()

    if session.status == PrayerSessionStatus.INTERRUPTED:
        return StageInfo(session, PrayerSessionStatus.INTERRUPTED, None, None, None, None, None, None, None)

    elapsed_minutes = (at - session.scheduled_start).total_seconds() / 60.0

    if elapsed_minutes < 0:
        return StageInfo(session, PrayerSessionStatus.SCHEDULED, None, None, None, None, None, None, None)
    if elapsed_minutes >= SESSION_DURATION_MINUTES:
        return StageInfo(session, PrayerSessionStatus.COMPLETED, None, None, None, None, None, None, None)

    for key, label, display_text, start_offset, end_offset in PRAYER_STAGES:
        if start_offset <= elapsed_minutes < end_offset:
            stage_start = session.scheduled_start + datetime.timedelta(minutes=start_offset)
            stage_end = session.scheduled_start + datetime.timedelta(minutes=end_offset)
            return StageInfo(
                session=session,
                session_status=PrayerSessionStatus.ACTIVE,
                stage_key=key,
                stage_label=label,
                stage_display_text=display_text,
                stage_started_at=stage_start,
                stage_ends_at=stage_end,
                seconds_remaining_in_stage=max(0, int((stage_end - at).total_seconds())),
                seconds_remaining_in_session=max(0, int((session.scheduled_end - at).total_seconds())),
            )
    # Unreachable given the bounds above, but fail safe rather than raise.
    return StageInfo(session, PrayerSessionStatus.ACTIVE, None, None, None, None, None, None, None)


def active_watch_hour(*, at: datetime.datetime | None = None) -> int:
    """Exactly one watch is active at any given UTC moment — the 24
    one-hour windows are back-to-back with no gap, so this is simply the
    current UTC hour."""
    at = at or utc_now()
    return at.hour


def next_watch_hour(*, at: datetime.datetime | None = None) -> tuple[int, datetime.datetime]:
    """Returns (hour, scheduled_start_of_that_watch) for the next watch
    to begin — the current one if it hasn't started yet is never
    returned here; this is strictly the *next* boundary."""
    at = at or utc_now()
    next_start = (at.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1))
    return next_start.hour, next_start


def monthly_leadership_count(user: User, *, at: datetime.datetime | None = None) -> int:
    """Global across all 24 watches, not per-watch — 'a member may lead a
    maximum of four times per calendar month' names no per-group scope.
    Counts PrayerDailySession rows directly (the authoritative session
    record), never a separate cached counter — a released-before-start
    claim clears `leader` back to null (see release_leadership) and so
    is correctly excluded here without any special-case logic."""
    at = at or utc_now()
    return PrayerDailySession.objects.filter(
        leader=user,
        leader_kind=PrayerLeaderKind.MEMBER,
        session_date__year=at.year,
        session_date__month=at.month,
    ).count()


def claim_leadership(*, session_id, user: User) -> PrayerDailySession:
    """The 'I AM LEADING' button's backend. Atomic: select_for_update on
    the session row is what makes two concurrent claims resolve to
    exactly one winner rather than a race — the loser gets a clear
    LeadershipError, never a silently-overwritten leader field.

    Rejects (LeadershipError.code):
    - "session_closed": session already completed/interrupted.
    - "already_taken": someone else already has the member-leader slot
      for this session.
    - "monthly_limit_reached": this user already has 4 this month.
    - "admin_leading": an admin already claimed this session solo — an
      ordinary member cannot create a second, competing main-leader slot
      (see claim rules in apps.prayer.services module docstring / spec
      section 8).
    """
    with transaction.atomic():
        session = PrayerDailySession.objects.select_for_update().get(pk=session_id)
        if session.status in (PrayerSessionStatus.COMPLETED, PrayerSessionStatus.INTERRUPTED):
            raise LeadershipError("This session is closed.", code="session_closed")
        if session.leader_id:
            if session.leader_id == user.id:
                return session  # idempotent re-claim by the same user
            raise LeadershipError("This session's leadership is already taken.", code="already_taken")

        if monthly_leadership_count(user, at=session.scheduled_start) >= MAX_MEMBER_LEADERSHIP_SESSIONS_PER_MONTH:
            raise LeadershipError(
                f"You've already led {MAX_MEMBER_LEADERSHIP_SESSIONS_PER_MONTH} sessions this month.",
                code="monthly_limit_reached",
            )

        session.leader = user
        session.leader_kind = PrayerLeaderKind.MEMBER
        session.save(update_fields=["leader", "leader_kind", "updated_at"])
        PrayerLeadershipEvent.objects.create(
            session=session, actor=user, target_user=user, kind=PrayerLeadershipEventKind.CLAIMED,
        )
    AuditLog.log(user, "prayer.leadership.claimed", {"session_id": str(session.id)})
    from .notifications import notify_leadership_claimed
    notify_leadership_claimed(session=session, user=user)
    return session


def release_leadership(*, session_id, user: User) -> PrayerDailySession:
    """Relinquish a claimed leadership slot — only before the session has
    actually started (status still SCHEDULED). Once ACTIVE, a leader
    cannot just silently clear the field and leave the room leaderless;
    they need the handover/admin-share path instead. Clearing the field
    here (rather than just logging a 'released' event) is what correctly
    excludes this from the monthly count — a changed mind costs nothing."""
    with transaction.atomic():
        session = PrayerDailySession.objects.select_for_update().get(pk=session_id)
        if session.leader_id != user.id:
            raise LeadershipError("You are not the leader of this session.", code="not_leader")
        if session.status != PrayerSessionStatus.SCHEDULED:
            raise LeadershipError(
                "This session has already started — ask an admin to arrange a handover.",
                code="session_already_started",
            )
        session.leader = None
        session.leader_kind = ""
        session.save(update_fields=["leader", "leader_kind", "updated_at"])
        PrayerLeadershipEvent.objects.create(
            session=session, actor=user, target_user=user, kind=PrayerLeadershipEventKind.RELEASED,
        )
    AuditLog.log(user, "prayer.leadership.released", {"session_id": str(session.id)})
    return session


def _require_group_admin_or_go(session: PrayerDailySession, user: User) -> None:
    if is_platform_go(user):
        return
    group = session.watch_group.group
    is_admin = GroupMembership.objects.filter(
        group=group, user=user, left_at__isnull=True, is_banned=False,
        role__in=(GroupRole.OWNER, GroupRole.ADMIN),
    ).exists()
    if not is_admin:
        raise LeadershipError("Only a group admin or the GO can do this.", code="not_authorized")


def claim_admin_solo_leadership(*, session_id, admin_user: User) -> PrayerDailySession:
    """An admin leading alone when no ordinary member has volunteered —
    counts as THE session's appointed leader (leader_kind=ADMIN), not an
    additional slot alongside a member leader. Rejected if an ordinary
    member already claimed the member-leader slot (admins join that as
    a SHARE request instead, see request_admin_share) or another admin
    already claimed solo leadership."""
    with transaction.atomic():
        session = PrayerDailySession.objects.select_for_update().get(pk=session_id)
        _require_group_admin_or_go(session, admin_user)
        if session.status in (PrayerSessionStatus.COMPLETED, PrayerSessionStatus.INTERRUPTED):
            raise LeadershipError("This session is closed.", code="session_closed")
        if session.leader_id:
            if session.leader_id == admin_user.id:
                return session
            raise LeadershipError("This session's leadership is already taken.", code="already_taken")

        session.leader = admin_user
        session.leader_kind = PrayerLeaderKind.ADMIN
        session.save(update_fields=["leader", "leader_kind", "updated_at"])
        PrayerLeadershipEvent.objects.create(
            session=session, actor=admin_user, target_user=admin_user,
            kind=PrayerLeadershipEventKind.ADMIN_LED_SOLO,
        )
    AuditLog.log(admin_user, "prayer.leadership.admin_led_solo", {"session_id": str(session.id)})
    return session


def request_admin_share(*, session_id, admin_user: User) -> PrayerDailySession:
    """'Admin selects Share this prayer watch' — only valid when an
    ORDINARY MEMBER already holds the leader slot (an admin leading
    solo IS the leader; there is nothing to 'share' with themselves).
    Does not change any state by itself — it just records the request;
    the member must accept via respond_admin_share before sharing_admin
    is actually set."""
    with transaction.atomic():
        session = PrayerDailySession.objects.select_for_update().get(pk=session_id)
        _require_group_admin_or_go(session, admin_user)
        if session.status in (PrayerSessionStatus.COMPLETED, PrayerSessionStatus.INTERRUPTED):
            raise LeadershipError("This session is closed.", code="session_closed")
        if not session.leader_id or session.leader_kind != PrayerLeaderKind.MEMBER:
            raise LeadershipError(
                "There is no member leader to share this session with yet.", code="no_member_leader",
            )
        if session.sharing_admin_id:
            if session.sharing_admin_id == admin_user.id:
                return session
            raise LeadershipError("Another admin has already requested to share this session.", code="share_already_requested")

        PrayerLeadershipEvent.objects.create(
            session=session, actor=admin_user, target_user=session.leader,
            kind=PrayerLeadershipEventKind.SHARE_REQUESTED,
        )
        member_user = session.leader
    from .notifications import notify_share_requested
    notify_share_requested(session=session, admin_user=admin_user, member_user=member_user)
    return session


def respond_admin_share(*, session_id, member_user: User, accept: bool) -> PrayerDailySession:
    """The member leader accepting/declining the most recent pending
    share request. Looks at the leadership-event log (not a separate
    'pending request' row) to find which admin most recently requested
    and hasn't since been accepted/declined — append-only audit trail
    doubling as the request queue, so there's exactly one source of
    truth for 'who asked, and did anyone already answer.'"""
    with transaction.atomic():
        session = PrayerDailySession.objects.select_for_update().get(pk=session_id)
        if session.leader_id != member_user.id or session.leader_kind != PrayerLeaderKind.MEMBER:
            raise LeadershipError("You are not this session's member leader.", code="not_leader")

        last_decision = session.leadership_events.filter(
            kind__in=(PrayerLeadershipEventKind.SHARE_ACCEPTED, PrayerLeadershipEventKind.SHARE_DECLINED),
        ).order_by("-created_at").first()
        pending_request = session.leadership_events.filter(
            kind=PrayerLeadershipEventKind.SHARE_REQUESTED,
        ).order_by("-created_at").first()
        if not pending_request or (last_decision and last_decision.created_at >= pending_request.created_at):
            raise LeadershipError("There is no pending share request.", code="no_pending_request")

        requesting_admin_id = pending_request.actor_id
        if accept:
            session.sharing_admin_id = requesting_admin_id
            session.sharing_admin_accepted_at = utc_now()
            session.save(update_fields=["sharing_admin", "sharing_admin_accepted_at", "updated_at"])
            kind = PrayerLeadershipEventKind.SHARE_ACCEPTED
        else:
            kind = PrayerLeadershipEventKind.SHARE_DECLINED
        PrayerLeadershipEvent.objects.create(
            session=session, actor=member_user, target_user_id=requesting_admin_id, kind=kind,
        )
    from .notifications import notify_share_responded
    requesting_admin = User.objects.filter(id=requesting_admin_id).first()
    if requesting_admin:
        notify_share_responded(session=session, admin_user=requesting_admin, member_user=member_user, accepted=accept)
    return session


def go_override_leader(*, session_id, go_user: User, new_leader: User | None, leader_kind: str = PrayerLeaderKind.ADMIN, note: str = "") -> PrayerDailySession:
    """The GO's explicit override — 'Override leadership assignments when
    necessary.' Never automatic: a vacated/problem session stays exactly
    as it is (open for text participation, per spec section 16) unless
    the GO takes this explicit action."""
    if not is_platform_go(go_user):
        raise LeadershipError("Only the GO can override leadership.", code="not_authorized")
    with transaction.atomic():
        session = PrayerDailySession.objects.select_for_update().get(pk=session_id)
        session.leader = new_leader
        session.leader_kind = leader_kind if new_leader else ""
        session.sharing_admin = None
        session.sharing_admin_accepted_at = None
        session.save(update_fields=["leader", "leader_kind", "sharing_admin", "sharing_admin_accepted_at", "updated_at"])
        PrayerLeadershipEvent.objects.create(
            session=session, actor=go_user, target_user=new_leader,
            kind=PrayerLeadershipEventKind.GO_OVERRIDE, note=note[:255],
        )
    AuditLog.log(go_user, "prayer.leadership.go_override", {"session_id": str(session.id), "note": note})
    from .notifications import notify_go_override
    notify_go_override(session=session, new_leader=new_leader)
    return session


def set_participation_mode(*, session_id, user: User, mode: str) -> PrayerDailySession:
    with transaction.atomic():
        session = PrayerDailySession.objects.select_for_update().get(pk=session_id)
        is_session_leader = user.id in (session.leader_id, session.sharing_admin_id)
        if not is_session_leader:
            _require_group_admin_or_go(session, user)
        if mode not in PrayerParticipationMode.values:
            raise LeadershipError("Invalid participation mode.", code="invalid_mode")
        session.participation_mode = mode
        session.save(update_fields=["participation_mode", "updated_at"])
    return session


# --- Prayer points -----------------------------------------------------

def prayer_points_window_open(session: PrayerDailySession, *, at: datetime.datetime | None = None) -> bool:
    """Submission/edit/delete is only allowed before the session begins
    (spec: 'Edit their own prayer point before the session begins') —
    once Stage 1 starts, points are locked for the leader to navigate
    during Stage 2 without the list changing underneath them."""
    at = at or utc_now()
    return at < session.scheduled_start


def submit_prayer_point(*, session_id, user: User, text: str) -> PrayerPointSubmission:
    text = (text or "").strip()
    if not text:
        raise LeadershipError("Prayer point text is required.", code="empty_text")
    if len(text) > 500:
        raise LeadershipError("Prayer point is too long.", code="too_long")
    session = PrayerDailySession.objects.get(pk=session_id)
    if not prayer_points_window_open(session):
        raise LeadershipError("Prayer point submissions are closed for this session.", code="submissions_closed")
    point, created = PrayerPointSubmission.objects.update_or_create(
        session=session, user=user, defaults={"text": text},
    )
    return point


def delete_prayer_point(*, session_id, user: User) -> None:
    session = PrayerDailySession.objects.get(pk=session_id)
    if not prayer_points_window_open(session):
        raise LeadershipError("Prayer point submissions are closed for this session.", code="submissions_closed")
    PrayerPointSubmission.objects.filter(session=session, user=user).delete()

