import re
from dataclasses import dataclass

from .models import BibleBook


class PassageReferenceError(ValueError):
    pass


BOOK_ALIASES = {
    "gen": "GENESIS",
    "ge": "GENESIS",
    "jn": "JOHN",
    "jhn": "JOHN",
    "rom": "ROMANS",
    "ps": "PSALMS",
    "psa": "PSALMS",
    "psalm": "PSALMS",
    "prov": "PROVERBS",
    "pr": "PROVERBS",
    "rev": "REVELATION",
    "1 jn": "1JOHN",
    "2 jn": "2JOHN",
    "3 jn": "3JOHN",
}


@dataclass(frozen=True)
class ParsedPassage:
    book: BibleBook
    chapter: int
    start_verse: int | None = None
    end_verse: int | None = None

    @property
    def is_chapter(self) -> bool:
        return self.start_verse is None

    @property
    def display_ref(self) -> str:
        if self.is_chapter:
            return f"{self.book.name} {self.chapter}"
        if self.end_verse and self.end_verse != self.start_verse:
            return f"{self.book.name} {self.chapter}:{self.start_verse}-{self.end_verse}"
        return f"{self.book.name} {self.chapter}:{self.start_verse}"


def _clean_reference(reference: str) -> str:
    return re.sub(r"\s+", " ", (reference or "").strip())


def _book_candidates() -> list[tuple[str, BibleBook]]:
    candidates: list[tuple[str, BibleBook]] = []
    for book in BibleBook.objects.all().order_by("-name"):
        candidates.append((book.name.lower(), book))
        candidates.append((book.code.lower(), book))
    for alias, code in BOOK_ALIASES.items():
        book = BibleBook.objects.filter(code__iexact=code).first()
        if book:
            candidates.append((alias, book))
    return sorted(candidates, key=lambda item: len(item[0]), reverse=True)


def parse_passage_reference(reference: str) -> ParsedPassage:
    cleaned = _clean_reference(reference)
    if not cleaned:
        raise PassageReferenceError("A passage reference is required.")

    lowered = cleaned.lower()
    match_book = None
    remainder = ""
    for candidate, book in _book_candidates():
        if lowered == candidate:
            match_book = book
            remainder = ""
            break
        if lowered.startswith(f"{candidate} "):
            match_book = book
            remainder = cleaned[len(candidate) :].strip()
            break

    if not match_book:
        raise PassageReferenceError("Book was not recognized.")
    if not remainder:
        raise PassageReferenceError("Chapter number is required.")

    match = re.fullmatch(r"(\d+)(?::(\d+)(?:-(\d+))?)?", remainder)
    if not match:
        raise PassageReferenceError("Only same-chapter references like John 3 or John 3:16-18 are supported.")

    chapter = int(match.group(1))
    start_verse = int(match.group(2)) if match.group(2) else None
    end_verse = int(match.group(3)) if match.group(3) else start_verse
    if end_verse and start_verse and end_verse < start_verse:
        raise PassageReferenceError("Verse range end cannot be before the start verse.")

    return ParsedPassage(book=match_book, chapter=chapter, start_verse=start_verse, end_verse=end_verse)
