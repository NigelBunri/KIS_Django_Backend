# apps/bots/memory.py
"""
KIS Bot Platform (Phase 4) — bot-specific operational memory, distinct
from Knowledge (apps.bots.knowledge): memory is short notes a bot's own
AI decision accumulates while operating (e.g. "this member asked about
volunteering"), never raw private conversation content unless the
owner explicitly turns on allow_message_content. Off by default for
every bot (BotMemoryConfig.enabled=False) - nothing here runs unless
an owner opts in.

Retrieval is the same Postgres full-text search pattern as knowledge
(SearchQuery/SearchRank against a GIN-indexed SearchVectorField) -
no embedding API, no provider dependency, works with AI disabled too
(though with AI disabled there is no decision step to feed memory
into in the first place).
"""
from __future__ import annotations

from datetime import timedelta

from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import Bot, BotMemoryConfig, BotMemoryEntry
from .services import User, _require_owner

MAX_RETENTION_DAYS = 365
MAX_MEMORY_CONTENT_CHARS = 4000
MAX_ENTRIES_PER_BOT = 2000


def get_or_create_memory_config(bot: Bot) -> BotMemoryConfig:
    config, _created = BotMemoryConfig.objects.get_or_create(bot=bot)
    return config


def update_memory_config(
    bot: Bot, user: User, *, enabled: bool | None = None,
    allow_message_content: bool | None = None, retention_days: int | None = None,
) -> BotMemoryConfig:
    _require_owner(bot, user)
    config = get_or_create_memory_config(bot)
    update_fields = []
    if enabled is not None:
        config.enabled = bool(enabled)
        update_fields.append("enabled")
    if allow_message_content is not None:
        config.allow_message_content = bool(allow_message_content)
        update_fields.append("allow_message_content")
    if retention_days is not None:
        if not isinstance(retention_days, int) or retention_days < 1 or retention_days > MAX_RETENTION_DAYS:
            raise ValidationError({"retention_days": f"retention_days must be between 1 and {MAX_RETENTION_DAYS}."})
        config.retention_days = retention_days
        update_fields.append("retention_days")
    if update_fields:
        config.save(update_fields=[*update_fields, "updated_at"])
    return config


def remember(bot: Bot, *, content: str, subject_user_id=None, source: str = "ai_decision") -> BotMemoryEntry | None:
    """The only write path for memory - called from apps.bots.ai_decision
    after a decision, never directly from a view. Returns None (writes
    nothing) if memory is disabled for this bot, which is what makes
    "let owners decide whether memory is enabled" an actual guarantee
    rather than a UI-only toggle."""
    config = get_or_create_memory_config(bot)
    if not config.enabled:
        return None
    content = (content or "").strip()[:MAX_MEMORY_CONTENT_CHARS]
    if not content:
        return None
    if BotMemoryEntry.objects.filter(bot=bot).count() >= MAX_ENTRIES_PER_BOT:
        BotMemoryEntry.objects.filter(bot=bot).order_by("created_at").first().delete()
    entry = BotMemoryEntry.objects.create(
        bot=bot, subject_user_id=subject_user_id, content=content, source=source[:20],
        expires_at=timezone.now() + timedelta(days=config.retention_days),
    )
    BotMemoryEntry.objects.filter(id=entry.id).update(search_vector=SearchVector("content"))
    return entry


def retrieve_relevant_memory(bot: Bot, query_text: str, *, subject_user_id=None, limit: int = 5) -> list[dict]:
    """Scoped strictly to `bot` (and optionally one subject), and to
    non-expired entries only - the same filter the periodic purge task
    enforces permanently, applied here so an entry past its retention
    window is invisible to retrieval even in the window between
    expiring and actually being purged."""
    config = get_or_create_memory_config(bot)
    if not config.enabled:
        return []
    query_text = (query_text or "").strip()
    now = timezone.now()
    qs = BotMemoryEntry.objects.filter(bot=bot).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    if subject_user_id:
        qs = qs.filter(subject_user_id=subject_user_id)
    if query_text:
        search_query = SearchQuery(query_text, search_type="websearch")
        qs = qs.filter(search_vector=search_query).annotate(rank=SearchRank(F("search_vector"), search_query)).order_by("-rank")
    else:
        qs = qs.order_by("-created_at")
    return [
        {"id": str(e.id), "content": e.content, "subject_user_id": str(e.subject_user_id) if e.subject_user_id else None}
        for e in qs[:limit]
    ]


def list_memory_entries(bot: Bot, user: User):
    _require_owner(bot, user)
    now = timezone.now()
    return BotMemoryEntry.objects.filter(bot=bot).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now),
    ).order_by("-created_at")


def delete_memory_entry(bot: Bot, user: User, entry_id) -> None:
    _require_owner(bot, user)
    BotMemoryEntry.objects.filter(bot=bot, id=entry_id).delete()


def delete_all_memory(bot: Bot, user: User) -> int:
    _require_owner(bot, user)
    return BotMemoryEntry.objects.filter(bot=bot).delete()[0]


def purge_expired_memory() -> int:
    """Celery-beat sweep - the actual deletion half of retention
    (retrieve_relevant_memory/list_memory_entries already filter
    expired rows out, but they still exist in the table until this
    runs)."""
    return BotMemoryEntry.objects.filter(expires_at__lt=timezone.now()).delete()[0]
