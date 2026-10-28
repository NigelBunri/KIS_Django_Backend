"""
Manuscript validation and conversion: turns an uploaded PDF/DOCX/TXT into
the structured BookChapter.content_blocks representation the native KIS
reader renders — never a raw file blob.

Scope honestly documented: this performs text-layer extraction (pypdf,
python-docx), OCR for scanned/image-only PDF pages (PyMuPDF + Tesseract,
requires the `tesseract` binary on the host — see requirements.txt and the
deployment docs), heading-heuristic chapter splitting, and embedded-image
detection/extraction. Image placement precision differs honestly by
format: DOCX images are positioned exactly where they occur in the
document's own paragraph order (see _docx_paragraph_images); PDF images
have no reliable sub-page position available from pypdf, so they are
placed at the end of their source page's text and tagged
`position_confidence: "page_level"` rather than claimed exact. Either
way, a detected-but-unextractable image (a linked-not-embedded DOCX
image, or a page.images entry pypdf can't decode) still produces a real
"image" content block with `extraction_status: "missing"` at its correct
position — authors supply the file themselves in the image review step
(apps.books.services.list_image_placements /
assign_image_to_placement) — never silently dropped.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
from dataclasses import dataclass, field

# Only these three are ever accepted — matches the product requirement
# exactly (no legacy DOC/EPUB/MOBI/HTML/images/executables).
ACCEPTED_EXTENSIONS = {"pdf", "docx", "txt"}
ACCEPTED_MIME_TYPES = {
    "pdf": {"application/pdf"},
    "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "txt": {"text/plain"},
}
# Magic-byte signatures checked server-side regardless of client-supplied
# Content-Type or filename extension — never trust either alone.
_SIGNATURES = {
    "pdf": (b"%PDF-",),
    "docx": (b"PK\x03\x04",),  # DOCX is a zip container
}

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
MAX_PDF_PAGES = 2000
MIN_WORDS_FOR_VALID_BOOK = 50

logger = logging.getLogger("security.books")


class ManuscriptValidationError(Exception):
    def __init__(self, message: str, code: str = "invalid_manuscript"):
        super().__init__(message)
        self.code = code


@dataclass
class ExtractedChapter:
    title: str
    blocks: list[dict] = field(default_factory=list)
    source_page_range: str = ""

    @property
    def word_count(self) -> int:
        return sum(len(b.get("text", "").split()) for b in self.blocks if b.get("text"))


@dataclass
class ConversionResult:
    chapters: list[ExtractedChapter]
    warnings: list[str]
    confidence_score: float
    images_extracted: int = 0
    total_word_count: int = 0


def sniff_extension(filename: str) -> str:
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    return ext


def validate_upload(*, filename: str, content_type: str, data: bytes) -> str:
    """Validates extension, MIME, magic bytes, and size. Returns the
    normalized format ('pdf'|'docx'|'txt') or raises ManuscriptValidationError.
    Treats the file as fully untrusted input."""
    ext = sniff_extension(filename)
    if ext not in ACCEPTED_EXTENSIONS:
        raise ManuscriptValidationError(
            f"Unsupported file type '.{ext}'. Only PDF, DOCX, and TXT manuscripts are accepted.",
            code="unsupported_format",
        )
    if len(data) == 0:
        raise ManuscriptValidationError("The uploaded file is empty.", code="empty_file")
    if len(data) > MAX_FILE_SIZE_BYTES:
        raise ManuscriptValidationError(
            f"File exceeds the {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB manuscript size limit.",
            code="file_too_large",
        )

    declared_mime = (content_type or "").split(";")[0].strip().lower()
    if ext in ACCEPTED_MIME_TYPES and declared_mime and declared_mime not in ACCEPTED_MIME_TYPES[ext]:
        raise ManuscriptValidationError(
            "The file's declared type does not match its extension — upload rejected for safety.",
            code="mime_mismatch",
        )

    if ext in _SIGNATURES:
        head = data[:8]
        if not any(head.startswith(sig) for sig in _SIGNATURES[ext]):
            raise ManuscriptValidationError(
                "The file's contents do not match a valid PDF/DOCX file — it may be corrupted, "
                "mislabeled, or disguised. Upload rejected.",
                code="signature_mismatch",
            )
    if ext == "txt":
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise ManuscriptValidationError(
                "The text file is not valid UTF-8 text.", code="invalid_encoding"
            )
    return ext


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_HEADING_RE = re.compile(
    r"^\s*(chapter|part|section|book)\s+([0-9]+|[ivxlc]+)\b.*$|^\s*[0-9]{1,3}[.)]\s+\S.*$",
    re.IGNORECASE,
)


def _looks_like_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 120:
        return False
    if _HEADING_RE.match(stripped):
        return True
    # ALL-CAPS short line with no terminal punctuation — a common heading shape.
    letters = [c for c in stripped if c.isalpha()]
    if letters and all(c.isupper() for c in letters) and len(stripped.split()) <= 10 and not stripped.endswith((".", ",", ";")):
        return True
    return False


def _image_item(*, data: bytes | None, content_type: str = "", page: int | None = None, position_confidence: str = "exact") -> dict:
    """A placeholder "item" fed into _paragraphs_to_chapters alongside
    plain paragraph strings — turned into a stable "image" content block
    at its real position in the reading-order stream, never collected
    separately and appended afterward. `_raw_bytes`/`_content_type` are
    transient keys process_manuscript consumes to create a
    BookImageAsset and strips before the block is ever persisted/
    encrypted (see services.process_manuscript)."""
    return {
        "__image__": True, "data": data, "content_type": content_type,
        "page": page, "position_confidence": position_confidence,
    }


def _paragraphs_to_chapters(items: list, *, warnings: list[str]) -> list[ExtractedChapter]:
    """`items` is a reading-order mix of plain paragraph/heading strings
    and _image_item() dicts — an image item becomes an "image" content
    block at exactly this position, not appended to the end of the
    chapter or collected into a gallery."""
    chapters: list[ExtractedChapter] = []
    current: ExtractedChapter | None = None

    def start_chapter(title: str):
        nonlocal current
        current = ExtractedChapter(title=title or f"Chapter {len(chapters) + 1}")
        chapters.append(current)

    for item in items:
        if isinstance(item, dict) and item.get("__image__"):
            if current is None:
                start_chapter("Chapter 1")
            current.blocks.append({
                "type": "image",
                "_raw_bytes": item.get("data"),
                "_content_type": item.get("content_type", ""),
                "source_page": item.get("page"),
                "extraction_status": "extracted" if item.get("data") else "missing",
                "position_confidence": item.get("position_confidence", "exact"),
                "caption": "",
                "alt_text": "",
            })
            continue

        if isinstance(item, dict) and item.get("__heading__"):
            line = str(item.get("text", "")).strip()
            if not line:
                continue
            if current is None or len(current.blocks) > 0:
                start_chapter(line.title() if line.isupper() else line)
            current.blocks.append({"type": "heading", "text": line})
            continue

        line = str(item).strip()
        if not line:
            continue
        if _looks_like_heading(line) and (current is None or len(current.blocks) > 0):
            start_chapter(line.title() if line.isupper() else line)
            current.blocks.append({"type": "heading", "text": line})
            continue
        if current is None:
            start_chapter("Chapter 1")
        current.blocks.append({"type": "paragraph", "text": line})

    if not chapters:
        warnings.append("No chapter or heading boundaries were detected; the whole manuscript was kept as a single chapter.")
    return chapters


def convert_txt(data: bytes) -> ConversionResult:
    warnings: list[str] = []
    text = data.decode("utf-8", errors="replace")
    raw_paragraphs = re.split(r"\n\s*\n|\r\n\s*\r\n", text)
    chapters = _paragraphs_to_chapters(raw_paragraphs, warnings=warnings)
    total_words = sum(c.word_count for c in chapters)
    if total_words < MIN_WORDS_FOR_VALID_BOOK:
        warnings.append("Extracted text is unusually short — verify the manuscript uploaded correctly.")
    return ConversionResult(chapters=chapters, warnings=warnings, confidence_score=0.9, total_word_count=total_words)


def _docx_paragraph_images(document, para) -> list[tuple[bytes | None, str]]:
    """Every inline/anchored image occurring inside this paragraph, in
    document order, as (image_bytes_or_None, content_type). Returns one
    entry per occurrence even when two occurrences share the same
    underlying image part — each is a distinct placement (see
    services.create_or_get_image_asset for where the dedup actually
    happens, by content hash, not here)."""
    from docx.oxml.ns import qn

    results: list[tuple[bytes | None, str]] = []
    for blip in para._element.findall(".//" + qn("a:blip")):
        rel_id = blip.get(qn("r:embed")) or blip.get(qn("r:link"))
        if not rel_id:
            continue
        try:
            part = document.part.rels[rel_id].target_part
            results.append((part.blob, part.content_type or ""))
        except Exception:
            # A relationship id was present but couldn't be resolved
            # (e.g. a linked, not embedded, external image) — still a
            # real detected placement, just one the author must supply.
            results.append((None, ""))
    return results


def convert_docx(data: bytes) -> ConversionResult:
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover - dependency documented in requirements.txt
        raise ManuscriptValidationError(f"DOCX support is unavailable on this server: {exc}", code="dependency_missing")

    warnings: list[str] = []
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise ManuscriptValidationError(f"Could not parse DOCX file: {exc}", code="parse_failed")

    # Reading order in a .docx is exactly document.paragraphs' order —
    # an image-only paragraph has empty `.text` but may still contain one
    # or more drawings, so it must NOT be skipped the way a genuinely
    # empty text paragraph is. Each paragraph contributes, in order: any
    # images found in its runs, then its own text (if any) — matching
    # how Word visually lays out an inline image followed by its
    # caption paragraph.
    items: list = []
    images_extracted = 0
    images_unresolved = 0
    for para in document.paragraphs:
        for image_bytes, content_type in _docx_paragraph_images(document, para):
            items.append(_image_item(data=image_bytes, content_type=content_type))
            if image_bytes:
                images_extracted += 1
            else:
                images_unresolved += 1
        text = para.text.strip()
        if not text:
            continue
        style_name = (para.style.name or "").lower() if para.style else ""
        # DOCX carries real style info ("Heading 1"/"Title") the plain
        # heuristic _looks_like_heading can't see — a heading tagged by
        # style forces a chapter break even on text too plain for the
        # heuristic alone (e.g. a lone word like "Introduction").
        is_style_heading = style_name.startswith("heading") or style_name == "title"
        if is_style_heading:
            items.append({"__heading__": True, "text": text})
        else:
            items.append(text)
    chapters = _paragraphs_to_chapters(items, warnings=warnings)

    if images_unresolved:
        warnings.append(
            f"{images_unresolved} embedded image(s) were detected but could not be extracted automatically "
            "(e.g. a linked rather than embedded image) — upload them manually in the image review step."
        )
    if images_extracted:
        warnings.append(
            f"{images_extracted} embedded image(s) were detected and extracted at their original position in "
            "the manuscript — review them in the image review step before publishing."
        )

    if not chapters:
        warnings.append("No paragraph text was found in this DOCX file.")
    total_words = sum(c.word_count for c in chapters)
    if total_words < MIN_WORDS_FOR_VALID_BOOK:
        warnings.append("Extracted text is unusually short — verify the manuscript uploaded correctly.")
    confidence = 0.85 if chapters else 0.1
    return ConversionResult(
        chapters=chapters, warnings=warnings, confidence_score=confidence,
        images_extracted=images_extracted + images_unresolved, total_word_count=total_words,
    )


# Hard cap on how many pages this pipeline will OCR for a single
# manuscript — OCR is seconds-per-page, not milliseconds; without a cap, a
# large scanned book would tie up a conversion worker indefinitely. A
# document that needs more than this many scanned pages recognized fails
# with a clear, honest error rather than hanging.
MAX_OCR_PAGES = 60
OCR_DPI = 200


def _ocr_available() -> tuple[object, object, object]:
    """Returns (fitz, pytesseract, Image) or raises ManuscriptValidationError
    with code='dependency_missing' — checked once per call site rather than
    assumed, since OCR depends on the `tesseract` binary being installed on
    the host in addition to the Python packages (see requirements.txt and
    the deployment docs note on apt-installing tesseract-ocr)."""
    try:
        import fitz  # PyMuPDF
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise ManuscriptValidationError(f"OCR is unavailable on this server: {exc}", code="dependency_missing")
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:
        raise ManuscriptValidationError(
            f"The OCR engine (tesseract) is not installed on this server: {exc}", code="dependency_missing"
        )
    return fitz, pytesseract, Image


def _ocr_pdf_page(pdf_data: bytes, page_index: int) -> tuple[str, float]:
    """Renders one PDF page to an image and OCRs it with Tesseract.
    Returns (recognized_text, confidence 0.0-1.0). Never claims success
    when OCR genuinely found nothing — an all-blank/illustration-only page
    legitimately returns ("", 0.0), which the caller treats as "no text on
    this page" rather than a conversion failure."""
    import io as _io

    fitz, pytesseract, Image = _ocr_available()
    doc = fitz.open(stream=pdf_data, filetype="pdf")
    try:
        page = doc[page_index]
        pix = page.get_pixmap(dpi=OCR_DPI)
        image = Image.open(_io.BytesIO(pix.tobytes("png")))
    finally:
        doc.close()

    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    # Group recognized words back into their original lines (tesseract's
    # block/par/line numbering) rather than joining every word on the page
    # with a single space — collapsing line breaks would destroy the
    # heading-heuristic paragraph/heading boundaries _paragraphs_to_chapters
    # relies on, exactly the same "\n"-per-visual-line convention
    # pypdf's extract_text() already uses for text-layer pages.
    lines: dict[tuple, list[str]] = {}
    confidences: list[float] = []
    n = len(data.get("text", []))
    for i in range(n):
        word = str(data["text"][i]).strip()
        if not word:
            continue
        try:
            conf_val = float(data["conf"][i])
        except (TypeError, ValueError, KeyError):
            continue
        if conf_val < 0:  # tesseract marks non-text regions with conf=-1
            continue
        line_key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(line_key, []).append(word)
        confidences.append(conf_val)

    recognized_text = "\n".join(" ".join(words) for words in lines.values())
    avg_confidence = (sum(confidences) / len(confidences) / 100.0) if confidences else 0.0
    return recognized_text, avg_confidence


