# apps/partners/tasks.py
"""Thin @shared_task wrapper around the actual publish logic, matching the
pattern in apps.accounts.tasks/apps.billing.tasks: a plain, independently
testable function plus a Celery wrapper, with a management-command fallback
(apps/partners/management/commands/publish_scheduled_posts.py) for
deployments where Celery Beat isn't provisioned."""
from __future__ import annotations

from celery import shared_task
from django.utils import timezone


def publish_due_scheduled_posts(limit: int = 200) -> dict:
    from .models import PartnerPost, PartnerPostStatus

    due = list(
        PartnerPost.objects.filter(
            status=PartnerPostStatus.SCHEDULED,
            scheduled_for__lte=timezone.now(),
            is_deleted=False,
        ).order_by("scheduled_for")[:limit]
    )
    published = 0
    for post in due:
        post.status = PartnerPostStatus.PUBLISHED
        post.scheduled_for = None
        post.created_at = timezone.now()
        post.save(update_fields=["status", "scheduled_for", "created_at"])
        published += 1
    return {"candidates": len(due), "published": published}


@shared_task
def publish_due_scheduled_posts_task():
    return publish_due_scheduled_posts()
