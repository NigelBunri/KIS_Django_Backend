"""Admin user management views — list, search, ban, tier change, force-verify."""
from __future__ import annotations

from django.core.paginator import Paginator
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from admin_control.audit.logging import AuditLogger
from admin_control.permissions import IsAdminControlUser
from admin_control.roles import AdminAccessService


def _safe_int(val, default, lo=1, hi=250):
    try:
        return max(lo, min(int(val), hi))
    except (TypeError, ValueError):
        return default


class AdminUserListView(APIView):
    """
    GET /control/admin/users/
    Search / list all users with full admin context.
    Query params: q, tier, status, country, is_staff, page, per_page
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "users.view"

    def get(self, request):
        from apps.accounts.models import User

        qs = User.objects.all().order_by("-created_at")
        q = request.query_params.get("q", "").strip()
        if q:
            from django.db.models import Q as DQ
            qs = qs.filter(
                DQ(email__icontains=q)
                | DQ(username__icontains=q)
                | DQ(display_name__icontains=q)
                | DQ(phone__icontains=q)
            )
        for field in ("tier", "status", "country"):
            val = request.query_params.get(field)
            if val:
                qs = qs.filter(**{field: val})
        is_staff = request.query_params.get("is_staff")
        if is_staff in ("true", "false"):
            qs = qs.filter(is_staff=(is_staff == "true"))

        page_num = _safe_int(request.query_params.get("page", 1), 1, lo=1, hi=10000)
        per_page = _safe_int(request.query_params.get("per_page", 25), 25, lo=1, hi=100)
        paginator = Paginator(qs, per_page)
        page_obj = paginator.get_page(page_num)

        users = [_serialize_user(u) for u in page_obj.object_list]
        return Response({
            "users": users,
            "pagination": {
                "page": page_obj.number,
                "per_page": per_page,
                "total_pages": paginator.num_pages,
                "total_items": paginator.count,
            },
        })


class AdminUserDetailView(APIView):
    """
    GET  /control/admin/users/<user_id>/   — full admin user profile
    PATCH /control/admin/users/<user_id>/  — update tier / status / trust_score / is_staff
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "users.view"

    def get(self, request, user_id):
        user = _get_user_or_404(user_id)
        if isinstance(user, Response):
            return user

        from apps.moderation.models import ModerationAction
        recent_actions = list(
            ModerationAction.objects.filter(flag__reporter_id=user.id)
            .select_related("flag")
            .order_by("-created_at")[:10]
            .values("id", "action", "notes", "created_at", "flag__target_type", "flag__severity")
        )
        return Response({
            "user": _serialize_user(user, full=True),
            "recent_moderation_actions": recent_actions,
        })

    def patch(self, request, user_id):
        user = _get_user_or_404(user_id)
        if isinstance(user, Response):
            return user

        allowed = {"tier", "status", "trust_score", "is_staff", "country", "display_name"}
        changed = []
        for key in allowed:
            if key in request.data:
                setattr(user, key, request.data[key])
                changed.append(key)

        if changed:
            user.save(update_fields=changed)
            AuditLogger.log(
                actor=request.user,
                action_type="user.profile_updated",
                target_app="accounts",
                target_model="User",
                target_pk=str(user.id),
                metadata={"fields": changed},
            )

        return Response({"user": _serialize_user(user, full=True)})


