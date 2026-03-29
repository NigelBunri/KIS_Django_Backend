"""Background tasks for admin analytics."""
from celery import shared_task


@shared_task(bind=True)
def refresh_dashboard(self):
    """Placeholder for a periodic dashboard refresher."""
    return {"status": "queued"}
