from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from .models import (
    EngineSession,
    EngineStepProgress,
    ServiceWorkflowSession,
    VideoEngineItem,
    VideoEngineItemProgress,
    WorkflowStatus,
)

ENGINE_RUNTIME_LOCKED = "locked"
ENGINE_RUNTIME_AVAILABLE = "available"
ENGINE_RUNTIME_COMPLETED = "completed"
ENGINE_RUNTIME_EXPIRED = "expired"


@dataclass(frozen=True)
class EngineAccessVerdict:
    allowed: bool
    detail: str
    status_code: int
    state: str


def _coerce_positive_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0, parsed)


def resolve_engine_access_window_days(engine_session: EngineSession) -> int:
    direct = _coerce_positive_int(getattr(engine_session.engine_map, "access_window_days", 0), default=0)
    if direct > 0:
        return direct

    config = engine_session.engine_map.config if isinstance(engine_session.engine_map.config, dict) else {}
    legacy_values = (
        config.get("access_window_days"),
        config.get("accessWindowDays"),
        config.get("duration_days"),
        config.get("durationDays"),
    )
    for value in legacy_values:
        parsed = _coerce_positive_int(value, default=0)
        if parsed > 0:
            return parsed
    return 0


def get_engine_runtime_state(engine_session: EngineSession, *, now=None) -> str:
    if engine_session.is_completed:
        return ENGINE_RUNTIME_COMPLETED
    if engine_session.is_expired:
        return ENGINE_RUNTIME_EXPIRED
    if engine_session.is_unlocked:
        return ENGINE_RUNTIME_AVAILABLE
    return ENGINE_RUNTIME_LOCKED


def get_engine_remaining_seconds(engine_session: EngineSession, *, now=None) -> int | None:
    runtime_state = get_engine_runtime_state(engine_session, now=now)
    if runtime_state != ENGINE_RUNTIME_AVAILABLE:
        return None
    if not engine_session.expires_at:
        return None
    now_value = now or timezone.now()
    remaining = int((engine_session.expires_at - now_value).total_seconds())
    return max(0, remaining)


def _apply_engine_window(engine_session: EngineSession, *, now) -> list[str]:
    updates: list[str] = []
    if engine_session.is_completed:
        if engine_session.is_expired:
            engine_session.is_expired = False
            updates.append("is_expired")
        if engine_session.expired_at:
            engine_session.expired_at = None
            updates.append("expired_at")
        return updates

    if not engine_session.is_unlocked:
        return updates

    if not engine_session.unlocked_at:
        engine_session.unlocked_at = now
        updates.append("unlocked_at")

    window_days = resolve_engine_access_window_days(engine_session)
    if window_days <= 0:
        if engine_session.expires_at:
            engine_session.expires_at = None
            updates.append("expires_at")
        if engine_session.is_expired:
            engine_session.is_expired = False
            updates.append("is_expired")
        if engine_session.expired_at:
            engine_session.expired_at = None
            updates.append("expired_at")
        return updates

    expected_expires_at = engine_session.expires_at
    if not expected_expires_at:
        expected_expires_at = engine_session.unlocked_at + timedelta(days=window_days)
        engine_session.expires_at = expected_expires_at
        updates.append("expires_at")

    if now >= expected_expires_at:
        if not engine_session.is_expired:
            engine_session.is_expired = True
            updates.append("is_expired")
        if not engine_session.expired_at:
            engine_session.expired_at = now
            updates.append("expired_at")
        if engine_session.is_unlocked:
            engine_session.is_unlocked = False
            updates.append("is_unlocked")
    elif engine_session.is_expired:
        engine_session.is_expired = False
        updates.append("is_expired")
        if engine_session.expired_at:
            engine_session.expired_at = None
            updates.append("expired_at")

    return updates