class AdminUserBanView(APIView):
    """
    POST /control/admin/users/<user_id>/ban/
    Body: {reason, permanent (bool), duration_days (int)}
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "users.moderate"

    def post(self, request, user_id):
        user = _get_user_or_404(user_id)
        if isinstance(user, Response):
            return user

        reason = str(request.data.get("reason", "Policy violation")).strip() or "Policy violation"
        permanent = bool(request.data.get("permanent", False))

        user.status = "banned" if permanent else "suspended"
        user.save(update_fields=["status"])

        AuditLogger.log(
            actor=request.user,
            action_type="user.banned" if permanent else "user.suspended",
            target_app="accounts",
            target_model="User",
            target_pk=str(user.id),
            severity="warning",
            metadata={"reason": reason, "permanent": permanent},
        )
        return Response({"user": _serialize_user(user), "action": "banned"})


class AdminUserUnbanView(APIView):
    """POST /control/admin/users/<user_id>/unban/"""
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "users.moderate"

    def post(self, request, user_id):
        user = _get_user_or_404(user_id)
        if isinstance(user, Response):
            return user

        user.status = "active"
        user.save(update_fields=["status"])
        AuditLogger.log(
            actor=request.user,
            action_type="user.unbanned",
            target_app="accounts",
            target_model="User",
            target_pk=str(user.id),
            metadata={"restored_by": str(request.user.id)},
        )
        return Response({"user": _serialize_user(user), "action": "unbanned"})


class AdminUserBlockView(APIView):
    """
    POST /control/admin/users/<user_id>/block/
    Body: {reason}
    Distinct from ban/suspend: also revokes every active device session
    immediately (ban/suspend historically didn't - block is the "kick them
    out right now" action) and sets is_active=False, which is what
    LoginSerializer's password-login path actually checks (the status
    string alone only blocks apps.otp's OTP-login path).
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "users.moderate"

    def post(self, request, user_id):
        from apps.accounts.models import Device
        from apps.accounts.views import revoke_device_session

        user = _get_user_or_404(user_id)
        if isinstance(user, Response):
            return user

        reason = str(request.data.get("reason", "")).strip() or "Blocked by admin"
        user.status = "blocked"
        user.is_active = False
        user.save(update_fields=["status", "is_active"])
        for device in Device.objects.filter(user=user, revoked_at__isnull=True):
            revoke_device_session(user, device, reason="account_blocked_by_admin", request=request)

        AuditLogger.log(
            actor=request.user,
            action_type="user.blocked",
            target_app="accounts",
            target_model="User",
            target_pk=str(user.id),
            severity="warning",
            metadata={"reason": reason},
        )
        return Response({"user": _serialize_user(user), "action": "blocked"})


class AdminUserDeleteView(APIView):
    """
    POST /control/admin/users/<user_id>/delete/
    Body: {reason}
    Reuses the exact same grace-period deletion machinery as the user's
    own self-service account deletion (apps.accounts.views.
    schedule_account_deletion) rather than a second, admin-only deletion
    path - deactivates, soft-deletes, revokes every device session, and
    files the GDPRRequest the daily purge sweep hard-deletes after
    settings.ACCOUNT_DELETION_GRACE_DAYS. Reversible via restore/ until
    then, exactly like the self-service flow's own reactivation window.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "users.moderate"

    def post(self, request, user_id):
        from apps.accounts.views import schedule_account_deletion

        user = _get_user_or_404(user_id)
        if isinstance(user, Response):
            return user

        reason = str(request.data.get("reason", "")).strip() or "Deleted by admin"
        gdpr_request = schedule_account_deletion(user, request=request, actor=request.user, source="admin_console")

        AuditLogger.log(
            actor=request.user,
            action_type="user.deleted",
            target_app="accounts",
            target_model="User",
            target_pk=str(user.id),
            severity="critical",
            metadata={"reason": reason, "scheduled_for": gdpr_request.scheduled_for.isoformat()},
        )
        return Response({
            "user": _serialize_user(user),
            "action": "deleted",
            "scheduled_for": gdpr_request.scheduled_for.isoformat(),
        })


class AdminUserRestoreView(APIView):
    """
    POST /control/admin/users/<user_id>/restore/
    Reverses ban, suspend, block, OR a pending scheduled deletion - one
    button for "undo whatever moderation state this account is in",
    covering the case AdminUserUnbanView alone doesn't (it only clears the
    status string, not is_active/is_deleted/a pending GDPRRequest). Only
    cancels a deletion still inside its grace period, matching
    AccountReactivationView's own window.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "users.moderate"

    def post(self, request, user_id):
        from django.db import transaction
        from django.utils import timezone

        from apps.accounts.models import GDPRRequest

        user = _get_user_or_404(user_id)
        if isinstance(user, Response):
            return user

        with transaction.atomic():
            pending = (
                GDPRRequest.objects.select_for_update()
                .filter(user=user, type="account_deletion", status="pending", scheduled_for__gt=timezone.now())
                .order_by("-created_at")
                .first()
            )
            if pending:
                pending.status = "cancelled"
                pending.completed_at = timezone.now()
                pending.save(update_fields=["status", "completed_at", "updated_at"])
            user.status = "active"
            user.is_active = True
            user.is_deleted = False
            user.save(update_fields=["status", "is_active", "is_deleted"])

        if pending:
            # A pending deletion is a strong enough signal (violation-driven
            # or self-service) that a bare restore shouldn't be silent about
            # what happens next - one more explicit warning before we stop
            # tracking that this account was ever on the edge of deletion.
            try:
                from apps.notifications.services import create_notification
                create_notification(
                    user_id=user.id,
                    type="account.deletion_cancelled",
                    title="Your account has been restored",
                    body=(
                        "Your account's scheduled deletion has been cancelled and your account "
                        "is active again. Further violations of KIS's content rules can result "
                        "in account restriction, suspension, or permanent deletion."
                    ),
                    priority="HIGH",
                    channels=["IN_APP", "PUSH"],
                    dedup_key=f"account_restored_after_pending_deletion:{pending.id}",
                )
            except Exception as exc:
                # Never let a notification failure block the restore
                # itself, but it must still be visible somewhere an admin
                # reviewing enforcement actually looks, not just a server
                # log.
                AuditLogger.log(
                    actor=request.user,
                    action_type="user.restore_notification_failed",
                    target_app="accounts",
                    target_model="User",
                    target_pk=str(user.id),
                    severity="warning",
                    metadata={"error": f"{type(exc).__name__}: {exc}"[:1000]},
                )

        AuditLogger.log(
            actor=request.user,
            action_type="user.restored",
            target_app="accounts",
            target_model="User",
            target_pk=str(user.id),
            metadata={"had_pending_deletion": bool(pending)},
        )
        return Response({"user": _serialize_user(user), "action": "restored"})


