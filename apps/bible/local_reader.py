"""Small public-domain Bible reader fallback for deployments before DB import.

This is intentionally limited to KJV so the public reader can keep working on
fresh/staging databases without exposing restricted translation files.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings

KJV_CODE = "EN_KING_JAMES_BIBLE"
KJV_NAME = "KING JAMES BIBLE"
OT_BOOK_COUNT = 39


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


@lru_cache(maxsize=1)
def _kjv_payload() -> dict[str, dict[str, dict[str, str]]]:
    path = Path(settings.BASE_DIR) / "bible" / "en" / "KING JAMES BIBLE.json"
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def _book_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, name in enumerate(_kjv_payload().keys(), start=1):
        rows.append(
            {
                "id": f"local_book_{index}",
                "code": re.sub(r"[^A-Za-z0-9]+", "", name).upper()[:30],
                "name": name,
                "testament": "OT" if index <= OT_BOOK_COUNT else "NT",
                "order": index,
            }
        )
    return rows


@lru_cache(maxsize=1)
def _book_aliases() -> dict[str, dict[str, Any]]:
    aliases: dict[str, dict[str, Any]] = {}
    for row in _book_rows():
        aliases[_normalize(row["name"])] = row
        aliases[_normalize(row["code"])] = row
    aliases.update(
        {
            "ps": aliases.get("psalms"),
            "psa": aliases.get("psalms"),
            "psalm": aliases.get("psalms"),
            "jn": aliases.get("john"),
            "jhn": aliases.get("john"),
            "rev": aliases.get("revelation"),
        }
    )
    return {key: value for key, value in aliases.items() if value}


def _translation_allowed(identifier: str | None) -> bool:
    if not identifier:
        return True
    normalized = _normalize(identifier)
    return normalized in {
        _normalize(KJV_CODE),
        _normalize(KJV_NAME),
        "kjv",
        "kingjames",
        "kingjamesbible",
    }


def local_kjv_translation() -> dict[str, Any]:
    return {
        "id": "local_kjv",
        "code": KJV_CODE,
        "name": KJV_NAME,
        "language": "en",
        "sort_order": 1,
        "is_active": True,
        "metadata_status": "valid",
        "copyright_status": "public_domain",
        "is_public": True,
        "is_licensed": True,
    }


def _book_for_identifier(identifier: str | None) -> dict[str, Any] | None:
    if identifier:
        found = _book_aliases().get(_normalize(identifier))
        if found:
            return found
    rows = _book_rows()
    return rows[0] if rows else None


def _chapter_payload(book: dict[str, Any], chapter_number: int) -> dict[str, Any]:
    return {
        "id": f"local_ch_{book['code']}_{chapter_number}",
        "book": book,
        "number": chapter_number,
    }


def _chapter_numbers(book: dict[str, Any]) -> list[int]:
    chapters = _kjv_payload().get(book["name"], {})
    numbers: list[int] = []
    for key in chapters.keys():
        try:
            numbers.append(int(key))
        except (TypeError, ValueError):
            continue
    return sorted(numbers)


def _navigation(book: dict[str, Any], chapter_number: int) -> dict[str, Any]:
    numbers = _chapter_numbers(book)
    previous_chapter = None
    next_chapter = None
    if chapter_number in numbers:
        idx = numbers.index(chapter_number)
        if idx > 0:
            previous_chapter = _chapter_payload(book, numbers[idx - 1])
        if idx + 1 < len(numbers):
            next_chapter = _chapter_payload(book, numbers[idx + 1])
    return {"previous": previous_chapter, "next": next_chapter}


def _verses(book: dict[str, Any], chapter_number: int, start_verse: int | None, end_verse: int | None) -> list[dict[str, Any]]:
    raw = _kjv_payload().get(book["name"], {}).get(str(chapter_number), {})
    rows: list[dict[str, Any]] = []
    for key, text in raw.items():
        try:
            number = int(key)
        except (TypeError, ValueError):
            continue
        if start_verse and number < start_verse:
            continue
        if end_verse and number > end_verse:
            continue
        rows.append(
            {
                "id": f"local_v_{book['code']}_{chapter_number}_{number}",
                "translation": "local_kjv",
                "chapter": _chapter_payload(book, chapter_number),
                "number": number,
                "text": str(text or "").strip(),
            }
        )
    return sorted([row for row in rows if row["text"]], key=lambda row: row["number"])


def _parse_reference(reference: str) -> tuple[dict[str, Any], int, int | None, int | None, str]:
    cleaned = re.sub(r"\s+", " ", str(reference or "").strip())
    lowered = cleaned.lower()
    for row in sorted(_book_rows(), key=lambda item: len(item["name"]), reverse=True):
        candidates = {row["name"].lower(), row["code"].lower()}
        for candidate in candidates:
            if lowered == candidate or lowered.startswith(f"{candidate} "):
                remainder = cleaned[len(candidate):].strip()
                match = re.fullmatch(r"(\d+)(?::(\d+)(?:-(\d+))?)?", remainder)
                if not match:
                    raise ValueError("Only same-chapter references like John 3 or John 3:16-18 are supported.")
                chapter = int(match.group(1))
                start = int(match.group(2)) if match.group(2) else None
                end = int(match.group(3)) if match.group(3) else start
                if start and end and end < start:
                    raise ValueError("Verse range end cannot be before the start verse.")
                if start:
                    label = f"{row['name']} {chapter}:{start}" + (f"-{end}" if end and end != start else "")
                else:
                    label = f"{row['name']} {chapter}"
                return row, chapter, start, end, label
    raise ValueError("Book was not recognized.")


def build_local_kjv_reader_payload(
    *,
    translation_identifier: str | None = None,
    book_identifier: str | None = None,
    chapter_number: int | str | None = None,
    reference: str | None = None,
    start_verse: int | str | None = None,
    end_verse: int | str | None = None,
) -> dict[str, Any] | None:
    if not _translation_allowed(translation_identifier):
        return None
    try:
        if reference:
            book, chapter, start, end, reference_label = _parse_reference(reference)
        else:
            book = _book_for_identifier(book_identifier)
            if not book:
                return None
            chapter = int(chapter_number or 1)
            start = int(start_verse) if start_verse else None
            end = int(end_verse) if end_verse else start
            reference_label = f"{book['name']} {chapter}"
            if start:
                reference_label = f"{book['name']} {chapter}:{start}" + (f"-{end}" if end and end != start else "")
    except (TypeError, ValueError):
        return None

    verse_rows = _verses(book, chapter, start, end)
    if not verse_rows:
        return None
    return {
        "translation": local_kjv_translation(),
        "book": book,
        "chapter": _chapter_payload(book, chapter),
        "reference": reference_label,
        "navigation": _navigation(book, chapter),
        "verses": verse_rows,
        "audio": None,
        "source": "bundled_kjv_fallback",
    }
