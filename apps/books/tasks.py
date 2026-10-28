import logging

from celery import shared_task

logger = logging.getLogger("security.books")


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def run_book_conversion(self, manuscript_id, force: bool = False):
    from .models import BookManuscript
    from .services import process_manuscript

    try:
        manuscript = BookManuscript.objects.select_related("book").get(id=manuscript_id)
    except BookManuscript.DoesNotExist:
        logger.warning("book_conversion_manuscript_missing", extra={"manuscript_id": str(manuscript_id)})
        return
    process_manuscript(manuscript, force=force)


@shared_task
def expire_book_publishing_restrictions():
    from .services import expire_stale_restrictions

    return expire_stale_restrictions()


@shared_task
def publish_scheduled_books_task():
    from .services import publish_scheduled_books

    return publish_scheduled_books()
