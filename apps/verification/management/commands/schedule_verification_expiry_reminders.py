from django.core.management.base import BaseCommand, CommandError

from apps.verification.services import schedule_verification_expiry_notifications


class Command(BaseCommand):
    help = "Dry-run or send verification expiry/reverification reminders through the central notification system."

    def add_arguments(self, parser):
        parser.add_argument("--send", action="store_true", help="Create notifications. Defaults to dry-run.")
        parser.add_argument("--days", default="", help="Comma-separated reminder windows, e.g. 30,14,7,1.")
        parser.add_argument("--limit", type=int, default=500, help="Maximum expiring items to inspect.")

    def handle(self, *args, **options):
        days_list = None
        if options.get("days"):
            try:
                days_list = [int(item.strip()) for item in options["days"].split(",") if item.strip()]
            except ValueError as exc:
                raise CommandError("--days must be a comma-separated list of integers.") from exc
        result = schedule_verification_expiry_notifications(
            days_list=days_list,
            dry_run=not bool(options.get("send")),
            limit=options.get("limit") or 500,
        )
        mode = "send" if not result["dry_run"] else "dry-run"
        self.stdout.write(
            self.style.SUCCESS(
                f"verification expiry reminders {mode}: matched={result['matched']} "
                f"created={result['created']} windows={','.join(str(day) for day in result['windows'])}"
            )
        )
        if result.get("candidates"):
            for row in result["candidates"][:10]:
                self.stdout.write(
                    f"- {row['kind']} {row['id']} owner={row['owner_id']} "
                    f"days_until={row['days_until']} window={row['window_days']}"
                )
