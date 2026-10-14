"""Periodic jobs for verification badge expiry — including the 90-day
user-identity renewal cycle (apps.verification.services.review_user_case
is what actually sets expires_at=now+90d on approval; nothing here does
that). schedule_verification_expiry_notifications and
expire_overdue_verification_badges already existed as real, working,
generic-across-every-subject-type functions (and a management command,
schedule_verification_expiry_reminders, wraps the first for manual/cron
use) — same "built but never scheduled" gap as apps/media/tasks.py's
upload-cleanup sweeps had before Celery Beat was wired up for those.
These are thin wrappers, not new logic, following that exact pattern."""
from celery import shared_task

from .services import expire_overdue_verification_badges, schedule_verification_expiry_notifications


@shared_task
def send_verification_renewal_reminders():
    """Reminds a user before any of their verification badges/cases expire
    (default windows: 30/14/7/1 days — settings.VERIFICATION_EXPIRY_REMINDER_DAYS
    overrides). dry_run=False is the only thing this wrapper adds over the
    underlying function's own default."""
    return schedule_verification_expiry_notifications(dry_run=False)


@shared_task
def expire_overdue_user_verification_badges():
    """Flips ACTIVE badges whose expires_at has passed to EXPIRED, across
    every subject type (not just USER — user identity badges are simply
    the first subject type to ever actually set expires_at). Display
    correctness never depends on this: public_badges_for_subject/_subjects
    already exclude an expired-by-date badge at read time regardless of
    `status` (models_q_not_expired). This is for admin/audit clarity
    only."""
    return expire_overdue_verification_badges(dry_run=False)
