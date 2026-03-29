"""Signals wiring for audit trail (auth events)."""
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from admin_control.audit.logging import AuditLogger


@receiver(user_logged_in)
def record_login(sender, user, request, **kwargs):
    AuditLogger.log(
        actor=user,
        action_type="auth.login",
        target_app="accounts",
        target_model="User",
        target_pk=str(getattr(user, "pk", "")),
        severity="info",
        metadata={
            "ip": request.META.get("REMOTE_ADDR"),
            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
        },
    )


@receiver(user_logged_out)
def record_logout(sender, user, request, **kwargs):
    AuditLogger.log(
        actor=user,
        action_type="auth.logout",
        target_app="accounts",
        target_model="User",
        target_pk=str(getattr(user, "pk", "")),
        severity="info",
        metadata={
            "ip": request.META.get("REMOTE_ADDR"),
            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
        },
    )


@receiver(user_login_failed)
def record_login_failed(sender, credentials, request, **kwargs):
    AuditLogger.log(
        actor=None,
        action_type="auth.login_failed",
        target_app="accounts",
        severity="warning",
        metadata={
            "credentials": credentials,
            "ip": request.META.get("REMOTE_ADDR"),
            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
        },
    )
