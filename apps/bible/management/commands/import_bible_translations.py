from django.core.management.base import BaseCommand, CommandError

from apps.bible.importers import import_bible_translations


class Command(BaseCommand):
    help = "Import Bible JSON files from bible/<language>/<translation>.json into reader models."

    def add_arguments(self, parser):
        parser.add_argument("--root", default="bible", help="Bible data root directory.")
        parser.add_argument("--language", action="append", dest="languages", help="Language code to import. Repeatable.")
        parser.add_argument("--translation", action="append", dest="translations", help="Translation name to import. Repeatable.")

    def handle(self, *args, **options):
        try:
            imported = import_bible_translations(
                root_dir=options["root"],
                languages=options.get("languages"),
                translations=options.get("translations"),
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"Imported {len(imported)} Bible translation(s): {', '.join(imported)}"))
