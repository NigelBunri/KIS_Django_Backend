from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def _setting_text(name: str) -> str:
    return str(getattr(settings, name, "") or "").strip()


def _broker_reachable() -> tuple[bool, str]:
    # Builds its own Connection straight from the live Django setting
    # rather than going through config.celery.app.connection_or_acquire() —
    # that module-level Celery singleton resolves its config once via
    # config_from_object() and does not observe a Django settings override
    # made afterwards (e.g. in a test, or if CELERY_BROKER_URL is rotated
    # without a process restart), which would make this check report
    # against a stale broker URL instead of the one actually configured.
    import kombu

    try:
        # ensure_connection's own `timeout` is a deadline checked BETWEEN
        # retry attempts — it cannot interrupt a single attempt that's
        # already blocked inside the underlying blocking connect() syscall
        # (e.g. a host that silently drops packets instead of refusing the
        # connection). socket_connect_timeout/socket_timeout bound that
        # individual attempt instead, which is what actually keeps this a
        # fast, deploy-safe check rather than one that can hang for minutes.
        connection = kombu.Connection(
            _setting_text("CELERY_BROKER_URL"),
            transport_options={"socket_connect_timeout": 3, "socket_timeout": 3},
        )
        with connection as conn:
            conn.ensure_connection(max_retries=1, timeout=3)
        return True, "connected"
    except Exception as exc:  # pragma: no cover - environment-dependent diagnostic.
        return False, exc.__class__.__name__


def _worker_ping(timeout: float = 2.0) -> tuple[bool, list[str]]:
    from config.celery import app as celery_app

    try:
        replies = celery_app.control.ping(timeout=timeout) or []
    except Exception:  # pragma: no cover - environment-dependent diagnostic.
        return False, []
    names = [list(r.keys())[0] for r in replies if r]
    return bool(names), names


def _beat_schedule_tasks_are_registered() -> list[dict[str, str]]:
    # Deliberately does NOT rely on celery_app.autodiscover_tasks() +
    # celery_app.tasks — outside a real `celery worker` process (which
    # finalizes the app as a side effect of its own bootstrap),
    # autodiscover_tasks() has proven unreliable here at actually
    # populating the task registry when called from a plain management
    # command. Importing each configured task path directly and reading
    # its .name is what a real worker does anyway, and catches the same
    # class of bug (a schedule entry pointing at a typo'd or renamed
    # task path) without that fragility.
    import importlib

    results = []
    for entry_name, entry in (getattr(settings, "CELERY_BEAT_SCHEDULE", None) or {}).items():
        task_path = entry.get("task", "")
        detail = task_path
        state = "fail"
        try:
            mod_path, func_name = task_path.rsplit(".", 1)
            mod = importlib.import_module(mod_path)
            task = getattr(mod, func_name)
            if getattr(task, "name", None) == task_path:
                state = "pass"
            else:
                detail = f"{task_path} imports but is not a Celery task (no matching .name)"
        except Exception as exc:
            detail = f"{task_path} failed to import: {exc.__class__.__name__}: {exc}"
        results.append({"name": f"beat_schedule:{entry_name}", "state": state, "detail": detail})
    return results


class Command(BaseCommand):
    help = "Verify Celery worker/beat infrastructure guardrails without requiring the caller to have shell access to a worker."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        parser.add_argument("--strict", action="store_true", help="Exit non-zero when launch blockers are found.")
        parser.add_argument(
            "--skip-ping", action="store_true",
            help="Skip the live worker ping (useful in environments where this command itself can't reach the broker).",
        )

    def handle(self, *args, **options):
        checks: list[dict[str, str]] = []

        broker_url_present = bool(_setting_text("CELERY_BROKER_URL"))
        result_backend_present = bool(_setting_text("CELERY_RESULT_BACKEND"))
        checks.append({
            "name": "CELERY_BROKER_URL",
            "state": "pass" if broker_url_present else "fail",
            "detail": "presence checked only; value is never printed",
        })
        checks.append({
            "name": "CELERY_RESULT_BACKEND",
            "state": "pass" if result_backend_present else "fail",
            "detail": "presence checked only; value is never printed",
        })

        scheduler = _setting_text("CELERY_BEAT_SCHEDULER")
        checks.append({
            "name": "CELERY_BEAT_SCHEDULER",
            "state": "pass" if scheduler == "django_celery_beat.schedulers:DatabaseScheduler" else "fail",
            "detail": scheduler or "not set — beat schedule state will not survive a redeploy",
        })

        acks_late = bool(getattr(settings, "CELERY_TASK_ACKS_LATE", False))
        checks.append({
            "name": "CELERY_TASK_ACKS_LATE",
            "state": "pass" if acks_late else "warn",
            "detail": "enabled" if acks_late else "a crashed worker will silently drop its in-flight task",
        })

        beat_schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", None) or {}
        checks.append({
            "name": "CELERY_BEAT_SCHEDULE",
            "state": "pass" if beat_schedule else "warn",
            "detail": f"{len(beat_schedule)} periodic task(s) configured" if beat_schedule else "no periodic tasks configured",
        })
        checks.extend(_beat_schedule_tasks_are_registered())

        if broker_url_present:
            broker_ok, broker_detail = _broker_reachable()
            checks.append({
                "name": "broker_connectivity",
                "state": "pass" if broker_ok else "fail",
                "detail": broker_detail,
            })
            if broker_ok and not options["skip_ping"]:
                worker_alive, worker_names = _worker_ping()
                checks.append({
                    "name": "worker_ping",
                    "state": "pass" if worker_alive else "warn",
                    "detail": f"{len(worker_names)} worker(s) responded" if worker_alive else "no worker responded — is one running?",
                })

        failures = [c for c in checks if c["state"] == "fail"]
        warnings = [c for c in checks if c["state"] == "warn"]
        result = {
            "ready": not failures,
            "summary": {"failures": len(failures), "warnings": len(warnings), "checks": len(checks)},
            "checks": checks,
            "notes": [
                "This command does not start, stop, or restart any worker/beat process.",
                "worker_ping only reports state if a worker happens to be reachable at the moment this command runs.",
            ],
        }

        if options["json"]:
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
        else:
            self.stdout.write(f"Celery infrastructure guardrails ready: {result['ready']}")
            for check in checks:
                self.stdout.write(f"- {check['state'].upper()}: {check['name']} - {check['detail']}")
            for note in result["notes"]:
                self.stdout.write(f"Note: {note}")

        if failures and options["strict"]:
            raise CommandError(f"Celery infrastructure guardrails failed: {len(failures)} blocker(s).")
