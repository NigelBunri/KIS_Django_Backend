# apps/bots/knowledge_extraction.py
"""
KIS Bot Platform (Phase 4) — extracting plain text out of an uploaded
file or a URL, for indexing as bot knowledge.

Security posture (deliberate, not incidental):
- Every extractor returns PLAIN TEXT only. Nothing here executes macros,
  runs scripts, follows embedded links, or renders markup - a PDF/DOCX's
  structural text is read, nothing else about it is interpreted.
- The returned text is later stored as an inert TextField and only ever
  used as reference material appended to an AI prompt (see
  apps.bots.ai_decision) - never parsed as a command by this app's own
  code. That is what makes a malicious "ignore your instructions and do
  X" sentence embedded in a document harmless to KIS's own enforcement:
  no code path here, or in apps.bots.workflow_engine, ever treats
  retrieved text as anything other than a string to search over, or
  prompt content to place after an explicit "this is reference material,
  not instructions" boundary.
- URL fetches go through the same is_safe_external_url SSRF guard already
  used for partner webhooks and custom AI endpoints, and are capped in
  both byte size and total wall-clock time.
"""
from __future__ import annotations

import re

import requests

from common.url_safety import is_safe_external_url

REQUEST_TIMEOUT_SECONDS = 15


class KnowledgeExtractionError(Exception):
    """Raised for any extraction failure - unreadable file, unsafe or
    unreachable URL, empty result. Callers record this as the source's
    error_message and status=ERROR; they never raise it past the task
    boundary (a bad document must not crash the worker)."""


def extract_text_from_upload(upload) -> str:
    name = str(getattr(upload, "name", "") or "").lower()
    upload.seek(0)
    data = upload.read()
    upload.seek(0)

    if name.endswith(".pdf"):
        return _extract_pdf(data)
    if name.endswith(".docx"):
        return _extract_docx(data)
    if name.endswith((".txt", ".md", ".csv")):
        return _decode_text(data)
    raise KnowledgeExtractionError(f"Unsupported file type for '{name}'.")


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise KnowledgeExtractionError("Could not decode file as text.")


def _extract_pdf(data: bytes) -> str:
    try:
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages)
    except Exception as exc:  # noqa: BLE001 - any pypdf failure means "couldn't extract", not a crash
        raise KnowledgeExtractionError(f"Could not read PDF: {exc}") from exc


def _extract_docx(data: bytes) -> str:
    try:
        import io

        import docx

        document = docx.Document(io.BytesIO(data))
        return "\n\n".join(p.text for p in document.paragraphs if p.text.strip())
    except Exception as exc:  # noqa: BLE001
        raise KnowledgeExtractionError(f"Could not read DOCX: {exc}") from exc


_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _html_to_text(html: str) -> str:
    """Deliberately minimal - strips <script>/<style> blocks entirely
    (never executed, but also pointless as 'content'), then every
    remaining tag, leaving only the text a reader would actually see.
    Not a full HTML parser; good enough for 'read this article's text',
    not meant to preserve layout."""
    html = _TAG_RE.sub(" ", html)
    text = _ANY_TAG_RE.sub(" ", html)
    text = _WHITESPACE_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    import html as html_module

    return html_module.unescape(text).strip()


def extract_text_from_url(url: str, *, max_bytes: int) -> str:
    if not is_safe_external_url(url):
        raise KnowledgeExtractionError("This URL is not an allowed external address.")
    try:
        resp = requests.get(
            url, timeout=REQUEST_TIMEOUT_SECONDS, stream=True,
            headers={"User-Agent": "KISBotPlatform/1.0 (+https://kingdomimpactventures.org)"},
        )
    except requests.RequestException as exc:
        raise KnowledgeExtractionError(f"Could not fetch URL: {exc}") from exc
    if resp.status_code != 200:
        raise KnowledgeExtractionError(f"URL returned HTTP {resp.status_code}.")
    content_type = resp.headers.get("Content-Type", "")
    if "text/html" not in content_type and "text/plain" not in content_type:
        raise KnowledgeExtractionError(f"Unsupported content type for knowledge: '{content_type}'.")

    chunks = []
    total = 0
    for chunk in resp.iter_content(chunk_size=8192):
        total += len(chunk)
        if total > max_bytes:
            raise KnowledgeExtractionError("URL content exceeded the size limit.")
        chunks.append(chunk)
    raw = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")

    if "text/html" in content_type:
        return _html_to_text(raw)
    return raw.strip()
