# apps/bots/tasks.py
from celery import shared_task

from . import services


@shared_task
def retry_pending_bot_webhook_deliveries() -> int:
    """Celery-beat entry point — see settings.CELERY_BEAT_SCHEDULE's
    'retry-pending-bot-webhook-deliveries' entry. Mirrors the retry-sweep
    pattern already used for other periodic KIS maintenance tasks
    (apps.media.tasks) — plain @shared_task with no name= override, so
    the task's registered name is the default "apps.bots.tasks.
    retry_pending_bot_webhook_deliveries", matching the dotted path the
    beat schedule and apps.core's verify_celery_launch guardrail both
    expect to actually import. The previous name=-override + "_task"
    suffixed function let Celery's own registry resolve the task fine
    (that's all Celery itself needs), but broke that stricter import-by-
    dotted-path guardrail check, which has no reason to know about a
    name= override."""
    return services.retry_pending_bot_webhook_deliveries()


@shared_task(bind=True, max_retries=2, default_retry_delay=15)
def run_workflow_task(self, workflow_id: str, event: str, event_id: str, payload: dict):
    """Async entry point for workflow_engine.dispatch_event_to_workflows —
    this is what actually makes workflow execution asynchronous (section
    5's "asynchronous execution using the existing Celery/Redis
    infrastructure"): a message send or group join enqueues this and
    returns immediately, never waiting on however long the workflow's
    own actions take.

    run_workflow() itself already catches every expected failure mode
    (denied permission, a failing action, a timeout) and records it on
    the BotWorkflowRun row rather than raising - retrying those would
    not help and could re-run already-executed actions. The retry here
    is only for genuinely unexpected infrastructure failures (e.g. a
    transient DB error) that happen OUTSIDE run_workflow's own try/except,
    which is the correct "retry transient infrastructure, not business
    outcomes" policy (section 5's "retry policies and failure
    handling")."""
    from .models import BotWorkflow
    from .workflow_engine import run_workflow

    try:
        workflow = BotWorkflow.objects.select_related("bot", "resource_grant").get(id=workflow_id)
    except BotWorkflow.DoesNotExist:
        return None
    try:
        run_workflow(workflow, event=event, event_id=event_id, payload=payload)
    except Exception as exc:  # noqa: BLE001 - anything escaping run_workflow is infrastructure, not business logic
        raise self.retry(exc=exc)


@shared_task
def run_delayed_workflow_action(workflow_id: str, action: dict, run_id: str):
    """Executes one action that a workflow run scheduled with a
    delay_seconds > 0 — re-entering via Celery's countdown rather than
    blocking a worker on time.sleep(), and logging its own outcome onto
    the originating BotWorkflowRun.steps_log rather than creating a
    second run row for what is still logically one trigger occurrence."""
    from .models import BotWorkflow, BotWorkflowRun, BotWorkflowStatus
    from .workflow_engine import _run_action, build_event_context

    try:
        workflow = BotWorkflow.objects.select_related("bot", "resource_grant").get(id=workflow_id)
        run = BotWorkflowRun.objects.get(id=run_id)
    except (BotWorkflow.DoesNotExist, BotWorkflowRun.DoesNotExist):
        return None

    from . import services

    step_entry = {"action": action.get("type"), "status": "ok"}
    if services._grant_is_currently_valid(workflow.resource_grant) and workflow.status == BotWorkflowStatus.ACTIVE:
        try:
            context = run.event_context or build_event_context(workflow.trigger_type, {})
            result = _run_action(workflow.bot, workflow.resource_grant, action, context)
            step_entry["result"] = result
        except Exception as exc:  # noqa: BLE001
            step_entry["status"] = "error"
            step_entry["error"] = str(exc)[:300]
    else:
        step_entry["status"] = "denied"
        step_entry["error"] = "Grant or workflow no longer active by the time the delay elapsed."

    run.steps_log = [*(run.steps_log or []), step_entry]
    run.save(update_fields=["steps_log"])


@shared_task
def run_due_scheduled_workflows() -> int:
    """Celery-beat sweep for trigger_type=SCHEDULE workflows — there is
    no external event to react to, so this periodically checks whether
    each one is due per workflow_engine.is_schedule_due (fixed-interval
    or timezone-aware cron, see that function's own docstring for the
    duplicate/missed-schedule semantics)."""
    from django.utils import timezone

    from .models import BotWorkflow, BotWorkflowStatus, BotWorkflowTriggerType
    from .workflow_engine import is_schedule_due

    triggered = 0
    now = timezone.now()
    workflows = BotWorkflow.objects.filter(
        status=BotWorkflowStatus.ACTIVE, trigger_type=BotWorkflowTriggerType.SCHEDULE,
    ).select_related("bot", "resource_grant")
    for workflow in workflows:
        if not is_schedule_due(workflow.trigger_config or {}, workflow.last_schedule_run_at, now):
            continue
        BotWorkflow.objects.filter(id=workflow.id).update(last_schedule_run_at=now)
        run_workflow_task.delay(
            str(workflow.id), "schedule", f"schedule:{workflow.id}:{now.isoformat()}", {},
        )
        triggered += 1
    return triggered
