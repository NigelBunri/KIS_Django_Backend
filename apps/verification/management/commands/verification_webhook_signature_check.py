from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.verification.providers import verify_webhook_signature


class Command(BaseCommand):
    help = "Validate a verification webhook HMAC signature without printing secrets."

    def add_arguments(self, parser):
        parser.add_argument("--payload", required=True, help="Raw webhook payload string to validate.")
        parser.add_argument("--signature", required=True, help="Webhook signature header value.")

    def handle(self, *args, **options):
        if not getattr(settings, "VERIFICATION_WEBHOOK_SECRET", ""):
            raise CommandError("VERIFICATION_WEBHOOK_SECRET is not configured.")
        ok, reason = verify_webhook_signature(
            body=str(options["payload"]).encode("utf-8"),
            signature=str(options["signature"]),
        )
        if not ok:
            raise CommandError(f"signature_valid=false reason={reason}")
        self.stdout.write("signature_valid=true reason=ok")
