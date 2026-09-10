from celery import shared_task


@shared_task
def purge_expired_statuses_task():
    """Thin wrapper so purge_expired_statuses - a real, working function
    with its own management command - is actually scheduled (see
    CELERY_BEAT_SCHEDULE) instead of only running when someone remembers to
    invoke the command by hand. Mirrors
    apps.broadcasts.tasks.purge_expired_broadcasts_task's exact pattern."""
    from .services import purge_expired_statuses

    return purge_expired_statuses()
