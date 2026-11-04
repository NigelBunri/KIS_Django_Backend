# apps/bots/knowledge.py
"""
KIS Bot Platform (Phase 4) — bot-specific knowledge: create/inspect/
deactivate/delete sources, chunk+index them, and retrieve the most
relevant chunks for one query. Every function here takes a `bot` and
either filters by it or receives an object already scoped to it -
there is no function in this module capable of returning another
bot's knowledge, by construction (see apps.bots.test_knowledge's
cross-bot isolation tests).

Retrieval is plain Postgres full-text search (SearchQuery/SearchRank
against a GIN-indexed SearchVectorField) - the same machinery
apps.broadcasts' own content search already runs in production. This
needs no embedding API call, so it works for a bot with no AI
connection configured at all, and never depends on a specific AI
provider.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import ValidationError

from apps.media.safety import validate_upload_file_safety

from .knowledge_extraction import KnowledgeExtractionError, extract_text_from_upload, extract_text_from_url
from .models import (
    Bot,
    BotKnowledgeChunk,
    BotKnowledgeSource,
    BotKnowledgeSourceStatus,
    BotKnowledgeSourceType,
)
from .services import User, _require_owner

CHUNK_TARGET_CHARS = 900
CHUNK_MAX_CHARS = 1500
ALLOWED_KNOWLEDGE_EXTENSIONS = {".txt", ".md", ".csv", ".pdf", ".docx"}


def _chunk_text(text: str) -> list[str]:
    """Groups paragraphs into ~CHUNK_TARGET_CHARS chunks, splitting any
    single paragraph longer than CHUNK_MAX_CHARS on its own. Simple and
    deterministic - no NLP dependency, good enough for retrieval-sized
    context, not meant to preserve perfect semantic boundaries."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(para) > CHUNK_MAX_CHARS:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(para), CHUNK_MAX_CHARS):
                chunks.append(para[i : i + CHUNK_MAX_CHARS])
            continue
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) > CHUNK_TARGET_CHARS and current:
            chunks.append(current)
            current = para
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _index_source(source: BotKnowledgeSource) -> None:
    """(Re)builds this source's chunks + their search vectors from its
    current extracted_text. Always deletes any prior chunks first - a
    re-process (e.g. a retried URL fetch) must never leave stale chunks
    alongside fresh ones."""
    with transaction.atomic():
        BotKnowledgeChunk.objects.filter(source=source).delete()
        pieces = _chunk_text(source.extracted_text)
        if not pieces:
            source.status = BotKnowledgeSourceStatus.ERROR
            source.error_message = "No readable text was found in this source."
            source.save(update_fields=["status", "error_message", "updated_at"])
            return
        chunks = [
            BotKnowledgeChunk(source=source, bot=source.bot, chunk_index=i, text=piece)
            for i, piece in enumerate(pieces)
        ]
        BotKnowledgeChunk.objects.bulk_create(chunks)
        BotKnowledgeChunk.objects.filter(source=source).update(search_vector=SearchVector("text"))
        source.status = BotKnowledgeSourceStatus.READY
        source.error_message = ""
        source.save(update_fields=["status", "error_message", "updated_at"])


def process_source(source_id) -> None:
    """Entry point for apps.bots.tasks.process_knowledge_source_task.
    Does the actual extraction (which can be slow - a large PDF, a slow
    URL) and indexing; never raises past its own boundary, since a bad
    document must not crash the worker - any failure is recorded on the
    source itself as status=ERROR with a human-readable error_message."""
    source = BotKnowledgeSource.objects.select_related("bot").filter(id=source_id).first()
    if not source:
        return
    source.status = BotKnowledgeSourceStatus.PROCESSING
    source.save(update_fields=["status", "updated_at"])
    try:
        if source.source_type == BotKnowledgeSourceType.URL:
            text = extract_text_from_url(source.source_url, max_bytes=settings.BOTS_KNOWLEDGE_MAX_URL_FETCH_BYTES)
        elif source.source_type == BotKnowledgeSourceType.FILE:
            source.file.open("rb")
            try:
                text = extract_text_from_upload(source.file)
            finally:
                source.file.close()
        else:
            text = source.extracted_text
        source.extracted_text = text[:2_000_000]
        source.save(update_fields=["extracted_text", "updated_at"])
        _index_source(source)
    except KnowledgeExtractionError as exc:
        source.status = BotKnowledgeSourceStatus.ERROR
        source.error_message = str(exc)[:2000]
        source.save(update_fields=["status", "error_message", "updated_at"])
    except Exception as exc:  # noqa: BLE001 - anything unexpected is still "this source failed," not a worker crash
        source.status = BotKnowledgeSourceStatus.ERROR
        source.error_message = f"Unexpected processing error: {str(exc)[:300]}"
        source.save(update_fields=["status", "error_message", "updated_at"])


def _require_source_count_within_limit(bot: Bot) -> None:
    count = BotKnowledgeSource.objects.filter(bot=bot).exclude(status=BotKnowledgeSourceStatus.DEACTIVATED).count()
    if count >= settings.BOTS_KNOWLEDGE_MAX_SOURCES_PER_BOT:
        raise ValidationError({
            "detail": f"This bot already has the maximum of {settings.BOTS_KNOWLEDGE_MAX_SOURCES_PER_BOT} active knowledge sources.",
        })