def convert_pdf(data: bytes) -> ConversionResult:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise ManuscriptValidationError(f"PDF support is unavailable on this server: {exc}", code="dependency_missing")

    warnings: list[str] = []
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ManuscriptValidationError(f"Could not parse PDF file: {exc}", code="parse_failed")

    if getattr(reader, "is_encrypted", False):
        raise ManuscriptValidationError(
            "This PDF is password-protected. Remove the password and re-upload.", code="encrypted_pdf"
        )
    num_pages = len(reader.pages)
    if num_pages > MAX_PDF_PAGES:
        raise ManuscriptValidationError(
            f"PDF has {num_pages} pages, exceeding the {MAX_PDF_PAGES}-page limit.", code="too_many_pages"
        )

    # First pass: real text-layer extraction only, and a count of pages
    # with no text layer at all — these are the candidates for OCR.
    page_texts: list[str] = []
    pages_needing_ocr: list[int] = []
    for idx, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        page_texts.append(text)
        if not text.strip():
            pages_needing_ocr.append(idx)

    pages_with_text_layer = num_pages - len(pages_needing_ocr)

    if pages_needing_ocr:
        if len(pages_needing_ocr) > MAX_OCR_PAGES:
            raise ManuscriptValidationError(
                f"This PDF has {len(pages_needing_ocr)} scanned page(s) with no text layer, exceeding the "
                f"{MAX_OCR_PAGES}-page OCR limit for a single manuscript. Split it into smaller files and "
                "re-upload.",
                code="too_many_ocr_pages",
            )
        # Fail loudly up front if OCR genuinely can't run here, rather than
        # silently treating every scanned page as blank.
        _ocr_available()
        ocr_confidences: list[float] = []
        pages_ocr_recognized = 0
        for idx in pages_needing_ocr:
            try:
                ocr_text, ocr_conf = _ocr_pdf_page(data, idx)
            except ManuscriptValidationError:
                raise
            except Exception as exc:
                logger.warning("book_ocr_page_failed", extra={"page_index": idx, "error": str(exc)})
                continue
            if ocr_text.strip():
                page_texts[idx] = ocr_text
                ocr_confidences.append(ocr_conf)
                pages_ocr_recognized += 1
        if pages_ocr_recognized:
            avg_ocr_conf = sum(ocr_confidences) / len(ocr_confidences)
            warnings.append(
                f"OCR was used to recognize text on {pages_ocr_recognized} scanned page(s) "
                f"(average OCR confidence {avg_ocr_conf:.0%}) — review the extracted text for recognition errors."
            )
        pages_without_any_text = len(pages_needing_ocr) - pages_ocr_recognized
        if pages_without_any_text:
            warnings.append(
                f"{pages_without_any_text} page(s) had no text layer and OCR recognized nothing on them "
                "(likely blank pages or illustrations only) — their content was not converted."
            )
    else:
        ocr_confidences = []

    pages_with_any_text = sum(1 for t in page_texts if t.strip())
    if pages_with_any_text == 0:
        raise ManuscriptValidationError(
            "No text could be extracted or recognized via OCR on any page of this PDF.",
            code="extraction_failed",
        )

    # Images are extracted per page and placed immediately after that
    # page's text in the reading-order stream below — pypdf exposes no
    # sub-page position (text-vs-image ordering within a page), so this
    # is an honest, documented approximation ("page_level" confidence),
    # not a claim of exact placement. See conversion.py's module
    # docstring and apps.books.services.list_image_placements, which
    # surfaces source_page/position_confidence to the author review UI.
    images_extracted = 0
    images_unresolved = 0
    items: list = []
    pages_needing_ocr_set = set(pages_needing_ocr)
    for page_idx, text in enumerate(page_texts):
        buf: list[str] = []
        # extract_text()/OCR both use "\n"-joined visual lines; blank lines
        # become paragraph/heading boundaries just like the TXT path.
        for line in [*text.split("\n"), ""]:
            if line.strip() == "":
                if buf:
                    items.append(" ".join(buf))
                    buf = []
                continue
            buf.append(line.strip())
        if buf:
            items.append(" ".join(buf))

        # A page with no text layer was itself recognized via a full-page
        # OCR scan (see pages_needing_ocr above) — that scanned page
        # image IS the page's text content, not an illustration embedded
        # alongside separate text, so it is deliberately not also
        # surfaced as an "image" placement (that would double-represent
        # the same page as both OCR'd text and a redundant image block).
        if page_idx in pages_needing_ocr_set:
            continue
        try:
            page_images = list(reader.pages[page_idx].images)
        except Exception:
            page_images = []
        for img in page_images:
            try:
                img_bytes = img.data
            except Exception:
                img_bytes = None
            if img_bytes:
                images_extracted += 1
            else:
                images_unresolved += 1
            items.append(_image_item(
                data=img_bytes, content_type="", page=page_idx + 1, position_confidence="page_level",
            ))

    if images_extracted or images_unresolved:
        warnings.append(
            f"{images_extracted + images_unresolved} embedded image(s) were detected across the PDF's pages "
            f"({images_extracted} extracted, {images_unresolved} needing a manual upload) — their position is "
            "placed at the end of their source page's text, not an exact in-page location. Review them in the "
            "image review step before publishing."
        )

    chapters = _paragraphs_to_chapters(items, warnings=warnings)
    total_words = sum(c.word_count for c in chapters)
    if total_words < MIN_WORDS_FOR_VALID_BOOK:
        warnings.append("Extracted text is unusually short — verify the manuscript uploaded correctly.")

    # Confidence blends the text-layer ratio with actual OCR confidence
    # where OCR was used — never reports a flat "0.75" for a document that
    # was partly or wholly recognized via OCR instead of a real text layer.
    text_layer_ratio = pages_with_text_layer / num_pages
    if ocr_confidences:
        ocr_ratio = len(ocr_confidences) / num_pages
        avg_ocr_conf = sum(ocr_confidences) / len(ocr_confidences)
        confidence = (text_layer_ratio * 0.9) + (ocr_ratio * avg_ocr_conf)
    else:
        confidence = 0.9 if pages_with_text_layer == num_pages else 0.5
    return ConversionResult(
        chapters=chapters, warnings=warnings, confidence_score=round(min(confidence, 0.95), 3),
        images_extracted=images_extracted + images_unresolved, total_word_count=total_words,
    )


def convert_manuscript(*, fmt: str, data: bytes) -> ConversionResult:
    if fmt == "txt":
        return convert_txt(data)
    if fmt == "docx":
        return convert_docx(data)
    if fmt == "pdf":
        return convert_pdf(data)
    raise ManuscriptValidationError(f"Unsupported format: {fmt}", code="unsupported_format")


def estimate_reading_minutes(total_word_count: int) -> int:
    words_per_minute = 200
    return max(1, round(total_word_count / words_per_minute))
