# apps/partners/management/commands/publish_scheduled_posts.py
"""Manual/cron fallback for scheduled announcement publishing.

Wraps the exact same function the Celery task
(apps.partners.tasks.publish_due_scheduled_posts_task) calls — this command
exists for deployments that don't run Celery Beat. Safe to run repeatedly;
only ever acts on PartnerPost rows still SCHEDULED past their scheduled_for.

Usage:
    python manage.py publish_scheduled_posts
    python manage.py publish_scheduled_posts --limit 50
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Publish partner announcement posts whose scheduled_for time has passed."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=200,
            help="Maximum number of posts to process in this run (default: 200).",
        )

    def handle(self, *args, **options):
        from apps.partners.tasks import publish_due_scheduled_posts

        result = publish_due_scheduled_posts(limit=options["limit"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Published {result['published']} of {result['candidates']} due scheduled post(s)."
            )
        )