class AdminUserScheduleViolationDeletionView(APIView):
    """
    POST /control/admin/users/<user_id>/schedule-violation-deletion/
    An administrator has reviewed an account already in PENDING HUMAN
    REVIEW (see apps.moderation.services.apply_ai_flag_consequence - status
    "suspended" is that state) and decided it should be permanently
    deleted. Reuses the same schedule_account_deletion machinery as
    self-service deletion, with a much shorter grace_period
    (ACCOUNT_VIOLATION_DELETION_WARNING_HOURS, default 3h) and its own
    notification wording - see schedule_account_deletion's
    source == "admin_violation_review" branch.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "users.moderate"

    def post(self, request, user_id):
        import datetime

        from django.conf import settings

        from apps.accounts.views import schedule_account_deletion

        user = _get_user_or_404(user_id)
        if isinstance(user, Response):
            return user

        reason = str(request.data.get("reason", "")).strip() or "Repeated/severe content violations"
        hours = int(getattr(settings, "ACCOUNT_VIOLATION_DELETION_WARNING_HOURS", 3))
        gdpr_request = schedule_account_deletion(
            user, request=request, actor=request.user, source="admin_violation_review",
            grace_period=datetime.timedelta(hours=hours),
        )

        AuditLogger.log(
            actor=request.user,
            action_type="user.violation_deletion_scheduled",
            target_app="accounts",
            target_model="User",
            target_pk=str(user.id),
            severity="critical",
            metadata={"reason": reason, "scheduled_for": gdpr_request.scheduled_for.isoformat(), "warning_hours": hours},
        )
        return Response({
            "user": _serialize_user(user),
            "action": "violation_deletion_scheduled",
            "scheduled_for": gdpr_request.scheduled_for.isoformat(),
        })


class AdminUserViolationsView(APIView):
    """
    GET /control/admin/users/<user_id>/violations/
    Everything an administrator needs to review an account flagged for
    content violations - reuses the real UserReputation/ModerationAction/
    Flag/GDPRRequest records rather than a second violation-tracking model.
    Never returns the actual flagged media itself (use media-safety's own
    scoped, signed-URL endpoint for that, one file at a time).
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "users.moderate"

    def get(self, request, user_id):
        from apps.accounts.models import GDPRRequest
        from apps.media.models import MediaSafetyScan
        from apps.moderation.models import ModerationAction, UserReputation

        user = _get_user_or_404(user_id)
        if isinstance(user, Response):
            return user

        reputation = UserReputation.objects.filter(user_id=user.id).first()
        blocked_incidents = list(
            MediaSafetyScan.objects.filter(owner_id=user.id, status="blocked")
            .order_by("-created_at")
            .values("id", "context", "mime_type", "created_at", "reason", "deleted_at")[:100]
        )
        for row in blocked_incidents:
            row["id"] = str(row["id"])
            row["created_at"] = row["created_at"].isoformat() if row["created_at"] else None
            row["deleted_at"] = row["deleted_at"].isoformat() if row["deleted_at"] else None

        warning_history = list(
            ModerationAction.objects.filter(
                performed_by_id="00000000-0000-0000-0000-000000000000",
                action__in=["WARN", "SUSPEND", "REINSTATE"],
                flag__target_id__in=MediaSafetyScan.objects.filter(owner_id=user.id).values("id"),
            )
            .order_by("-created_at")
            .values("id", "action", "notes", "created_at", "auto_generated")[:100]
        )
        for row in warning_history:
            row["id"] = str(row["id"])
            row["created_at"] = row["created_at"].isoformat() if row["created_at"] else None

        pending_deletion = (
            GDPRRequest.objects.filter(user=user, type="account_deletion", status="pending")
            .order_by("-created_at")
            .values("id", "scheduled_for", "created_at")
            .first()
        )
        if pending_deletion:
            pending_deletion["id"] = str(pending_deletion["id"])
            pending_deletion["scheduled_for"] = pending_deletion["scheduled_for"].isoformat()
            pending_deletion["created_at"] = pending_deletion["created_at"].isoformat()

        return Response({
            "user": _serialize_user(user),
            "violation_count": reputation.flags_received if reputation else 0,
            "actions_taken": reputation.actions_taken if reputation else 0,
            "blocked_incidents": blocked_incidents,
            "warning_history": warning_history,
            "pending_deletion": pending_deletion,
        })


