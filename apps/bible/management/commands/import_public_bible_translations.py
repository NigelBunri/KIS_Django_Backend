from django.core.management.base import BaseCommand, CommandError

from apps.bible.importers import import_bible_translations, scan_bible_translation_registry
from apps.bible.models import BibleTranslationMetadata, BibleTranslationValidationStatus


class Command(BaseCommand):
    help = (
        "Scan bible/<language>/<translation>.json and import only translations that are "
        "public, licensed, valid/warning, and import-enabled."
    )

    def add_arguments(self, parser):
        parser.add_argument("--root", default="bible", help="Bible data root directory.")
        parser.add_argument("--language", action="append", dest="languages", help="Language code to import. Repeatable.")
        parser.add_argument("--translation", action="append", dest="translations", help="Translation name to import. Repeatable.")
        parser.add_argument(
            "--include-partial",
            action="store_true",
            help="Also import public/licensed partial Bibles. By default only 66-book Bibles are imported.",
        )

    def handle(self, *args, **options):
        root = options["root"]
        languages = options.get("languages")
        translations = options.get("translations")
        include_partial = options["include_partial"]

        try:
            scan_bible_translation_registry(root_dir=root, languages=languages, translations=translations)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        qs = BibleTranslationMetadata.objects.filter(
            is_public=True,
            is_licensed=True,
            import_enabled=True,
            validation_status__in=[
                BibleTranslationValidationStatus.VALID,
                BibleTranslationValidationStatus.WARNING,
            ],
        )
        if languages:
            qs = qs.filter(language__in=languages)
        if translations:
            qs = qs.filter(full_name__in=translations)
        if not include_partial:
            qs = qs.filter(book_count=66)

        candidates = list(qs.order_by("language", "full_name"))
        if not candidates:
            self.stdout.write(self.style.WARNING("No public/licensed import-enabled Bible translations matched."))
            return

        imported: list[str] = []
        skipped: list[str] = []
        for metadata in candidates:
            try:
                imported.extend(
                    import_bible_translations(
                        root_dir=root,
                        languages=[metadata.language],
                        translations=[metadata.full_name],
                    )
                )
            except Exception as exc:  # pragma: no cover - management command reporting path.
                skipped.append(f"{metadata.language}/{metadata.full_name}: {exc}")

        if skipped:
            for item in skipped:
                self.stdout.write(self.style.WARNING(f"Skipped {item}"))
        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {len(imported)} public/licensed Bible translation(s): {', '.join(imported)}"
            )
        )
