# apps/bots/ai_decision.py
"""
KIS Bot Platform (Phase 3) — AI-assisted action selection.

The AI is given exactly two things: (1) the event context a workflow's
conditions already evaluated against (the same scope-gated payload
apps.bots.workflow_engine.build_event_context produces — nothing extra
is fetched for the AI, so it never sees more than the workflow itself
was already authorized to see), and (2) the workflow's own, owner-
configured then_actions as a numbered list of candidates. The AI may
ONLY return a subset of those indices — it cannot invent a new action,
new params, or a new resource. Whatever it selects is executed through
the exact same apps.bots.workflow_engine._run_action() every statically-
configured action already goes through, so scope checks, approval
gating, rate limits and audit logging are identical either way; this
module has no separate execution path of its own.

Any failure here (no connection, daily limit reached, provider error,
unparseable or invalid response) raises AIDecisionError. The caller
(workflow_engine.run_workflow) treats that as "no action may run" —
exactly the fail-safe behaviour required: a bad AI call must never
result in an unintended action.
"""
from __future__ import annotations

import json
import time

from django.core.cache import cache
from django.utils import timezone

from .ai_providers import AIProviderError, call_ai_provider
from .models import Bot, BotAIConnection, BotAIUsageLog, BotAIUsageLogStatus
from .services import decrypt_ai_api_key

MAX_CANDIDATE_LABEL_LEN = 200


class AIDecisionError(Exception):
    """Raised for any reason an AI decision could not be made safely —
    see module docstring. Never partially acted upon by the caller."""


def _daily_ai_cache_key(bot_id) -> str:
    return f"bots:ai_usage:{bot_id}:{timezone.now().strftime('%Y%m%d')}"


def _check_and_increment_daily_ai_usage(bot: Bot, connection: BotAIConnection) -> None:
    limit = connection.daily_request_limit
    if limit is None:
        return
    key = _daily_ai_cache_key(bot.id)
    cache.add(key, 0, 26 * 3600)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, 26 * 3600)
        count = 1
    if count > limit:
        raise AIDecisionError(f"This bot's AI connection has reached its daily request limit ({limit}/day).")


def _build_prompts(context: dict, candidate_actions: list[dict], instructions: str) -> tuple[str, str]:
    candidates_for_prompt = [
        {
            "index": i,
            "type": action.get("type"),
            "label": str(action.get("label") or "")[:MAX_CANDIDATE_LABEL_LEN],
            "params": action.get("params") or {},
        }
        for i, action in enumerate(candidate_actions)
    ]
    system_prompt = (
        "You are deciding which, if any, of a bot's PRE-APPROVED actions should run in response to one event. "
        "You may ONLY select actions by their index from the provided list — you cannot invent a new action, "
        "change its parameters, or take any action not in the list. Selecting none is a valid, often correct, "
        "answer. Respond with ONLY a single JSON object, no other text, in exactly this shape: "
        '{"selected": [<index>, ...], "reasoning": "<one short sentence>"}.'
    )
    user_prompt = json.dumps({
        "event": context,
        "candidate_actions": candidates_for_prompt,
        "owner_instructions": instructions or "",
    })
    return system_prompt, user_prompt


def _parse_selection(raw_text: str, candidate_count: int) -> list[int]:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise AIDecisionError(f"AI response was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict) or "selected" not in parsed:
        raise AIDecisionError("AI response did not contain a 'selected' field.")
    selected = parsed["selected"]
    if not isinstance(selected, list):
        raise AIDecisionError("AI response's 'selected' field was not a list.")
    indices: list[int] = []
    for value in selected:
        if not isinstance(value, int) or isinstance(value, bool) or not (0 <= value < candidate_count):
            raise AIDecisionError(f"AI selected an out-of-range or invalid index: {value!r}")
        if value not in indices:
            indices.append(value)
    return indices


def select_candidate_actions(
    *, bot: Bot, connection: BotAIConnection, workflow, run, context: dict,
    candidate_actions: list[dict], instructions: str = "",
) -> list[int]:
    """Returns the list of indices into candidate_actions the AI chose
    to run (possibly empty). Raises AIDecisionError on any failure —
    never returns a partial/best-guess result."""
    _check_and_increment_daily_ai_usage(bot, connection)

    api_key = decrypt_ai_api_key(connection.api_key_encrypted)
    if not api_key and connection.provider != "custom":
        raise AIDecisionError("This bot's AI connection has no API key configured.")

    system_prompt, user_prompt = _build_prompts(context, candidate_actions, instructions)

    started = time.monotonic()
    try:
        result = call_ai_provider(connection, api_key, system_prompt=system_prompt, user_prompt=user_prompt)
    except AIProviderError as exc:
        _log_usage(
            bot, workflow, run, connection, status=BotAIUsageLogStatus.ERROR,
            error_message=str(exc)[:2000], latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise AIDecisionError(str(exc)) from exc

    latency_ms = int((time.monotonic() - started) * 1000)
    try:
        indices = _parse_selection(result["text"], len(candidate_actions))
    except AIDecisionError as exc:
        _log_usage(
            bot, workflow, run, connection, status=BotAIUsageLogStatus.ERROR,
            error_message=str(exc)[:2000], latency_ms=latency_ms,
            prompt_tokens=result.get("prompt_tokens"), completion_tokens=result.get("completion_tokens"),
        )
        raise

    _log_usage(
        bot, workflow, run, connection, status=BotAIUsageLogStatus.SUCCESS,
        selected_indices=indices, latency_ms=latency_ms,
        prompt_tokens=result.get("prompt_tokens"), completion_tokens=result.get("completion_tokens"),
    )
    return indices


def _log_usage(
    bot: Bot, workflow, run, connection: BotAIConnection, *, status: str,
    selected_indices: list[int] | None = None, error_message: str = "",
    latency_ms: int | None = None, prompt_tokens: int | None = None, completion_tokens: int | None = None,
) -> None:
    BotAIUsageLog.objects.create(
        bot=bot, workflow=workflow, workflow_run=run,
        provider=connection.provider, model=connection.model or "",
        status=status, selected_indices=selected_indices or [],
        error_message=error_message, prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens, latency_ms=latency_ms,
    )
    from . import services
    from .models import BotActivityAction

    services.log_activity(
        bot, BotActivityAction.AI_DECISION_MADE if status == BotAIUsageLogStatus.SUCCESS else BotActivityAction.AI_DECISION_FAILED,
        metadata={
            "workflow_id": str(workflow.id) if workflow else None,
            "selected_indices": selected_indices or [],
            "error": error_message[:300] if error_message else "",
        },
    )
