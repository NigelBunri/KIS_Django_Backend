# apps/prayer/views.py
from __future__ import annotations

import datetime

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.groups.models import GroupMembership, GroupRole
from apps.groups.services import reactivate_group_membership
from apps.partners.permissions import is_platform_go

from . import services
from .models import PrayerDailySession, PrayerPointSubmission, PrayerWatchGroup
from .serializers import (
    GroupAdminSerializer,
    MyActivitySerializer,
    PrayerPointSerializer,
    SubmitPrayerPointSerializer,
    WatchDetailSerializer,
    WatchSummarySerializer,
)

LEADERSHIP_STATE_AVAILABLE = "available"
LEADERSHIP_STATE_LEADING = "leading"
LEADERSHIP_STATE_ALREADY_TAKEN = "already_taken"
LEADERSHIP_STATE_MONTHLY_LIMIT_REACHED = "monthly_limit_reached"
LEADERSHIP_STATE_SESSION_CLOSED = "session_closed"


def _watch_or_404(hour: int) -> PrayerWatchGroup | None:
    return PrayerWatchGroup.objects.select_related("group", "group__conversation").filter(hour=hour).first()


def _is_group_admin(group, user) -> bool:
    if is_platform_go(user):
        return True
    return GroupMembership.objects.filter(
        group=group, user=user, left_at__isnull=True, is_banned=False,
        role__in=(GroupRole.OWNER, GroupRole.ADMIN),
    ).exists()


def _leader_dict(user, kind: str) -> dict | None:
    if not user:
        return None
    return {"id": str(user.id), "display_name": getattr(user, "display_name", "") or "KIS member", "kind": kind}


def _participant_count(group) -> int:
    return GroupMembership.objects.filter(group=group, left_at__isnull=True, is_banned=False).count()


def _watch_summary(watch_group: PrayerWatchGroup, *, at=None) -> dict:
    at = at or timezone.now()
    session = services.get_or_create_session_for_watch(watch_group, at=at)
    leader_user = session.leader
    sharing_user = session.sharing_admin
    return {
        "hour": watch_group.hour,
        "name": watch_group.name,
        "group_id": str(watch_group.group_id),
        "conversation_id": str(watch_group.group.conversation_id),
        "scheduled_start": session.scheduled_start,
        "scheduled_end": session.scheduled_end,
        "session_status": session.status,
        "is_current_watch": watch_group.hour == services.active_watch_hour(at=at),
        "leader": _leader_dict(leader_user, session.leader_kind) if leader_user else None,
        "sharing_admin": _leader_dict(sharing_user, "admin") if sharing_user else None,
        "participant_count": _participant_count(watch_group.group),
    }, session


def _my_leadership_state(session: PrayerDailySession, user) -> str:
    if session.status in ("completed", "interrupted"):
        return LEADERSHIP_STATE_SESSION_CLOSED
    if session.leader_id == user.id:
        return LEADERSHIP_STATE_LEADING
    if session.leader_id:
        return LEADERSHIP_STATE_ALREADY_TAKEN
    if services.monthly_leadership_count(user, at=session.scheduled_start) >= services.MAX_MEMBER_LEADERSHIP_SESSIONS_PER_MONTH:
        return LEADERSHIP_STATE_MONTHLY_LIMIT_REACHED
    return LEADERSHIP_STATE_AVAILABLE


def _pending_share_request(session: PrayerDailySession) -> dict | None:
    from .models import PrayerLeadershipEventKind

    last_decision = session.leadership_events.filter(
        kind__in=(PrayerLeadershipEventKind.SHARE_ACCEPTED, PrayerLeadershipEventKind.SHARE_DECLINED),
    ).order_by("-created_at").first()
    pending = session.leadership_events.filter(kind=PrayerLeadershipEventKind.SHARE_REQUESTED).order_by("-created_at").first()
    if not pending or (last_decision and last_decision.created_at >= pending.created_at):
        return None
    return {"admin_id": str(pending.actor_id), "admin_display_name": getattr(pending.actor, "display_name", "") or "Admin"}


