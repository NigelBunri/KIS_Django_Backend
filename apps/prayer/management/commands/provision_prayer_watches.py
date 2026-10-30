"""Idempotent setup for the 24 permanent Prayer Calendar rooms.

Usage:
    python manage.py provision_prayer_watches
    python manage.py provision_prayer_watches --skip-welcome-messages

Running this twice is safe: an already-provisioned watch is left exactly
as it is (no duplicate group, conversation, or welcome message); only
whatever is genuinely still missing gets created or sent.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.prayer.provisioning import ProvisioningError, provision_prayer_watches


class Command(BaseCommand):
    help = "Idempotently provision the 24 permanent Prayer Calendar watch groups."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-welcome-messages", action="store_true", default=False,
            help="Create/verify the 24 groups but don't attempt to post the KIS welcome message.",
        )

    def handle(self, *args, **options):
        try:
            report = provision_prayer_watches(post_welcome_messages=not options["skip_welcome_messages"])
        except ProvisioningError as exc:
            raise CommandError(str(exc))

        self.stdout.write(self.style.SUCCESS(
            f"Watches created: {len(report['created'])} (hours: {report['created']})"
        ))
        self.stdout.write(f"Watches already existed: {len(report['already_existed'])}")
        if not options["skip_welcome_messages"]:
            self.stdout.write(self.style.SUCCESS(
                f"Welcome messages sent/confirmed: {len(report['welcome_sent'])}"
            ))
            if report["welcome_failed"]:
                self.stdout.write(self.style.WARNING(
                    f"Welcome messages FAILED for hours: {report['welcome_failed']} "
                    f"(the groups themselves were still created/verified — re-run this "
                    f"command, or just --skip-welcome-messages next time, once the "
                    f"NestJS internal API is reachable)."
                ))
