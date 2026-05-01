"""Scriptable helpers for loading the Bible JSON data into the Django models."""

from __future__ import annotations

import json
import logging
import re
import hashlib
from pathlib import Path
from typing import Any, Sequence

from django.db import transaction
from django.utils import timezone

from .models import (
    BibleBook,
    BibleChapter,
    BibleTranslation,
    BibleTranslationCopyrightStatus,
    BibleTranslationLicenseReviewStatus,
    BibleTranslationMetadata,
    BibleTranslationValidationStatus,
    BibleVerse,
)

logger = logging.getLogger(__name__)

OT_BOOK_COUNT = 39
VERSE_BATCH_SIZE = 1000

PUBLIC_DOMAIN_HINTS = {
    "ALEPPO CODEX",
    "AMERICAN STANDARD VERSION",
    "ASV",
    "BIBLE (1776)",
    "BRENTON SEPTUAGINT",
    "DARBY",
    "DOUAY",
    "ENGLISH REVISED VERSION",
    "JPS TANAKH 1917",
    "KING JAMES",
    "KJV",
    "LATIN: VULGATA",
    "LOUIS SEGOND",
    "LUTHER (1912)",
    "MAORI",
    "REINA VALERA 1909",
    "RIVEDUTA",
    "SMITH & VAN DYKE",
    "STATEN VERTALING",
    "SWEDISH (1917)",
    "WEBSTER",
    "WESTMINSTER LENINGRAD CODEX",
    "WORLD ENGLISH BIBLE",
    "YOUNG",
}

RESTRICTED_HINTS = {
    "AMPLIFIED",
    "BEREAN",
    "CHRISTIAN STANDARD BIBLE",
    "CONTEMPORARY ENGLISH VERSION",
    "ENGLISH STANDARD VERSION",
    "ESV",
    "GOOD NEWS",
    "GOD'S WORD",
    "HOLMAN",
    "INTERNATIONAL STANDARD VERSION",
    "LEGACY STANDARD BIBLE",
    "MAJORITY STANDARD BIBLE",
    "NASB",
    "NET BIBLE",
    "NEW AMERICAN",
    "NEW HEART ENGLISH",
    "NEW INTERNATIONAL VERSION",
    "NEW KING JAMES",
    "NEW LIVING TRANSLATION",
    "NEW REVISED STANDARD",
    "NIV",
    "NKJV",
    "NLT",
}


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