class AdminUserTierChangeView(APIView):
    """
    POST /control/admin/users/<user_id>/set-tier/
    Body: {tier: "Pro" | "Business" | ...}
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "users.moderate"

    def post(self, request, user_id):
        from apps.accounts.tiers import TIER_HIERARCHY

        user = _get_user_or_404(user_id)
        if isinstance(user, Response):
            return user

        new_tier = str(request.data.get("tier", "")).strip()
        canonical = {t.lower(): t for t in TIER_HIERARCHY}
        resolved = canonical.get(new_tier.lower())
        if not resolved:
            return Response(
                {"detail": f"Unknown tier. Valid options: {list(canonical.values())}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_tier = user.tier
        user.tier = resolved
        user.save(update_fields=["tier"])
        AuditLogger.log(
            actor=request.user,
            action_type="user.tier_changed",
            target_app="accounts",
            target_model="User",
            target_pk=str(user.id),
            metadata={"from": old_tier, "to": resolved},
        )
        return Response({"user": _serialize_user(user), "action": "tier_changed", "old_tier": old_tier, "new_tier": resolved})


class AdminUserDeviceWipeView(APIView):
    """
    POST /control/admin/users/<user_id>/wipe-devices/
    Deletes every Device row (parent + secondary) for one account, so its
    next login registers a fresh parent device with no pairing/secondary-
    code prompt. The account itself is untouched.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "users.moderate"

    def post(self, request, user_id):
        from apps.accounts.device_admin import wipe_devices_for_user

        user = _get_user_or_404(user_id)
        if isinstance(user, Response):
            return user

        reason = str(request.data.get("reason", "")).strip() or "admin_reset"
        result = wipe_devices_for_user(user, actor=request.user, reason=reason)

        AuditLogger.log(
            actor=request.user,
            action_type="user.devices_wiped",
            target_app="accounts",
            target_model="User",
            target_pk=str(user.id),
            severity="warning",
            metadata=result,
        )
        return Response({"user": _serialize_user(user), "action": "devices_wiped", **result})