@transaction.atomic
def refresh_workflow_engine_runtime(workflow_session: ServiceWorkflowSession, *, now=None) -> ServiceWorkflowSession:
    now_value = now or timezone.now()
    engine_sessions = list(
        workflow_session.engine_sessions.select_related("engine_map__engine").order_by("engine_map__execution_order")
    )
    if not engine_sessions:
        return workflow_session

    for index, engine_session in enumerate(engine_sessions):
        previous_required_complete = all(
            prior.is_completed or not bool(prior.engine_map.is_required)
            for prior in engine_sessions[:index]
        )

        updates: list[str] = []
        if not engine_session.is_completed:
            if previous_required_complete and not engine_session.is_expired:
                if not engine_session.is_unlocked:
                    engine_session.is_unlocked = True
                    updates.append("is_unlocked")
            elif engine_session.is_unlocked:
                engine_session.is_unlocked = False
                updates.append("is_unlocked")

        updates.extend(_apply_engine_window(engine_session, now=now_value))
        if updates:
            unique_updates = list(dict.fromkeys([*updates, "updated_at"]))
            engine_session.save(update_fields=unique_updates)

    required_expired = any(
        engine.engine_map.is_required and engine.is_expired and not engine.is_completed for engine in engine_sessions
    )
    all_completed = all(engine.is_completed for engine in engine_sessions)
    active_engine = next(
        (
            engine
            for engine in engine_sessions
            if engine.is_unlocked and not engine.is_completed and not engine.is_expired
        ),
        None,
    )

    workflow_updates: list[str] = []
    if active_engine and workflow_session.current_engine_map_id != active_engine.engine_map_id:
        workflow_session.current_engine_map = active_engine.engine_map
        workflow_updates.append("current_engine_map")
    if active_engine and workflow_session.current_step_index != int(active_engine.progress_step or 0):
        workflow_session.current_step_index = int(active_engine.progress_step or 0)
        workflow_updates.append("current_step_index")

    if all_completed:
        if workflow_session.status != WorkflowStatus.COMPLETED:
            workflow_session.status = WorkflowStatus.COMPLETED
            workflow_updates.append("status")
        if not workflow_session.completed_at:
            workflow_session.completed_at = now_value
            workflow_updates.append("completed_at")
    else:
        if required_expired and workflow_session.status in {WorkflowStatus.DRAFT, WorkflowStatus.IN_PROGRESS}:
            workflow_session.status = WorkflowStatus.PAUSED
            workflow_updates.append("status")
        elif not required_expired and workflow_session.status in {WorkflowStatus.DRAFT, WorkflowStatus.PAUSED}:
            workflow_session.status = WorkflowStatus.IN_PROGRESS
            workflow_updates.append("status")
        if workflow_session.status != WorkflowStatus.COMPLETED and workflow_session.completed_at:
            workflow_session.completed_at = None
            workflow_updates.append("completed_at")

    if workflow_updates:
        workflow_session.save(update_fields=[*workflow_updates, "updated_at"])
    return workflow_session


def resolve_engine_access(workflow_session: ServiceWorkflowSession, engine_session: EngineSession) -> EngineAccessVerdict:
    refresh_workflow_engine_runtime(workflow_session)
    engine_session.refresh_from_db()
    state = get_engine_runtime_state(engine_session)

    if engine_session.workflow_session_id != workflow_session.id:
        return EngineAccessVerdict(False, "Engine does not belong to this workflow.", 400, ENGINE_RUNTIME_LOCKED)
    if state == ENGINE_RUNTIME_COMPLETED:
        return EngineAccessVerdict(True, "", 200, state)
    if state == ENGINE_RUNTIME_EXPIRED:
        return EngineAccessVerdict(False, "This engine access window has expired.", 410, state)
    if state == ENGINE_RUNTIME_LOCKED:
        return EngineAccessVerdict(False, "This engine is locked until previous required engines are completed.", 423, state)
    return EngineAccessVerdict(True, "", 200, state)