def _classify_copyright_status(name: str) -> tuple[str, bool, bool, str]:
    upper_name = name.upper()
    if any(hint in upper_name for hint in RESTRICTED_HINTS):
        return (
            BibleTranslationCopyrightStatus.RESTRICTED,
            False,
            False,
            "Recognized as a modern/copyrighted translation. Keep private until a license is recorded.",
        )
    if any(hint in upper_name for hint in PUBLIC_DOMAIN_HINTS):
        return (
            BibleTranslationCopyrightStatus.PUBLIC_DOMAIN,
            True,
            True,
            "Auto-classified as public-domain/open based on the translation name. Verify before production launch.",
        )
    return (
        BibleTranslationCopyrightStatus.UNKNOWN,
        False,
        False,
        "Copyright status is unknown. Keep private until manually reviewed.",
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_translation_payload(data: Any) -> tuple[int, int, int, list[str]]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return 0, 0, 0, ["Root JSON value must be an object keyed by book name."]

    book_count = len(data)
    chapter_count = 0
    verse_count = 0
    for book_name, chapters in data.items():
        if not isinstance(chapters, dict):
            errors.append(f"{book_name}: chapters must be an object.")
            continue
        chapter_count += len(chapters)
        for chapter_key, verses in chapters.items():
            try:
                int(chapter_key)
            except (TypeError, ValueError):
                errors.append(f"{book_name}: invalid chapter key {chapter_key!r}.")
            if not isinstance(verses, dict):
                errors.append(f"{book_name} {chapter_key}: verses must be an object.")
                continue
            for verse_key, verse_text in verses.items():
                try:
                    int(verse_key)
                except (TypeError, ValueError):
                    errors.append(f"{book_name} {chapter_key}: invalid verse key {verse_key!r}.")
                    continue
                if verse_text in (None, ""):
                    errors.append(f"{book_name} {chapter_key}:{verse_key}: verse text is empty.")
                    continue
                verse_count += 1
    return book_count, chapter_count, verse_count, errors


def validate_translation_file(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        return {
            "book_count": 0,
            "chapter_count": 0,
            "verse_count": 0,
            "validation_status": BibleTranslationValidationStatus.ERROR,
            "validation_errors": [f"Invalid JSON: {exc}"],
            "source_hash": "",
        }

    book_count, chapter_count, verse_count, errors = _count_translation_payload(data)
    if not errors and book_count > 0 and chapter_count > 0 and verse_count > 0:
        status = BibleTranslationValidationStatus.VALID
    elif book_count > 0 and verse_count > 0:
        status = BibleTranslationValidationStatus.WARNING
    else:
        status = BibleTranslationValidationStatus.ERROR

    return {
        "book_count": book_count,
        "chapter_count": chapter_count,
        "verse_count": verse_count,
        "validation_status": status,
        "validation_errors": errors[:200],
        "source_hash": _file_sha256(path),
    }


def _metadata_for_file(
    language: str,
    path: Path,
    root_dir: Path,
    sort_index: int,
    existing_codes: dict[str, str],
) -> BibleTranslationMetadata:
    translation_name = path.stem.strip()
    code = _build_translation_code(language, translation_name, existing_codes)
    copyright_status, is_licensed, is_public, license_notes = _classify_copyright_status(translation_name)
    review_status = (
        BibleTranslationLicenseReviewStatus.NOT_REQUIRED
        if copyright_status == BibleTranslationCopyrightStatus.PUBLIC_DOMAIN
        else BibleTranslationLicenseReviewStatus.PENDING
    )
    validation = validate_translation_file(path)
    defaults = {
        "translation": BibleTranslation.objects.filter(code=code).first(),
        "language": language,
        "display_language": language,
        "abbreviation": _sanitize_code(translation_name, max_length=40),
        "full_name": translation_name,
        "source_path": str(path.relative_to(root_dir)),
        "source_filename": path.name,
        "copyright_status": copyright_status,
        "license_notes": license_notes,
        "license_review_status": review_status,
        "is_licensed": is_licensed,
        "is_public": is_public and is_licensed,
        "import_enabled": is_licensed,
        "last_scanned_at": timezone.now(),
        **validation,
    }
    metadata, created = BibleTranslationMetadata.objects.update_or_create(code=code, defaults=defaults)
    if not created:
        manual_license = metadata.copyright_status == BibleTranslationCopyrightStatus.LICENSED
        if manual_license:
            BibleTranslationMetadata.objects.filter(id=metadata.id).update(
                source_hash=validation["source_hash"],
                book_count=validation["book_count"],
                chapter_count=validation["chapter_count"],
                verse_count=validation["verse_count"],
                validation_status=validation["validation_status"],
                validation_errors=validation["validation_errors"],
                last_scanned_at=timezone.now(),
            )
            metadata.refresh_from_db()
    return metadata


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
    metadata = BibleTranslationMetadata.objects.filter(code=translation_code).first()
    if metadata:
        metadata.translation = translation
        metadata.last_imported_at = timezone.now()
        metadata.save(update_fields=["translation", "last_imported_at", "updated_at"])

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

    scan_bible_translation_registry(base_path, languages=languages, translations=translations)
    existing_codes = dict(
        BibleTranslationMetadata.objects.values_list("code", "full_name")
    ) | dict(BibleTranslation.objects.values_list("code", "name"))
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


def scan_bible_translation_registry(
    root_dir: Path | str | None = None,
    languages: Sequence[str] | None = None,
    translations: Sequence[str] | None = None,
) -> list[BibleTranslationMetadata]:
    base_path = Path(root_dir) if root_dir else Path("bible")
    if not base_path.exists():
        raise ValueError(f"Bible root directory not found: {base_path}")

    translation_files = _collect_translation_files(base_path, languages, translations)
    if not translation_files:
        raise ValueError("No translation JSON files were found with the provided filters.")

    existing_codes = dict(
        BibleTranslationMetadata.objects.values_list("code", "full_name")
    ) | dict(BibleTranslation.objects.values_list("code", "name"))
    scanned: list[BibleTranslationMetadata] = []
    for sort_index, (language, translation_path) in enumerate(translation_files, start=1):
        scanned.append(
            _metadata_for_file(
                language=language,
                path=translation_path,
                root_dir=base_path,
                sort_index=sort_index,
                existing_codes=existing_codes,
            )
        )
    return scanned