class AdminDeviceWipeAllView(APIView):
    """
    POST /control/admin/devices/wipe-all/
    Body: {confirm: "WIPE ALL DEVICES", reason?: str}

    Platform-wide: deletes every Device row for every account so every user's
    next login registers a fresh parent device with no secondary/pairing-code
    prompt. Accounts are kept — only device history is removed. Restricted to
    super-admin (GO) role regardless of the caller's granular permission
    grants, and requires an exact confirmation phrase since this is
    irreversible and affects the entire user base in one call.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "platform.dangerous_ops"

    CONFIRM_PHRASE = "WIPE ALL DEVICES"

    def post(self, request):
        if not AdminAccessService.is_super_admin(request.user):
            return Response(
                {"detail": "Only a super-admin (GO) may run a platform-wide device wipe."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if str(request.data.get("confirm", "")).strip() != self.CONFIRM_PHRASE:
            return Response(
                {"detail": f'Send {{"confirm": "{self.CONFIRM_PHRASE}"}} to proceed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.accounts.device_admin import wipe_all_devices

        reason = str(request.data.get("reason", "")).strip() or "admin_console_platform_wide_wipe"
        result = wipe_all_devices(actor=request.user, reason=reason)

        AuditLogger.log(
            actor=request.user,
            action_type="platform.devices_wiped_all",
            severity="critical",
            metadata=result,
        )
        return Response({"action": "devices_wiped_all", **result})


class AdminPlatformStatsView(APIView):
    """
    GET /control/admin/platform-stats/
    Quick-access KPIs: user totals by tier, status, recent growth.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "users.view"

    def get(self, request):
        from django.db.models import Count
        from datetime import timedelta
        from apps.accounts.models import User

        now = timezone.now()
        tiers = list(User.objects.values("tier").annotate(count=Count("id")).order_by("-count"))
        statuses = list(User.objects.values("status").annotate(count=Count("id")))
        new_7d = User.objects.filter(created_at__gte=now - timedelta(days=7)).count()
        new_30d = User.objects.filter(created_at__gte=now - timedelta(days=30)).count()
        banned = User.objects.filter(status__in=["banned", "suspended"]).count()
        staff_count = User.objects.filter(is_staff=True).count()
        superuser_count = User.objects.filter(is_superuser=True).count()

        # 30-day daily growth series
        growth_series = []
        for i in range(30, 0, -1):
            day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            growth_series.append({
                "date": day_start.strftime("%Y-%m-%d"),
                "new_users": User.objects.filter(created_at__gte=day_start, created_at__lt=day_end).count(),
            })

        return Response({
            "total_users": User.objects.count(),
            "new_users_7d": new_7d,
            "new_users_30d": new_30d,
            "banned_users": banned,
            "staff_count": staff_count,
            "superuser_count": superuser_count,
            "by_tier": tiers,
            "by_status": statuses,
            "growth_series_30d": growth_series,
            "generated_at": now.isoformat(),
        })


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_user_or_404(user_id):
    from apps.accounts.models import User
    try:
        return User.objects.get(id=user_id)
    except (User.DoesNotExist, Exception):
        return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)


def _serialize_user(user, full: bool = False):
    base = {
        "id": str(user.id),
        "email": user.email,
        "username": user.username,
        "display_name": user.display_name,
        "phone": user.phone,
        "tier": user.tier,
        "status": getattr(user, "status", "active"),
        "is_active": user.is_active,
        "is_deleted": getattr(user, "is_deleted", False),
        "country": getattr(user, "country", ""),
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,
        "trust_score": getattr(user, "trust_score", 0.0),
        "last_login_at": getattr(user, "last_login_at", None),
        "date_joined": user.created_at.isoformat() if getattr(user, "created_at", None) else None,
    }
    if full:
        base.update({
            "email_verified": getattr(user, "email_verified", False),
            "locale": getattr(user, "locale", "en"),
            "timezone": getattr(user, "timezone", "UTC"),
            "verification": getattr(user, "verification", {}),
            "entitlements": getattr(user, "entitlements", {}),
            "preferences": getattr(user, "preferences", {}),
        })
    return base
