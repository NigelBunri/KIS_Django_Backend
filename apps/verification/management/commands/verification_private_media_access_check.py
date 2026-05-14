from django.core.management.base import BaseCommand, CommandError

from apps.media.models import MediaAsset
from apps.media.views import MEDIA_SIGNED_URL_TTL_SECONDS, _asset_is_private, _sign_media_asset, _token_allows_asset


class Command(BaseCommand):
    help = "Validate private media signed-access readiness for verification evidence without printing file contents."

    def add_arguments(self, parser):
        parser.add_argument("--asset-id", default="", help="Optional MediaAsset id to validate.")

    def handle(self, *args, **options):
        asset_id = str(options.get("asset_id") or "").strip()
        if not asset_id:
            self.stdout.write(
                self.style.WARNING(
                    "No --asset-id supplied. Readiness command loaded; run with a private MediaAsset id from staging "
                    "to prove signed access before live provider enablement."
                )
            )
            return

        try:
            asset = MediaAsset.objects.get(id=asset_id, is_deleted=False)
        except MediaAsset.DoesNotExist as exc:
            raise CommandError("MediaAsset was not found.") from exc

        is_private = _asset_is_private(asset)
        token = _sign_media_asset(asset)
        token_valid = _token_allows_asset(token, asset)
        self.stdout.write(
            self.style.SUCCESS(
                f"media asset signed-access check: asset={asset.id} private={str(is_private).lower()} "
                f"token_valid={str(token_valid).lower()} ttl_seconds={MEDIA_SIGNED_URL_TTL_SECONDS}"
            )
        )
        if not is_private:
            raise CommandError("Asset is not marked private/restricted; do not use it for verification evidence.")
        if not token_valid:
            raise CommandError("Generated signed token did not validate for this asset.")