def create_file_source(bot: Bot, user: User, *, title: str, upload) -> BotKnowledgeSource:
    _require_owner(bot, user)
    _require_source_count_within_limit(bot)
    import os

    ext = os.path.splitext(str(getattr(upload, "name", "")))[1].lower()
    if ext not in ALLOWED_KNOWLEDGE_EXTENSIONS:
        raise ValidationError({"file": f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_KNOWLEDGE_EXTENSIONS))}."})
    if upload.size > settings.BOTS_KNOWLEDGE_MAX_UPLOAD_BYTES:
        raise ValidationError({"file": "File is too large for a knowledge source."})
    validate_upload_file_safety(upload, context="general")

    source = BotKnowledgeSource.objects.create(
        bot=bot, source_type=BotKnowledgeSourceType.FILE, title=(title or upload.name)[:200],
        file=upload, file_size_bytes=upload.size,
        content_type=str(getattr(upload, "content_type", "") or ""), created_by=user,
    )
    from .tasks import process_knowledge_source_task

    process_knowledge_source_task.delay(str(source.id))
    return source


def create_url_source(bot: Bot, user: User, *, title: str, url: str) -> BotKnowledgeSource:
    _require_owner(bot, user)
    _require_source_count_within_limit(bot)
    from common.url_safety import is_safe_external_url

    if not is_safe_external_url(url):
        raise ValidationError({"url": "This URL is not an allowed external address."})
    source = BotKnowledgeSource.objects.create(
        bot=bot, source_type=BotKnowledgeSourceType.URL, title=(title or url)[:200],
        source_url=url[:2000], created_by=user,
    )
    from .tasks import process_knowledge_source_task

    process_knowledge_source_task.delay(str(source.id))
    return source


def create_text_source(bot: Bot, user: User, *, title: str, text: str) -> BotKnowledgeSource:
    _require_owner(bot, user)
    _require_source_count_within_limit(bot)
    text = (text or "").strip()
    if not text:
        raise ValidationError({"text": "Pasted text cannot be empty."})
    source = BotKnowledgeSource.objects.create(
        bot=bot, source_type=BotKnowledgeSourceType.TEXT, title=(title or text[:60])[:200],
        extracted_text=text[:2_000_000], created_by=user,
    )
    _index_source(source)
    return source


def _owned_source_or_404(bot: Bot, user: User, source_id) -> BotKnowledgeSource:
    _require_owner(bot, user)
    source = BotKnowledgeSource.objects.filter(id=source_id, bot=bot).first()
    if not source:
        raise ValidationError({"detail": "Knowledge source not found."})
    return source


def deactivate_source(bot: Bot, user: User, source_id) -> BotKnowledgeSource:
    from django.utils import timezone

    source = _owned_source_or_404(bot, user, source_id)
    source.status = BotKnowledgeSourceStatus.DEACTIVATED
    source.deactivated_at = timezone.now()
    source.save(update_fields=["status", "deactivated_at", "updated_at"])
    return source


def reactivate_source(bot: Bot, user: User, source_id) -> BotKnowledgeSource:
    source = _owned_source_or_404(bot, user, source_id)
    source.status = BotKnowledgeSourceStatus.READY if source.chunks.exists() else BotKnowledgeSourceStatus.PENDING
    source.deactivated_at = None
    source.save(update_fields=["status", "deactivated_at", "updated_at"])
    if source.status == BotKnowledgeSourceStatus.PENDING:
        from .tasks import process_knowledge_source_task

        process_knowledge_source_task.delay(str(source.id))
    return source


def delete_source(bot: Bot, user: User, source_id) -> None:
    source = _owned_source_or_404(bot, user, source_id)
    file_to_delete = source.file
    source.delete()  # CASCADE removes every chunk (and its search index entry) with it
    if file_to_delete:
        file_to_delete.delete(save=False)


def retrieve_relevant_chunks(bot: Bot, query_text: str, *, limit: int = 5, max_total_chars: int = 4000) -> list[dict]:
    """Returns the most relevant chunks for `query_text`, scoped
    strictly to `bot` - the WHERE bot=bot clause below is the entire
    isolation guarantee; no other model/relationship is consulted.
    Caps both the number of chunks AND their combined length so a
    caller (apps.bots.ai_decision) never ends up sending an entire
    knowledge library to a provider for one decision."""
    query_text = (query_text or "").strip()
    if not query_text:
        return []
    search_query = SearchQuery(query_text, search_type="websearch")
    qs = (
        BotKnowledgeChunk.objects.filter(
            bot=bot, search_vector=search_query,
            source__status=BotKnowledgeSourceStatus.READY,
            source__deactivated_at__isnull=True,
        )
        .annotate(rank=SearchRank(F("search_vector"), search_query))
        .order_by("-rank")
        .select_related("source")[: limit * 2]
    )
    results: list[dict] = []
    total_chars = 0
    for chunk in qs:
        if total_chars >= max_total_chars or len(results) >= limit:
            break
        results.append({
            "text": chunk.text,
            "source_id": str(chunk.source_id),
            "source_title": chunk.source.title,
            "rank": float(chunk.rank),
        })
        total_chars += len(chunk.text)
    return results
