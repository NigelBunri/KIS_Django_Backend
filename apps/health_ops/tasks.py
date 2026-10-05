"""Periodic observability/self-healing sweeps for health_ops, following the
same recurring-Celery-beat-sweep convention already established across the
project (see apps/broadcasts/tasks.py's sweep_stuck_education_bookings,
apps/billing/tasks.py's expiry sweeps — all registered in
CELERY_BEAT_SCHEDULE, config/settings/base.py).

Unlike those domains, health_ops' most safety-relevant stuck-state is an
EmergencyDispatchSession that nobody has touched in a while: if the last
real tracking update on a live emergency is 30+ minutes old, that is not a
"will get to it eventually" problem like a stale cart or an unclaimed
reward — it is a signal someone may be waiting for help that never came,
and it needs to surface to a human now. logger.error(...) is deliberately
used (not .warning()) for genuinely stuck emergencies specifically because
this project's Sentry init (config/settings/production.py) enables
sentry_sdk's LoggingIntegration by default, so an ERROR-level log call is
automatically captured as a real Sentry event with no extra wiring needed
— the same "no-op without SENTRY_DSN, real alert with it" posture the rest
of the project already relies on.
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.db.models import DateTimeField
from django.db.models.functions import Coalesce
from django.utils import timezone

logger = logging.getLogger(__name__)

# How long an active emergency dispatch session can go without a tracking
# update before it's considered stuck. Short, because this is the one
# health_ops domain where "stuck" plausibly means "someone is still
# waiting for help" — unlike a stale cart or an unclaimed reward, there is
# no safe default assumption to fall back on here.
EMERGENCY_DISPATCH_STUCK_THRESHOLD_MINUTES = 30

# PaymentBillingSession left PAYMENT_PENDING (provider checkout started,
# webhook never arrived) for this long is flagged — likely a lost/delayed
# webhook, not a security issue (see the Section 13 audit: the session
# cannot self-certify PAID), but a patient stuck unable to receive care
# they may have already paid for is a real operational problem worth
# surfacing.
BILLING_PAYMENT_PENDING_STUCK_THRESHOLD_MINUTES = 60


@shared_task
def sweep_stuck_emergency_dispatch_sessions(limit: int = 500) -> dict:
    from .models import EmergencyDispatchSession, EmergencyDispatchStatus

    cutoff = timezone.now() - timezone.timedelta(minutes=EMERGENCY_DISPATCH_STUCK_THRESHOLD_MINUTES)
    live_statuses = [
        EmergencyDispatchStatus.WAITING,
        EmergencyDispatchStatus.TRIAGING,
        EmergencyDispatchStatus.DISPATCHED,
        EmergencyDispatchStatus.IN_TRANSIT,
    ]
    # last_tracking_at is null until the first step/tracking update ever
    # lands — a session created and then immediately abandoned (e.g. a
    # dropped connection right after /start) would otherwise never match
    # last_tracking_at__lte=cutoff at all (SQL NULL comparisons never
    # match), which is exactly the "silently stuck forever" failure mode
    # this sweep exists to catch. Coalesce to created_at so that case is
    # covered too.
    stuck_sessions = list(
        EmergencyDispatchSession.objects.filter(status__in=live_statuses)
        .annotate(last_activity_at=Coalesce("last_tracking_at", "created_at", output_field=DateTimeField()))
        .filter(last_activity_at__lte=cutoff)
        .select_related("institution", "user")
        .order_by("last_activity_at")[:limit]
    )

    for session in stuck_sessions:
        stuck_minutes = int((timezone.now() - session.last_activity_at).total_seconds() // 60)
        logger.error(
            "health_ops.emergency_dispatch.stuck",
            extra={
                "emergency_session_id": str(session.id),
                "dispatch_code": session.dispatch_code,
                "institution_id": str(session.institution_id) if session.institution_id else None,
                "patient_user_id": str(session.user_id),
                "status": session.status,
                "stuck_minutes": stuck_minutes,
            },
        )

    return {"stuck_count": len(stuck_sessions), "threshold_minutes": EMERGENCY_DISPATCH_STUCK_THRESHOLD_MINUTES}


@shared_task
def sweep_stuck_billing_sessions(limit: int = 500) -> dict:
    from .models import PaymentBillingSession, PaymentBillingStatus

    cutoff = timezone.now() - timezone.timedelta(minutes=BILLING_PAYMENT_PENDING_STUCK_THRESHOLD_MINUTES)
    stuck_sessions = list(
        PaymentBillingSession.objects.filter(
            status=PaymentBillingStatus.PAYMENT_PENDING,
            updated_at__lte=cutoff,
        )
        .select_related("institution", "user")
        .order_by("updated_at")[:limit]
    )

    for session in stuck_sessions:
        stuck_minutes = int((timezone.now() - session.updated_at).total_seconds() // 60)
        logger.warning(
            "health_ops.billing_session.stuck_payment_pending",
            extra={
                "billing_session_id": str(session.id),
                "institution_id": str(session.institution_id) if session.institution_id else None,
                "patient_user_id": str(session.user_id),
                "payment_provider": session.payment_provider,
                "payment_reference": session.payment_reference,
                "stuck_minutes": stuck_minutes,
            },
        )

    return {"stuck_count": len(stuck_sessions), "threshold_minutes": BILLING_PAYMENT_PENDING_STUCK_THRESHOLD_MINUTES}
