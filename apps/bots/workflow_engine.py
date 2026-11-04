# apps/bots/workflow_engine.py
"""
KIS Bot Platform — Phase 2 no-code workflow execution engine.

A workflow's `definition` is declarative JSON (conditions + actions),
validated here and NEVER executed as code — there is no eval/exec
anywhere in this module, satisfying "do not execute arbitrary
user-supplied code in the trusted backend." Every action a workflow can
take funnels through apps.bots.services.send_message_as_bot or
execute_bot_action, the exact same gates (live grant re-verification,
daily rate limit, approval-for-sensitive-actions) a bot's own direct API
calls go through — a workflow is configuration on top of an existing
BotResourceGrant, never a new source of authority.

Real event-publisher audit (section 2's "do not invent events the
existing backend cannot reliably produce"), as of Phase 2:
  - message.created / message.edited: real, via Nest's dispatchWebhook
    -> apps.chat.views.webhook_dispatch -> dispatch_bot_events_for_
    conversation. message.edited carries text; message.deleted carries
    no text (nothing to evaluate text-conditions against) and is not
    wired as a workflow trigger here (no corresponding UI need yet).
  - member.joined / member.left: NEWLY built in this phase — Django-only
    events (Group join/add-members/approve-request/unban and leave/
    remove-member/ban are plain Django views, never touch Nest), wired
    directly at apps.groups.services.reactivate_group_membership and
    apps.groups.views' leave/remove_member/ban actions.
  - schedule: not an event at all — a celery-beat sweep (tasks.py's
    run_due_scheduled_workflows) finds ACTIVE workflows whose
    trigger_config's interval has elapsed.
  - Deliberately NOT implemented (would require new publishers this
    phase didn't build): channel/community/partner membership events,
    reactions, posts/comments, feed events. Do not wire a trigger_type
    for any of these until a real publisher exists.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import (
    Bot,
    BotActivityAction,
    BotPermissionScope,
    BotResourceGrant,
    BotWorkflow,
    BotWorkflowRun,
    BotWorkflowRunStatus,
    BotWorkflowStatus,
    BotWorkflowTriggerType,
    BotWorkflowVersion,
)

MAX_ACTIONS_PER_BRANCH = 10
MAX_RUNS_PER_MINUTE = 20
RUN_WALL_CLOCK_BUDGET_SECONDS = 25
WORKFLOW_ABUSE_THRESHOLD = 5  # consecutive storm-detections before auto-pause

CONDITION_OPS = {
    "equals", "not_equals", "contains", "not_contains",
    "starts_with", "ends_with", "in", "not_in",
    "gt", "lt", "gte", "lte",
    "is_empty", "is_not_empty",
    "before", "after",
    "within_last_minutes", "within_last_hours",
}

# Ops that describe the field itself rather than comparing it against a
# second value - validate_definition must not require "value" for these,
# and evaluate_conditions must not KeyError looking it up.
CONDITION_OPS_WITHOUT_VALUE = {"is_empty", "is_not_empty"}

ACTION_TYPES = {"send_message", "send_notification", "execute_action"}


# ---------------------------------------------------------------------
# Definition validation — shape-checking only, never code execution.
# ---------------------------------------------------------------------

def _validate_rule(rule: dict) -> None:
    if not isinstance(rule, dict):
        raise ValidationError({"conditions": "Each rule must be an object."})
    field = rule.get("field")
    op = rule.get("op")
    if not field or not isinstance(field, str):
        raise ValidationError({"conditions": "Each rule needs a 'field'."})
    if op not in CONDITION_OPS:
        raise ValidationError({"conditions": f"Unsupported condition operator '{op}'."})
    if op not in CONDITION_OPS_WITHOUT_VALUE and "value" not in rule:
        raise ValidationError({"conditions": "Each rule needs a 'value'."})
    if op in ("within_last_minutes", "within_last_hours"):
        # Accept a numeric string too (e.g. "15") - the mobile builder's
        # value field is a plain text input, so being liberal here about
        # what counts as "numeric" avoids rejecting a perfectly valid
        # workflow just because JSON round-tripped it as a string.
        window = _as_number(rule.get("value"))
        if window is None or window <= 0:
            raise ValidationError({"conditions": f"{op} needs a positive numeric 'value'."})


def _validate_conditions(conditions: dict) -> None:
    if not conditions:
        return
    if not isinstance(conditions, dict):
        raise ValidationError({"conditions": "conditions must be an object."})
    op = conditions.get("op", "AND")
    if op not in ("AND", "OR"):
        raise ValidationError({"conditions": "conditions.op must be AND or OR."})
    rules = conditions.get("rules", [])
    if not isinstance(rules, list):
        raise ValidationError({"conditions": "conditions.rules must be a list."})
    for rule in rules:
        _validate_rule(rule)


def _validate_actions(actions: list, *, key: str) -> None:
    if not isinstance(actions, list):
        raise ValidationError({key: f"{key} must be a list."})
    if len(actions) > MAX_ACTIONS_PER_BRANCH:
        raise ValidationError({key: f"A workflow may run at most {MAX_ACTIONS_PER_BRANCH} actions per branch."})
    for action in actions:
        if not isinstance(action, dict):
            raise ValidationError({key: "Each action must be an object."})
        action_type = action.get("type")
        if action_type not in ACTION_TYPES:
            raise ValidationError({key: f"Unsupported action type '{action_type}'."})
        params = action.get("params", {})
        if not isinstance(params, dict):
            raise ValidationError({key: "Each action's params must be an object."})
        delay = action.get("delay_seconds", 0)
        if not isinstance(delay, int) or delay < 0 or delay > 86400:
            raise ValidationError({key: "delay_seconds must be an integer between 0 and 86400."})
        if action_type == "send_message" and not str(params.get("text") or "").strip():
            raise ValidationError({key: "send_message requires non-empty params.text."})
        if action_type == "execute_action" and not str(params.get("action") or "").strip():
            raise ValidationError({key: "execute_action requires params.action."})
        if action_type == "send_notification" and not str(params.get("body") or "").strip():
            raise ValidationError({key: "send_notification requires non-empty params.body."})


def validate_definition(definition: dict) -> dict:
    if not isinstance(definition, dict):
        raise ValidationError({"definition": "definition must be an object."})
    conditions = definition.get("conditions", {})
    _validate_conditions(conditions)
    _validate_actions(definition.get("then_actions", []), key="then_actions")
    _validate_actions(definition.get("else_actions", []), key="else_actions")
    return {
        "conditions": conditions,
        "then_actions": definition.get("then_actions", []),
        "else_actions": definition.get("else_actions", []),
    }


def _validate_timezone(tz_name: str) -> str:
    tz_name = (tz_name or "UTC").strip() or "UTC"
    try:
        ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError / ValueError, both mean "bad name"
        raise ValidationError({"trigger_config": f"'{tz_name}' is not a recognized timezone."})
    return tz_name


def validate_trigger_config(trigger_type: str, trigger_config: dict) -> dict:
    trigger_config = trigger_config or {}
    if trigger_type != BotWorkflowTriggerType.SCHEDULE:
        return {}

    tz_name = _validate_timezone(trigger_config.get("timezone", "UTC"))
    cron = trigger_config.get("cron")
    if cron:
        if not isinstance(cron, str) or len(cron.split()) != 5:
            raise ValidationError({
                "trigger_config": "cron must be a 5-field crontab string: 'minute hour day_of_month month day_of_week'.",
            })
        minute, hour, day_of_month, month_of_year, day_of_week = cron.split()
        try:
            from celery.schedules import crontab

            parsed = crontab(
                minute=minute, hour=hour, day_of_month=day_of_month,
                month_of_year=month_of_year, day_of_week=day_of_week,
            )
        except Exception as exc:  # noqa: BLE001 - celery raises a bare ValueError on a bad cronspec
            raise ValidationError({"trigger_config": f"Invalid cron expression: {exc}"})
        # Re-serialize from celery's own parsed sets rather than storing the
        # raw string verbatim - catches e.g. an out-of-range value celery
        # silently wraps, and guarantees what gets stored is exactly what
        # run_due_scheduled_workflows will later match against.
        return {
            "cron": cron, "timezone": tz_name,
            "_parsed": {
                "minute": sorted(parsed.minute), "hour": sorted(parsed.hour),
                "day_of_month": sorted(parsed.day_of_month), "month_of_year": sorted(parsed.month_of_year),
                "day_of_week": sorted(parsed.day_of_week),
            },
        }

    interval = trigger_config.get("interval_minutes")
    if not isinstance(interval, int) or interval < 5 or interval > 10080:
        raise ValidationError({
            "trigger_config": "Either provide 'cron' (a 5-field crontab string) or "
                              "'interval_minutes' as an integer between 5 and 10080 (one week).",
        })
    return {"interval_minutes": interval, "timezone": tz_name}


def is_schedule_due(trigger_config: dict, last_run_at, now) -> bool:
    """Section 4's extended scheduler - supports both the original
    fixed-interval mode and a new cron mode, both timezone-aware (DST
    included, since zoneinfo converts correctly across transitions by
    construction - there is no DST-specific branch to get wrong).

    Duplicate protection: cron mode never fires twice for the same
    localized (year, month, day, hour, minute) - comparing truncated-to-
    the-minute timestamps rather than relying on the sweep interval
    being exactly 60s. Missed schedules: if the sweep itself didn't run
    for a while (worker down), a cron schedule simply waits for its next
    matching minute rather than firing a backlog - documented, not a
    silent bug - and an interval schedule fires on the very next sweep
    once the elapsed time exceeds the interval, so it "catches up"
    immediately rather than waiting a full extra interval."""
    tz_name = trigger_config.get("timezone") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("UTC")

    cron = trigger_config.get("cron")
    if cron:
        parsed = trigger_config.get("_parsed") or {}
        local_now = now.astimezone(tz).replace(second=0, microsecond=0)
        if last_run_at is not None:
            local_last = last_run_at.astimezone(tz).replace(second=0, microsecond=0)
            if local_last == local_now:
                return False
        return (
            local_now.minute in set(parsed.get("minute", range(60)))
            and local_now.hour in set(parsed.get("hour", range(24)))
            and local_now.day in set(parsed.get("day_of_month", range(1, 32)))
            and local_now.month in set(parsed.get("month_of_year", range(1, 13)))
            and (local_now.isoweekday() % 7) in set(parsed.get("day_of_week", range(7)))
        )

    interval = int(trigger_config.get("interval_minutes") or 0)
    if interval <= 0:
        return False
    if last_run_at is None:
        return True
    return (now - last_run_at).total_seconds() >= interval * 60


# ---------------------------------------------------------------------
# Event context — normalizes a raw trigger payload into flat fields the
# declarative conditions above can reference.
# ---------------------------------------------------------------------

def build_event_context(trigger_type: str, payload: dict) -> dict:
    payload = payload or {}
    received_at = timezone.now().isoformat()
    if trigger_type in (BotWorkflowTriggerType.MESSAGE_CREATED, BotWorkflowTriggerType.MESSAGE_EDITED):
        context = {
            "message": {"text": payload.get("text") or ""},
            "sender": {"id": str(payload.get("senderId") or payload.get("sender_id") or "")},
            "subject_user_id": str(payload.get("senderId") or payload.get("sender_id") or ""),
        }
    elif trigger_type in (BotWorkflowTriggerType.MEMBER_JOINED, BotWorkflowTriggerType.MEMBER_LEFT):
        context = {
            "member": {"id": str(payload.get("user_id") or "")},
            "subject_user_id": str(payload.get("user_id") or ""),
        }
    elif trigger_type == BotWorkflowTriggerType.POST_CREATED:
        context = {
            "post": {
                "id": str(payload.get("post_id") or ""),
                "title": payload.get("title") or "",
                "text": payload.get("text") or "",
            },
            "author": {"id": str(payload.get("author_id") or "")},
            "subject_user_id": str(payload.get("author_id") or ""),
        }
    elif trigger_type == BotWorkflowTriggerType.COMMENT_CREATED:
        context = {
            "comment": {"id": str(payload.get("comment_id") or ""), "text": payload.get("text") or ""},
            "post": {"id": str(payload.get("post_id") or "")},
            "author": {"id": str(payload.get("author_id") or "")},
            "subject_user_id": str(payload.get("author_id") or ""),
        }
    elif trigger_type == BotWorkflowTriggerType.SCHEDULE:
        context = {"schedule": {"ran_at": received_at}}
    else:
        context = dict(payload)
    # Always available regardless of trigger_type - the anchor
    # "within_last_minutes"/"before"/"after" conditions compare other
    # timestamp fields against (section 3's time-window helpers).
    context["received_at"] = received_at
    return context


def _get_field(context: dict, dotted_field: str) -> Any:
    node: Any = context
    for part in dotted_field.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _as_number(value: Any) -> float | None:
    """Safe numeric coercion - section 3's "safe handling of missing or
    incorrectly typed fields": a gt/lt/gte/lte against a field that
    turns out to be missing, a string, a dict, etc. must evaluate to
    False, never raise and crash the execution worker."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _as_datetime(value: Any):
    """Safe ISO-8601 parsing - returns None (never raises) for anything
    that isn't a recognizable datetime, same safety guarantee as
    _as_number above."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        from django.utils.dateparse import parse_datetime

        parsed = parse_datetime(value.strip())
    except Exception:  # noqa: BLE001
        return None
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.utc)
    return parsed


def _is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, list, dict, tuple, set)):
        return len(value) == 0
    return False


def _apply_op(actual: Any, op: str, expected: Any) -> bool:
    try:
        if op == "equals":
            return actual == expected
        if op == "not_equals":
            return actual != expected
        if op == "contains":
            return isinstance(actual, str) and isinstance(expected, str) and expected.lower() in actual.lower()
        if op == "not_contains":
            return not (isinstance(actual, str) and isinstance(expected, str) and expected.lower() in actual.lower())
        if op == "starts_with":
            return isinstance(actual, str) and isinstance(expected, str) and actual.lower().startswith(expected.lower())
        if op == "ends_with":
            return isinstance(actual, str) and isinstance(expected, str) and actual.lower().endswith(expected.lower())
        if op == "in":
            return isinstance(expected, list) and actual in expected
        if op == "not_in":
            return not (isinstance(expected, list) and actual in expected)
        if op == "is_empty":
            return _is_empty_value(actual)
        if op == "is_not_empty":
            return not _is_empty_value(actual)
        if op in ("gt", "lt", "gte", "lte"):
            actual_num, expected_num = _as_number(actual), _as_number(expected)
            if actual_num is None or expected_num is None:
                return False
            if op == "gt":
                return actual_num > expected_num
            if op == "lt":
                return actual_num < expected_num
            if op == "gte":
                return actual_num >= expected_num
            return actual_num <= expected_num
        if op in ("before", "after"):
            actual_dt, expected_dt = _as_datetime(actual), _as_datetime(expected)
            if actual_dt is None or expected_dt is None:
                return False
            return actual_dt < expected_dt if op == "before" else actual_dt > expected_dt
        if op in ("within_last_minutes", "within_last_hours"):
            actual_dt = _as_datetime(actual)
            window = _as_number(expected)
            if actual_dt is None or window is None:
                return False
            seconds = window * 60 if op == "within_last_minutes" else window * 3600
            delta = (timezone.now() - actual_dt).total_seconds()
            return 0 <= delta <= seconds
    except Exception:  # noqa: BLE001
        # Any unexpected shape (e.g. a dict where a string was expected)
        # must fail the condition, never crash the worker evaluating it.
        return False
    return False


def evaluate_conditions(conditions: dict, context: dict) -> bool:
    if not conditions or not conditions.get("rules"):
        return True
    op = conditions.get("op", "AND")
    results = [
        _apply_op(_get_field(context, rule.get("field", "")), rule.get("op", ""), rule.get("value"))
        for rule in conditions["rules"]
        if isinstance(rule, dict)
    ]
    if not results:
        return True
    return all(results) if op == "AND" else any(results)


# ---------------------------------------------------------------------
# Action execution — every action goes through the exact same
# permission/rate-limit/approval gates a direct bot API call would.
# ---------------------------------------------------------------------

def _execute_send_message(bot: Bot, grant: BotResourceGrant, params: dict) -> dict:
    from . import services

    result = services.send_message_as_bot(bot, grant, str(params.get("text") or ""))
    return {"ok": True, "result": result}


def _execute_generic_action(bot: Bot, grant: BotResourceGrant, params: dict) -> dict:
    from . import services

    result = services.execute_bot_action(
        bot, grant, str(params.get("action") or ""), params.get("details") or {},
    )
    return result


def _execute_send_notification(bot: Bot, grant: BotResourceGrant, params: dict, context: dict) -> dict:
    from apps.notifications.services import create_notification

    user_id = params.get("user_id") or context.get("subject_user_id")
    if not user_id:
        raise ValidationError({"params": "send_notification needs a user_id (or a trigger with a subject user)."})
    notification = create_notification(
        user_id=user_id,
        type="BOT_WORKFLOW",
        title=str(params.get("title") or bot.name)[:200],
        body=str(params.get("body") or "")[:1000],
        source="bot_workflow",
    )
    return {"ok": True, "notification_id": str(notification.id)}


def _run_action(bot: Bot, grant: BotResourceGrant, action: dict, context: dict) -> dict:
    action_type = action.get("type")
    params = action.get("params") or {}
    if action_type == "send_message":
        return _execute_send_message(bot, grant, params)
    if action_type == "execute_action":
        return _execute_generic_action(bot, grant, params)
    if action_type == "send_notification":
        return _execute_send_notification(bot, grant, params, context)
    raise ValidationError({"action": f"Unsupported action type '{action_type}'."})


# ---------------------------------------------------------------------
# Storm / infinite-loop protection
# ---------------------------------------------------------------------

def _workflow_run_count_this_minute(workflow_id) -> int:
    key = f"bots:workflow_runs:{workflow_id}:{timezone.now().strftime('%Y%m%d%H%M')}"
    cache.add(key, 0, 65)
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, 65)
        return 1


def _record_storm_signal(workflow: BotWorkflow) -> None:
    key = f"bots:workflow_storm:{workflow.id}"
    cache.add(key, 0, 3600)
    try:
        count = cache.incr(key)
    except ValueError:
        count = 1
        cache.set(key, count, 3600)
    if count >= WORKFLOW_ABUSE_THRESHOLD:
        BotWorkflow.objects.filter(id=workflow.id).update(status=BotWorkflowStatus.PAUSED)
        from . import services

        services.log_activity(
            workflow.bot, BotActivityAction.WORKFLOW_PAUSED,
            metadata={"workflow_id": str(workflow.id), "reason": "auto-paused: run-rate storm protection"},
        )


def _workflow_concurrency_lock(workflow_id):
    """A short-lived cache lock so overlapping trigger deliveries for the
    SAME workflow never run two actions sets concurrently against the
    same resource. Returns True if the lock was acquired."""
    key = f"bots:workflow_lock:{workflow_id}"
    return cache.add(key, "1", 30)


def _release_workflow_lock(workflow_id) -> None:
    cache.delete(f"bots:workflow_lock:{workflow_id}")


# ---------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------

def run_workflow(workflow: BotWorkflow, *, event: str, event_id: str, payload: dict) -> BotWorkflowRun | None:
    """Evaluates one trigger occurrence against one workflow. Returns the
    BotWorkflowRun row, or None if the run was skipped before a row was
    even worth creating (e.g. idempotent duplicate). Never raises —
    a workflow's own bug must never break the caller (the message-send
    or membership-change path that triggered it)."""
    if workflow.status != BotWorkflowStatus.ACTIVE:
        return None

    # Idempotency: the same underlying occurrence must never run twice.
    # The create() is wrapped in its own atomic() so a duplicate-event_id
    # IntegrityError only rolls back this savepoint - without it, hitting
    # the UniqueConstraint poisons the OUTER transaction (Django refuses
    # any further query until a rollback), which would break every
    # caller that dispatches inside its own transaction (e.g. a message
    # send already inside one).
    try:
        with transaction.atomic():
            run = BotWorkflowRun.objects.create(
                workflow=workflow, workflow_version=workflow.version, trigger_event=event,
                event_id=event_id, status=BotWorkflowRunStatus.RUNNING,
            )
    except IntegrityError:
        return BotWorkflowRun.objects.filter(workflow=workflow, event_id=event_id).first()

    from . import services

    def _finish(status: str, *, error: str = "", steps=None, condition_result=None):
        run.status = status
        run.error_message = error
        run.steps_log = steps or []
        run.condition_result = condition_result
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error_message", "steps_log", "condition_result", "finished_at"])
        BotWorkflow.objects.filter(id=workflow.id).update(last_run_at=timezone.now())
        if status == BotWorkflowRunStatus.FAILED:
            services.log_activity(
                workflow.bot, BotActivityAction.WORKFLOW_RUN_FAILED,
                metadata={"workflow_id": str(workflow.id), "run_id": str(run.id), "error": error[:300]},
            )
        return run

    # Storm / infinite-loop protection — a workflow whose own actions
    # re-trigger itself (or any other runaway trigger source) gets
    # capped per-minute, then auto-paused if it keeps happening.
    if _workflow_run_count_this_minute(workflow.id) > MAX_RUNS_PER_MINUTE:
        _record_storm_signal(workflow)
        return _finish(BotWorkflowRunStatus.SKIPPED, error="Run-rate limit exceeded for this workflow.")

    if not _workflow_concurrency_lock(workflow.id):
        return _finish(BotWorkflowRunStatus.SKIPPED, error="Another run for this workflow is already in progress.")

    try:
        grant = workflow.resource_grant
        bot = workflow.bot

        if not services._grant_is_currently_valid(grant):
            return _finish(BotWorkflowRunStatus.DENIED, error="The underlying resource grant is no longer valid.")

        context = build_event_context(workflow.trigger_type, payload)
        run.event_context = context
        run.save(update_fields=["event_context"])

        condition_result = evaluate_conditions(workflow.definition.get("conditions") or {}, context)
        actions = workflow.definition.get("then_actions" if condition_result else "else_actions") or []

        steps: list[dict] = []
        deadline = timezone.now() + timedelta(seconds=RUN_WALL_CLOCK_BUDGET_SECONDS)
        for action in actions:
            if timezone.now() > deadline:
                steps.append({"action": action.get("type"), "status": "timeout"})
                return _finish(
                    BotWorkflowRunStatus.FAILED, error="Workflow exceeded its execution time budget.",
                    steps=steps, condition_result=condition_result,
                )
            delay = int(action.get("delay_seconds") or 0)
            if delay:
                # Real actions never block the worker on a sleep - a
                # delayed action is re-entered as its own Celery task
                # (see tasks.run_delayed_workflow_action) and this run
                # ends here as SUCCEEDED for its synchronous portion.
                from .tasks import run_delayed_workflow_action

                run_delayed_workflow_action.apply_async(
                    args=[str(workflow.id), action, str(run.id)], countdown=delay,
                )
                steps.append({"action": action.get("type"), "status": "scheduled", "delay_seconds": delay})
                continue
            try:
                result = _run_action(bot, grant, action, context)
                steps.append({"action": action.get("type"), "status": "ok", "result": result})
            except Exception as exc:  # noqa: BLE001 - one action's failure must not corrupt the run's own bookkeeping
                steps.append({"action": action.get("type"), "status": "error", "error": str(exc)[:300]})
                return _finish(
                    BotWorkflowRunStatus.FAILED, error=str(exc)[:500],
                    steps=steps, condition_result=condition_result,
                )

        # A "scheduled" step means a delayed action was handed off to its
        # own Celery task (see run_delayed_workflow_action) - that task
        # logs its own outcome via BotActivityLog when it actually runs
        # later; this run's own synchronous portion completed cleanly.
        return _finish(BotWorkflowRunStatus.SUCCEEDED, steps=steps, condition_result=condition_result)
    finally:
        _release_workflow_lock(workflow.id)


def dispatch_event_to_workflows(
    *, resource_type: str, resource_id: str, event: str, payload: dict, event_id: str,
    required_scope: str | None = None,
) -> int:
    """Entry point called from apps.bots.services.dispatch_bot_events_for_
    conversation (messages, on the critical message-send path) and from
    apps.bots.services.dispatch_resource_event (every non-conversation
    event: member/post/comment publishers across Groups/Channels/
    Communities/Partners/My Feed). Finds every matching ACTIVE workflow
    and hands each one to Celery (tasks.run_workflow_task) rather than
    running it inline — the actual "asynchronous execution using the
    existing Celery/Redis infrastructure" requirement: the caller (a
    message send, a group join) must never be slowed down by however
    long a workflow's own actions take. Returns the number of workflows
    ENQUEUED, not the number that finished running (that's in
    BotWorkflowRun once each task completes).

    required_scope re-checks that the MATCHING workflow's own grant (not
    just whichever grant the caller happened to be iterating) actually
    holds the scope this event requires — a workflow must never react to
    something its own resource grant isn't scoped to see, independent of
    how many other grants/bots exist on the same resource."""
    if event not in BotWorkflowTriggerType.values:
        return 0
    from .tasks import run_workflow_task

    candidates = BotWorkflow.objects.filter(
        status=BotWorkflowStatus.ACTIVE,
        trigger_type=event,
        resource_grant__resource_type=resource_type,
        resource_grant__resource_id=str(resource_id),
        resource_grant__is_active=True,
    ).select_related("resource_grant")
    sender_id = str(payload.get("senderId") or payload.get("sender_id") or "")
    workflow_ids = []
    for workflow in candidates:
        if required_scope and required_scope not in (workflow.resource_grant.scopes or []):
            continue
        if event in (BotWorkflowTriggerType.MESSAGE_CREATED, BotWorkflowTriggerType.MESSAGE_EDITED) and sender_id and str(workflow.bot_id) == sender_id:
            # The single most direct infinite-loop guard: a bot's own
            # send_message action re-enters this exact event (the bot's
            # message goes through the normal Nest send path, which fires
            # message.created like any other message) - a workflow must
            # never react to a message its own bot just sent. The storm/
            # rate-limit protection in run_workflow is the second, broader
            # safety net for every other recursion shape.
            continue
        workflow_ids.append(workflow.id)
    for workflow_id in workflow_ids:
        run_workflow_task.delay(str(workflow_id), event, f"{event_id}:{workflow_id}", payload)
    return len(workflow_ids)
