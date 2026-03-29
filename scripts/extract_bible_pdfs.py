import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from pypdf import PdfReader


PDF_DIR = os.path.join(os.path.dirname(__file__), "..", "bible")
OUT_DIR = os.path.join(PDF_DIR, "json")


BOOKS = [
    "Genesis",
    "Exodus",
    "Leviticus",
    "Numbers",
    "Deuteronomy",
    "Joshua",
    "Judges",
    "Ruth",
    "1 Samuel",
    "2 Samuel",
    "1 Kings",
    "2 Kings",
    "1 Chronicles",
    "2 Chronicles",
    "Ezra",
    "Nehemiah",
    "Esther",
    "Job",
    "Psalms",
    "Proverbs",
    "Ecclesiastes",
    "Song of Solomon",
    "Isaiah",
    "Jeremiah",
    "Lamentations",
    "Ezekiel",
    "Daniel",
    "Hosea",
    "Joel",
    "Amos",
    "Obadiah",
    "Jonah",
    "Micah",
    "Nahum",
    "Habakkuk",
    "Zephaniah",
    "Haggai",
    "Zechariah",
    "Malachi",
    "Matthew",
    "Mark",
    "Luke",
    "John",
    "Acts",
    "Romans",
    "1 Corinthians",
    "2 Corinthians",
    "Galatians",
    "Ephesians",
    "Philippians",
    "Colossians",
    "1 Thessalonians",
    "2 Thessalonians",
    "1 Timothy",
    "2 Timothy",
    "Titus",
    "Philemon",
    "Hebrews",
    "James",
    "1 Peter",
    "2 Peter",
    "1 John",
    "2 John",
    "3 John",
    "Jude",
    "Revelation",
]


BOOK_ALIASES = {
    "psalm": "Psalms",
    "psalms": "Psalms",
    "song of songs": "Song of Solomon",
    "song of solomon": "Song of Solomon",
    "canticles": "Song of Solomon",
}


TRANSLATION_HINTS = {
    "kjv": "KJV",
    "king james": "KJV",
    "nkjv": "NKJV",
    "niv": "NIV",
    "new living translation": "NLT",
    "nlt": "NLT",
    "english standard version": "ESV",
    "esv": "ESV",
    "csb": "CSB",
    "christian standard bible": "CSB",
    "amp": "AMP",
    "amplified": "AMP",
    "good news": "GNB",
    "new revised standard": "NRSV",
    "american standard": "ASV",
}


@dataclass
class VerseState:
    number: Optional[int] = None
    chunks: List[str] = None

    def __post_init__(self):
        if self.chunks is None:
            self.chunks = []

    def text(self) -> str:
        return " ".join(self.chunks).strip()


def normalize_key(text: str) -> str:
    value = text.lower().strip()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value


def detect_translation(filename: str) -> str:
    key = normalize_key(filename)
    for hint, name in TRANSLATION_HINTS.items():
        if hint in key:
            return name
    return "UNKNOWN"


def match_book(line: str) -> Optional[str]:
    normalized = normalize_key(line)
    if not normalized:
        return None
    for alias, canonical in BOOK_ALIASES.items():
        if alias == normalized:
            return canonical
    for book in BOOKS:
        if normalize_key(book) == normalized:
            return book
    for book in BOOKS:
        if normalize_key(book) in normalized and len(normalized.split()) <= 5:
            return book
    return None


def match_chapter(line: str, book: Optional[str]) -> Optional[int]:
    if not line:
        return None
    stripped = line.strip()
    if re.fullmatch(r"\d{1,3}", stripped):
        return int(stripped)
    if book:
        pattern = rf"^{re.escape(book)}\s+(\d{{1,3}})$"
        match = re.match(pattern, stripped, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    match = re.match(r"^(chapter|chap|ch)\s+(\d{1,3})$", stripped, flags=re.IGNORECASE)
    if match:
        return int(match.group(2))
    return None


def split_verses(line: str) -> Optional[List[Tuple[int, str]]]:
    if not line or not re.match(r"^\d{1,3}\s", line):
        return None
    matches = list(re.finditer(r"(\d{1,3})\s", line))
    if not matches:
        return None
    result: List[Tuple[int, str]] = []
    for idx, match in enumerate(matches):
        verse_num = int(match.group(1))
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(line)
        text = line[start:end].strip()
        result.append((verse_num, text))
    return result


def build_structure(book_map: Dict[str, Dict[int, List[dict]]]) -> List[dict]:
    books = []
    for book in BOOKS:
        if book not in book_map:
            continue
        chapters = []
        for chapter_num in sorted(book_map[book].keys()):
            chapters.append(
                {
                    "number": chapter_num,
                    "verses": book_map[book][chapter_num],
                }
            )
        books.append({"name": book, "chapters": chapters})
    return books


def extract_pdf(path: str) -> dict:
    reader = PdfReader(path)
    book_map: Dict[str, Dict[int, List[dict]]] = {}
    current_book: Optional[str] = None
    current_chapter: Optional[int] = None
    last_book_line: Optional[str] = None
    current_verse = VerseState()

    def commit_verse():
        nonlocal current_verse
        if current_book and current_chapter and current_verse.number is not None:
            book_map.setdefault(current_book, {}).setdefault(current_chapter, []).append(
                {
                    "number": current_verse.number,
                    "text": current_verse.text(),
                }
            )
        current_verse = VerseState()

    for page in reader.pages:
        text = page.extract_text() or ""
        for raw_line in text.splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if not line:
                continue
            if re.fullmatch(r"\d{1,4}", line) and not last_book_line and not current_book:
                continue
            matched_book = match_book(line)
            if matched_book:
                commit_verse()
                current_book = matched_book
                current_chapter = None
                last_book_line = matched_book
                continue
            chapter_num = match_chapter(line, current_book)
            if chapter_num is not None:
                commit_verse()
                current_chapter = chapter_num
                last_book_line = None
                continue
            verses = split_verses(line)
            if verses and current_book and current_chapter:
                for verse_num, verse_text in verses:
                    commit_verse()
                    current_verse.number = verse_num
                    if verse_text:
                        current_verse.chunks.append(verse_text)
                last_book_line = None
                continue
            if current_verse.number is not None:
                current_verse.chunks.append(line)
            last_book_line = None

    commit_verse()
    return {"books": build_structure(book_map)}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    pdf_files = [f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf")]
    if not pdf_files:
        print("No PDF files found.")
        return
    for filename in sorted(pdf_files):
        path = os.path.join(PDF_DIR, filename)
        translation = detect_translation(filename)
        print(f"Processing {filename} -> {translation}")
        data = extract_pdf(path)
        out_path = os.path.join(OUT_DIR, f"{translation.lower()}-bible.json")
        output = {
            "translation": translation,
            "source_pdf": filename,
            "books": data["books"],
        }
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(output, handle, ensure_ascii=False, indent=2)
        print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