def validate_engine_step_progression(engine_session: EngineSession, step_key: str, *, is_completed: bool) -> tuple[bool, str]:
    if not is_completed:
        return True, ""

    step_defs = list(
        engine_session.engine_map.engine.step_definitions.order_by("step_order").values("step_key", "is_required")
    )
    if not step_defs:
        return True, ""

    keys = [str(row["step_key"]) for row in step_defs]
    if step_key not in keys:
        return False, "Invalid step key for this engine."

    target_index = keys.index(step_key)
    required_prior_keys = [keys[idx] for idx in range(target_index) if bool(step_defs[idx]["is_required"])]
    if not required_prior_keys:
        return True, ""

    completed_prior = set(
        EngineStepProgress.objects.filter(
            engine_session=engine_session,
            step_key__in=required_prior_keys,
            is_completed=True,
        ).values_list("step_key", flat=True)
    )
    missing = next((required for required in required_prior_keys if required not in completed_prior), None)
    if missing:
        return False, f"Complete '{missing}' before '{step_key}'."
    return True, ""


def build_workflow_runtime_payload(workflow_session: ServiceWorkflowSession) -> dict[str, Any]:
    refresh_workflow_engine_runtime(workflow_session)
    workflow_session.refresh_from_db()
    engine_sessions = list(
        workflow_session.engine_sessions.select_related("engine_map__engine").order_by("engine_map__execution_order")
    )

    now_value = timezone.now()
    engines_payload: list[dict[str, Any]] = []
    for engine_session in engine_sessions:
        runtime_state = get_engine_runtime_state(engine_session, now=now_value)
        engines_payload.append(
            {
                "engine_session_id": str(engine_session.id),
                "engine_map_id": str(engine_session.engine_map_id),
                "engine_code": str(engine_session.engine_map.engine.code),
                "engine_name": str(engine_session.engine_map.engine.name),
                "execution_order": int(engine_session.engine_map.execution_order or 0),
                "is_required": bool(engine_session.engine_map.is_required),
                "completion_mode": str(engine_session.engine_map.completion_mode or ""),
                "access_window_days": int(resolve_engine_access_window_days(engine_session)),
                "state": runtime_state,
                "is_unlocked": bool(engine_session.is_unlocked),
                "is_completed": bool(engine_session.is_completed),
                "is_expired": bool(engine_session.is_expired),
                "progress_step": int(engine_session.progress_step or 0),
                "progress_percent": int(engine_session.progress_percent or 0),
                "unlocked_at": engine_session.unlocked_at.isoformat() if engine_session.unlocked_at else None,
                "expires_at": engine_session.expires_at.isoformat() if engine_session.expires_at else None,
                "expired_at": engine_session.expired_at.isoformat() if engine_session.expired_at else None,
                "remaining_seconds": get_engine_remaining_seconds(engine_session, now=now_value),
            }
        )

    required_expired = any(
        bool(engine_session.engine_map.is_required) and bool(engine_session.is_expired) and not bool(engine_session.is_completed)
        for engine_session in engine_sessions
    )
    current_engine = next(
        (
            item
            for item in engines_payload
            if item["state"] == ENGINE_RUNTIME_AVAILABLE
        ),
        None,
    )

    return {
        "workflow_session_id": str(workflow_session.id),
        "status": str(workflow_session.status),
        "current_engine_session_id": current_engine["engine_session_id"] if current_engine else None,
        "blocked_reason": "required_engine_expired" if required_expired else "",
        "engines": engines_payload,
    }


def is_video_engine_item_mode(engine_session: EngineSession) -> bool:
    completion_mode = str(getattr(engine_session.engine_map, "completion_mode", "") or "")
    if completion_mode == "video_items":
        return True
    engine_code = str(getattr(engine_session.engine_map.engine, "code", "") or "")
    return engine_code == "video"


def evaluate_video_engine_completion(engine_session: EngineSession) -> tuple[int, int, bool]:
    active_item_ids = list(
        VideoEngineItem.objects.filter(engine_map=engine_session.engine_map, is_active=True)
        .order_by("sort_order")
        .values_list("id", flat=True)
    )
    total_items = len(active_item_ids)
    if total_items <= 0:
        return 0, 0, False

    completed_items = (
        VideoEngineItemProgress.objects.filter(
            engine_session=engine_session,
            user=engine_session.user,
            item_id__in=active_item_ids,
            is_completed=True,
        )
        .values("item_id")
        .distinct()
        .count()
    )
    return completed_items, total_items, completed_items >= total_items
