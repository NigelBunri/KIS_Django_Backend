"""Scriptable helpers for loading the Bible JSON data into the Django models."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Sequence

from django.db import transaction

from .models import BibleBook, BibleChapter, BibleTranslation, BibleVerse

logger = logging.getLogger(__name__)

OT_BOOK_COUNT = 39
VERSE_BATCH_SIZE = 1000


def _sanitize_code(text: str, max_length: int = 30) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    cleaned = cleaned.upper()
    return cleaned[:max_length] if cleaned else ""


def _normalize_book_code(book_name: str) -> str:
    code = re.sub(r"[^A-Za-z0-9]+", "", book_name).upper()
    return code[:30] or book_name[:30].upper()


def _build_translation_code(language: str, name: str, existing_codes: dict[str, str]) -> str:
    base = f"{language}_{name}"
    sanitized = _sanitize_code(base, max_length=20)
    if not sanitized:
        sanitized = "TRANS"

    candidate = sanitized
    suffix = 1
    while candidate in existing_codes and existing_codes[candidate] != name:
        suffix_str = f"_{suffix}"
        allowed = 20 - len(suffix_str)
        candidate = (sanitized[:allowed] if allowed > 0 else "") + suffix_str
        suffix += 1
    existing_codes[candidate] = name
    return candidate


def _load_canonical_books(root_dir: Path) -> tuple[list[str], dict[str, list[int]]]:
    english_dir = root_dir / "en"
    if not english_dir.exists():
        raise ValueError(f"English reference directory was not found at {english_dir}")

    english_file = sorted(english_dir.glob("*.json"))[:1]
    if not english_file:
        raise ValueError("No English Bible JSON files were found for canonical book mapping.")

    with english_file[0].open(encoding="utf-8") as fh:
        data = json.load(fh)

    canonical_books = list(data.keys())
    chapter_structure: dict[str, list[int]] = {}
    for book_name, chapters in data.items():
        chapter_structure[book_name] = sorted(int(ch) for ch in chapters.keys())
    return canonical_books, chapter_structure


def _ensure_books_and_chapters(canonical_books: list[str], chapter_structure: dict[str, list[int]]) -> dict[tuple[int, int], BibleChapter]:
    chapter_lookup: dict[tuple[int, int], BibleChapter] = {}

    for book_index, book_name in enumerate(canonical_books):
        code = _normalize_book_code(book_name)
        testament = "OT" if book_index < OT_BOOK_COUNT else "NT"
        book, _ = BibleBook.objects.update_or_create(
            code=code,
            defaults={"name": book_name, "testament": testament, "order": book_index + 1},
        )
        chapter_numbers = chapter_structure.get(book_name, [])
        for chapter_number in chapter_numbers:
            chapter, _ = BibleChapter.objects.get_or_create(book=book, number=chapter_number)
            chapter_lookup[(book_index, chapter_number)] = chapter
    return chapter_lookup


def _collect_translation_files(
    root_dir: Path, languages: Sequence[str] | None, translations: Sequence[str] | None
) -> list[tuple[str, Path]]:
    language_filter = {lang.lower() for lang in languages} if languages else None
    translation_filter = {name.lower() for name in translations} if translations else None

    files: list[tuple[str, Path]] = []
    for language_dir in sorted(root_dir.iterdir()):
        if not language_dir.is_dir():
            continue
        lang_key = language_dir.name
        if language_filter and lang_key.lower() not in language_filter:
            continue
        for json_file in sorted(language_dir.glob("*.json")):
            translation_name = json_file.stem.strip()
            if translation_filter and translation_name.lower() not in translation_filter:
                continue
            files.append((lang_key, json_file))
    return files


def _import_translation(
    translation_path: Path,
    language: str,
    chapter_lookup: dict[tuple[int, int], BibleChapter],
    canonical_books: list[str],
    sort_index: int,
    existing_codes: dict[str, str],
) -> None:
    translation_name = translation_path.stem.strip()
    translation_code = _build_translation_code(language, translation_name, existing_codes)

    translation, _ = BibleTranslation.objects.update_or_create(
        code=translation_code,
        defaults={
            "name": translation_name,
            "language": language,
            "sort_order": sort_index,
            "is_active": True,
        },
    )

    total_imported = 0
    verses_to_create: list[BibleVerse] = []
    BibleVerse.objects.filter(translation=translation).delete()

    with translation_path.open(encoding="utf-8") as fh:
        data = json.load(fh)

    if len(data) != len(canonical_books):
        logger.warning(
            "Translation %s contains %s books but canonical list expects %s. Extra books will be skipped.",
            translation_path.name,
            len(data),
            len(canonical_books),
        )

    for book_index, (book_key, chapters) in enumerate(data.items()):
        if book_index >= len(canonical_books):
            break
        for chapter_key, verses in chapters.items():
            chapter_number = int(chapter_key)
            chapter = chapter_lookup.get((book_index, chapter_number))
            if not chapter:
                continue
            for verse_key, verse_text in verses.items():
                try:
                    verse_number = int(verse_key)
                except ValueError:
                    continue
                if verse_text is None:
                    continue
                verses_to_create.append(
                    BibleVerse(
                        translation=translation,
                        chapter=chapter,
                        number=verse_number,
                        text=str(verse_text).strip(),
                    )
                )
                if len(verses_to_create) >= VERSE_BATCH_SIZE:
                    BibleVerse.objects.bulk_create(verses_to_create)
                    total_imported += len(verses_to_create)
                    verses_to_create.clear()

    if verses_to_create:
        BibleVerse.objects.bulk_create(verses_to_create)
        total_imported += len(verses_to_create)

    logger.info("Imported %s verses for translation %s (%s).", total_imported, translation.name, translation_code)


def import_bible_translations(
    root_dir: Path | str | None = None,
    languages: Sequence[str] | None = None,
    translations: Sequence[str] | None = None,
) -> list[str]:
    base_path = Path(root_dir) if root_dir else Path("bible")
    if not base_path.exists():
        raise ValueError(f"Bible root directory not found: {base_path}")

    canonical_books, chapter_structure = _load_canonical_books(base_path)
    chapter_lookup = _ensure_books_and_chapters(canonical_books, chapter_structure)
    translation_files = _collect_translation_files(base_path, languages, translations)

    if not translation_files:
        raise ValueError("No translation JSON files were found with the provided filters.")

    existing_codes = dict(BibleTranslation.objects.values_list("code", "name"))
    imported_codes: list[str] = []
    for sort_index, (language, translation_path) in enumerate(translation_files, start=1):
        with transaction.atomic():
            _import_translation(
                translation_path=translation_path,
                language=language,
                chapter_lookup=chapter_lookup,
                canonical_books=canonical_books,
                sort_index=sort_index,
                existing_codes=existing_codes,
            )
            imported_codes.append(translation_path.stem)
    return imported_codes
