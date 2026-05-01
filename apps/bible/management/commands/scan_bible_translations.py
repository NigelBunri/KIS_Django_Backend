from django.core.management.base import BaseCommand, CommandError

from apps.bible.importers import scan_bible_translation_registry


class Command(BaseCommand):
    help = "Scan root bible/<language>/<translation>.json files into the Bible translation registry."

    def add_arguments(self, parser):
        parser.add_argument("--root", default="bible", help="Bible data root directory.")
        parser.add_argument("--language", action="append", dest="languages", help="Language code to scan. Repeatable.")
        parser.add_argument("--translation", action="append", dest="translations", help="Translation name to scan. Repeatable.")

    def handle(self, *args, **options):
        try:
            scanned = scan_bible_translation_registry(
                root_dir=options["root"],
                languages=options.get("languages"),
                translations=options.get("translations"),
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        public_count = sum(1 for item in scanned if item.can_be_public)
        restricted_count = sum(1 for item in scanned if not item.can_be_public)
        self.stdout.write(
            self.style.SUCCESS(
                f"Scanned {len(scanned)} Bible translation file(s): {public_count} public/licensed, {restricted_count} private/restricted."
            )
        )
