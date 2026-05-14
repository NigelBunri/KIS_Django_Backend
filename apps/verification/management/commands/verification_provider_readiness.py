from django.core.management.base import BaseCommand

from apps.verification.providers import ADAPTERS, provider_public_status


class Command(BaseCommand):
    help = "Print non-secret verification provider readiness status."

    def handle(self, *args, **options):
        seen = []
        for name in ADAPTERS:
            canonical = provider_public_status(name)["name"]
            if canonical in seen:
                continue
            seen.append(canonical)
            status = provider_public_status(canonical)
            self.stdout.write(
                f"{status['name']}: configured={str(status['configured']).lower()} "
                f"live_calls_enabled={str(status['live_calls_enabled']).lower()} "
                f"sandbox_enabled={str(status.get('sandbox_enabled')).lower()} "
                f"sandbox_network_enabled={str(status.get('sandbox_network_enabled')).lower()} "
                f"live_call_made=false"
            )
