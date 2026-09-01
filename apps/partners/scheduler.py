# apps/partners/scheduler.py
"""Registers the scheduled-announcement sweep as a django_celery_beat
PeriodicTask row rather than a static CELERY_BEAT_SCHEDULE entry in
config/settings/base.py — this app's CELERY_BEAT_SCHEDULER is already
DatabaseScheduler, so a DB-registered task is read the same way a
settings-declared one would be, without touching shared settings."""
import logging

from django.db.utils import OperationalError, ProgrammingError

logger = logging.getLogger(__name__)


def register_scheduled_post_sweep() -> None:
    try:
        _register_scheduled_post_sweep()
    except (OperationalError, ProgrammingError):
        return
    except Exception as exc:
        logger.warning("[partners.scheduler] Unable to register scheduled-post sweep: %s", exc)


def _register_scheduled_post_sweep() -> None:
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    schedule, _ = IntervalSchedule.objects.get_or_create(every=5, period=IntervalSchedule.MINUTES)
    PeriodicTask.objects.get_or_create(
        name="partners.publish_due_scheduled_posts",
        defaults={
            "interval": schedule,
            "task": "apps.partners.tasks.publish_due_scheduled_posts_task",
        },
    )