class WatchListView(APIView):
    """GET /api/v1/prayer/watches/ — all 24, for the 'All 24 watches' grid."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        at = timezone.now()
        watches = PrayerWatchGroup.objects.select_related("group", "group__conversation").order_by("hour")
        payload = [_watch_summary(w, at=at)[0] for w in watches]
        return Response(WatchSummarySerializer(payload, many=True).data)


class WatchDetailView(APIView):
    """GET /api/v1/prayer/watches/<hour>/ — current stage, leader, join link."""
    permission_classes = [IsAuthenticated]

    def get(self, request, hour: int):
        watch_group = _watch_or_404(hour)
        if not watch_group:
            return Response({"detail": "Watch not found."}, status=status.HTTP_404_NOT_FOUND)

        at = timezone.now()
        summary, session = _watch_summary(watch_group, at=at)
        stage_info = services.compute_stage(session, at=at)

        payload = {
            **summary,
            "session_id": str(session.id),
            "participation_mode": session.participation_mode,
            "stage": (
                {
                    "key": stage_info.stage_key,
                    "label": stage_info.stage_label,
                    "display_text": stage_info.stage_display_text,
                    "started_at": stage_info.stage_started_at,
                    "ends_at": stage_info.stage_ends_at,
                    "seconds_remaining": stage_info.seconds_remaining_in_stage,
                }
                if stage_info.stage_key else None
            ),
            "seconds_remaining_in_session": stage_info.seconds_remaining_in_session,
            "my_leadership_state": _my_leadership_state(session, request.user),
            "my_monthly_leadership_count": services.monthly_leadership_count(request.user, at=at),
            "monthly_leadership_limit": services.MAX_MEMBER_LEADERSHIP_SESSIONS_PER_MONTH,
            "is_group_admin": _is_group_admin(watch_group.group, request.user),
            "is_go": is_platform_go(request.user),
            "pending_share_request": _pending_share_request(session),
        }
        return Response(WatchDetailSerializer(payload).data)


class _LeadershipActionView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_session(self, hour: int) -> tuple[PrayerWatchGroup, PrayerDailySession] | None:
        watch_group = _watch_or_404(hour)
        if not watch_group:
            return None
        session = services.get_or_create_session_for_watch(watch_group)
        return watch_group, session

    def _error_response(self, exc: services.LeadershipError):
        return Response({"detail": str(exc), "code": exc.code}, status=status.HTTP_400_BAD_REQUEST)


class ClaimLeadershipView(_LeadershipActionView):
    """POST /api/v1/prayer/watches/<hour>/leadership/claim/ — 'I AM LEADING'."""

    def post(self, request, hour: int):
        found = self._get_session(hour)
        if not found:
            return Response({"detail": "Watch not found."}, status=status.HTTP_404_NOT_FOUND)
        watch_group, session = found
        reactivate_group_membership(watch_group.group, request.user)
        try:
            services.claim_leadership(session_id=session.id, user=request.user)
        except services.LeadershipError as exc:
            return self._error_response(exc)
        return Response(_watch_summary(watch_group)[0])


class ReleaseLeadershipView(_LeadershipActionView):
    def post(self, request, hour: int):
        found = self._get_session(hour)
        if not found:
            return Response({"detail": "Watch not found."}, status=status.HTTP_404_NOT_FOUND)
        watch_group, session = found
        try:
            services.release_leadership(session_id=session.id, user=request.user)
        except services.LeadershipError as exc:
            return self._error_response(exc)
        return Response(_watch_summary(watch_group)[0])


class AdminSoloLeadershipView(_LeadershipActionView):
    def post(self, request, hour: int):
        found = self._get_session(hour)
        if not found:
            return Response({"detail": "Watch not found."}, status=status.HTTP_404_NOT_FOUND)
        watch_group, session = found
        reactivate_group_membership(watch_group.group, request.user)
        try:
            services.claim_admin_solo_leadership(session_id=session.id, admin_user=request.user)
        except services.LeadershipError as exc:
            return self._error_response(exc)
        return Response(_watch_summary(watch_group)[0])


class ShareRequestView(_LeadershipActionView):
    def post(self, request, hour: int):
        found = self._get_session(hour)
        if not found:
            return Response({"detail": "Watch not found."}, status=status.HTTP_404_NOT_FOUND)
        watch_group, session = found
        try:
            services.request_admin_share(session_id=session.id, admin_user=request.user)
        except services.LeadershipError as exc:
            return self._error_response(exc)
        return Response(_watch_summary(watch_group)[0])


class ShareRespondView(_LeadershipActionView):
    def post(self, request, hour: int):
        found = self._get_session(hour)
        if not found:
            return Response({"detail": "Watch not found."}, status=status.HTTP_404_NOT_FOUND)
        watch_group, session = found
        accept = bool(request.data.get("accept"))
        try:
            services.respond_admin_share(session_id=session.id, member_user=request.user, accept=accept)
        except services.LeadershipError as exc:
            return self._error_response(exc)
        return Response(_watch_summary(watch_group)[0])


class OverrideLeadershipView(_LeadershipActionView):
    """GO-only — 'Override leadership assignments when necessary.'"""

    def post(self, request, hour: int):
        if not is_platform_go(request.user):
            return Response({"detail": "Only the GO can do this."}, status=status.HTTP_403_FORBIDDEN)
        found = self._get_session(hour)
        if not found:
            return Response({"detail": "Watch not found."}, status=status.HTTP_404_NOT_FOUND)
        watch_group, session = found

        new_leader_id = request.data.get("user_id")
        new_leader = None
        if new_leader_id:
            from apps.accounts.models import User
            new_leader = User.objects.filter(id=new_leader_id).first()
            if not new_leader:
                return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        leader_kind = request.data.get("leader_kind") or services.PrayerLeaderKind.ADMIN
        note = str(request.data.get("note") or "")
        try:
            services.go_override_leader(
                session_id=session.id, go_user=request.user, new_leader=new_leader,
                leader_kind=leader_kind, note=note,
            )
        except services.LeadershipError as exc:
            return self._error_response(exc)
        return Response(_watch_summary(watch_group)[0])


class ParticipationModeView(_LeadershipActionView):
    def post(self, request, hour: int):
        found = self._get_session(hour)
        if not found:
            return Response({"detail": "Watch not found."}, status=status.HTTP_404_NOT_FOUND)
        watch_group, session = found
        mode = str(request.data.get("mode") or "")
        try:
            services.set_participation_mode(session_id=session.id, user=request.user, mode=mode)
        except services.LeadershipError as exc:
            return self._error_response(exc)
        return Response(_watch_summary(watch_group)[0])


class PrayerPointsView(APIView):
    """GET (list, with submission-window flag) / POST (submit or update
    the caller's own point) on /api/v1/prayer/watches/<hour>/prayer-points/."""
    permission_classes = [IsAuthenticated]

    def get(self, request, hour: int):
        watch_group = _watch_or_404(hour)
        if not watch_group:
            return Response({"detail": "Watch not found."}, status=status.HTTP_404_NOT_FOUND)
        session = services.get_or_create_session_for_watch(watch_group)
        points = PrayerPointSubmission.objects.filter(session=session).select_related("user").order_by("created_at")
        payload = [
            {
                "id": str(p.id),
                "user_id": str(p.user_id),
                "display_name": getattr(p.user, "display_name", "") or "KIS member",
                "text": p.text,
                "is_mine": p.user_id == request.user.id,
                "created_at": p.created_at,
                "updated_at": p.updated_at,
            }
            for p in points
        ]
        return Response({
            "submissions_open": services.prayer_points_window_open(session),
            "points": PrayerPointSerializer(payload, many=True).data,
        })

    def post(self, request, hour: int):
        watch_group = _watch_or_404(hour)
        if not watch_group:
            return Response({"detail": "Watch not found."}, status=status.HTTP_404_NOT_FOUND)
        session = services.get_or_create_session_for_watch(watch_group)
        serializer = SubmitPrayerPointSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reactivate_group_membership(watch_group.group, request.user)
        try:
            point = services.submit_prayer_point(
                session_id=session.id, user=request.user, text=serializer.validated_data["text"],
            )
        except services.LeadershipError as exc:
            return Response({"detail": str(exc), "code": exc.code}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            "id": str(point.id), "text": point.text, "created_at": point.created_at, "updated_at": point.updated_at,
        }, status=status.HTTP_200_OK)

    def delete(self, request, hour: int):
        watch_group = _watch_or_404(hour)
        if not watch_group:
            return Response({"detail": "Watch not found."}, status=status.HTTP_404_NOT_FOUND)
        session = services.get_or_create_session_for_watch(watch_group)
        try:
            services.delete_prayer_point(session_id=session.id, user=request.user)
        except services.LeadershipError as exc:
            return Response({"detail": str(exc), "code": exc.code}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MyActivityView(APIView):
    """GET /api/v1/prayer/my-activity/ — 'My prayer activity' panel."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        joined_hours = list(
            GroupMembership.objects.filter(
                user=user, left_at__isnull=True, is_banned=False, group__prayer_watch__isnull=False,
            ).values_list("group__prayer_watch__hour", flat=True)
        )
        now = timezone.now()
        upcoming = list(
            PrayerDailySession.objects.filter(
                leader=user, scheduled_start__gte=now, status__in=("scheduled", "active"),
            ).select_related("watch_group").order_by("scheduled_start")[:10]
        )
        led_this_month = services.monthly_leadership_count(user, at=now)
        payload = {
            "joined_watch_hours": sorted(joined_hours),
            "upcoming_leadership": [
                {
                    "hour": s.watch_group.hour, "name": s.watch_group.name,
                    "scheduled_start": s.scheduled_start, "session_id": str(s.id),
                }
                for s in upcoming
            ],
            "led_this_month": led_this_month,
            "remaining_this_month": max(0, services.MAX_MEMBER_LEADERSHIP_SESSIONS_PER_MONTH - led_this_month),
            "monthly_limit": services.MAX_MEMBER_LEADERSHIP_SESSIONS_PER_MONTH,
        }
        return Response(MyActivitySerializer(payload).data)


class WatchAdminsView(APIView):
    """GO-only management of a single watch's group admins — reuses
    GroupMembership.role for storage (the same field the generic group
    member-list already reads), but this write path is strictly GO-only,
    unlike the generic GroupViewSet.members/set-role action (which also
    lets an existing group admin promote another member) — see
    apps.prayer.views module note in docs/prayer-calendar for why that
    generic endpoint is deliberately NOT reused for this specific
    GO-only management screen."""
    permission_classes = [IsAuthenticated]

    def get(self, request, hour: int):
        watch_group = _watch_or_404(hour)
        if not watch_group:
            return Response({"detail": "Watch not found."}, status=status.HTTP_404_NOT_FOUND)
        admins = GroupMembership.objects.filter(
            group=watch_group.group, left_at__isnull=True, is_banned=False,
            role__in=(GroupRole.OWNER, GroupRole.ADMIN),
        ).select_related("user")
        payload = [
            {"user_id": str(m.user_id), "display_name": getattr(m.user, "display_name", "") or "", "phone": getattr(m.user, "phone", "") or ""}
            for m in admins
        ]
        return Response(GroupAdminSerializer(payload, many=True).data)

    def post(self, request, hour: int):
        if not is_platform_go(request.user):
            return Response({"detail": "Only the GO can manage prayer-watch admins."}, status=status.HTTP_403_FORBIDDEN)
        watch_group = _watch_or_404(hour)
        if not watch_group:
            return Response({"detail": "Watch not found."}, status=status.HTTP_404_NOT_FOUND)
        user_id = request.data.get("user_id")
        if not user_id:
            return Response({"detail": "user_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        from apps.accounts.models import User
        target = User.objects.filter(id=user_id).first()
        if not target:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        membership, _ = reactivate_group_membership(watch_group.group, target, role=GroupRole.ADMIN)
        if membership.role != GroupRole.ADMIN:
            membership.role = GroupRole.ADMIN
            membership.save(update_fields=["role"])
        return Response(status=status.HTTP_200_OK)

    def delete(self, request, hour: int):
        if not is_platform_go(request.user):
            return Response({"detail": "Only the GO can manage prayer-watch admins."}, status=status.HTTP_403_FORBIDDEN)
        watch_group = _watch_or_404(hour)
        if not watch_group:
            return Response({"detail": "Watch not found."}, status=status.HTTP_404_NOT_FOUND)
        user_id = request.data.get("user_id")
        membership = GroupMembership.objects.filter(
            group=watch_group.group, user_id=user_id, left_at__isnull=True,
        ).first()
        if not membership:
            return Response({"detail": "Member not found."}, status=status.HTTP_404_NOT_FOUND)
        if membership.role == GroupRole.OWNER:
            return Response({"detail": "The GO's own ownership cannot be removed here."}, status=status.HTTP_400_BAD_REQUEST)
        membership.role = GroupRole.MEMBER
        membership.save(update_fields=["role"])
        return Response(status=status.HTTP_204_NO_CONTENT)
