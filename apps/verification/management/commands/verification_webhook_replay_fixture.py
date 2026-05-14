import hashlib
import hmac
import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Generate signed verification webhook replay fixtures for staging QA without printing secrets."

    def add_arguments(self, parser):
        parser.add_argument("--provider", default="dojah")
        parser.add_argument("--case-id", default="")
        parser.add_argument(
            "--status",
            default="approved",
            choices=("approved", "rejected", "needs_more_info", "provider_pending", "unmatched"),
        )

    def handle(self, *args, **options):
        secret = getattr(settings, "VERIFICATION_WEBHOOK_SECRET", "")
        if not secret:
            raise CommandError("VERIFICATION_WEBHOOK_SECRET is required to generate a replay signature.")
        provider = str(options.get("provider") or "dojah").strip()[:32]
        case_id = str(options.get("case_id") or "").strip()
        status = str(options.get("status") or "approved").strip()
        reference = f"sandbox:{provider}:{case_id}" if case_id else f"sandbox:{provider}:00000000-0000-4000-8000-000000000000"
        if status == "unmatched":
            reference = f"sandbox:{provider}:00000000-0000-4000-8000-000000000999"
        payload = {
            "provider_case_id": reference,
            "status": status,
            "event": f"verification.{status}",
            "sandbox": True,
        }
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        self.stdout.write("payload:")
        self.stdout.write(body.decode("utf-8"))
        self.stdout.write("signature_header:")
        self.stdout.write(f"X-Verification-Signature: sha256={signature}")
        self.stdout.write("curl:")
        self.stdout.write(
            "curl -X POST "
            f"\"$VERIFICATION_WEBHOOK_BASE_URL/api/v1/verification/webhooks/{provider}/\" "
            "-H 'Content-Type: application/json' "
            f"-H 'X-Verification-Signature: sha256={signature}' "
            f"--data '{body.decode('utf-8')}'"
        )
