from __future__ import annotations

import json
from io import StringIO
from typing import Any

from django.core.management import call_command, get_commands
from django.core.management.base import BaseCommand, CommandError

from apps.core.launch_ops import staff_launch_operations_summary


SAFE_MODULE_COMMANDS = [
    ("deployment_security", "verify_deployment_security", ["--target-production"]),
    ("profile", "verify_profile_launch", []),
    ("messaging_search_discovery", "verify_search_discovery_launch", []),
    ("media_safety", "verify_media_safety_launch", []),
    ("payments", "verify_payment_launch", []),
    ("commerce", "verify_commerce_launch", []),
    ("broadcast_channels", "verify_broadcast_channels_launch", []),
    ("public_web_embeds", "verify_public_web_launch", []),
    ("education", "verify_education_launch", []),
    ("health", "verify_health_launch", []),
    ("partners", "verify_partners_launch", []),
    ("bible_kcan", "verify_bible_launch", []),
    ("verification_trust", "verify_verification_launch", []),
]

MANUAL_EVIDENCE_AREAS = [
    "Render Django service deploy smoke: /health or root API responds, migrations applied, static files collected",
    "Render NestJS service deploy smoke: /health responds, Socket.IO accepts authenticated connection",
    "Supabase storage smoke: profile upload, private media reference, signed access where required",
    "Flutterwave sandbox smoke: payment link, signed webhook success/failure/duplicate/unmatched replay",
    "React Native Android APK smoke: login, messaging, uploads, payments, public content share",
    "React Native iOS/staging build smoke: login, messaging, uploads, payments, public content share",
    "Notifications smoke: device token registration and one in-app/push delivery path",
    "Staff-only admin smoke: non-staff receives 403 for safety, security, launch operations, and evidence consoles",
    "Rollback smoke: Django/Nest/app environment rollback runbook rehearsed and evidence attached",
]


def _trim_output(value: str, limit: int = 1200) -> str:
    text = str(value or '').strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _state_from_output(output: str, error: str = '') -> str:
    lowered = f"{output}\n{error}".lower()
    if error:
        return "blocked"
    if "failed" in lowered or "fail:" in lowered or "ready: false" in lowered or "ready false" in lowered:
        return "needs_evidence"
    if "ready: true" in lowered or "guardrails ready: true" in lowered or "checks passing" in lowered:
        return "passed"
    return "prepared"


def _run_safe_command(command_name: str, args: list[str]) -> dict[str, Any]:
    buffer = StringIO()
    try:
        call_command(command_name, *args, stdout=buffer)
        output = buffer.getvalue()
        return {
            "command": command_name,
            "args": args,
            "state": _state_from_output(output),
            "output_excerpt": _trim_output(output),
        }
    except BaseException as exc:  # Management commands may raise SystemExit under --strict-like paths.
        output = buffer.getvalue()
        return {
            "command": command_name,
            "args": args,
            "state": "blocked",
            "error": exc.__class__.__name__,
            "detail": _trim_output(str(exc), 700),
            "output_excerpt": _trim_output(output),
        }


class Command(BaseCommand):
    help = "Run or prepare the final KIS launch smoke evidence bundle without live provider calls or secret output."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        parser.add_argument("--strict", action="store_true", help="Exit non-zero if any command blocks or launch ops is no-go.")
        parser.add_argument("--skip-module-checks", action="store_true", help="Only emit launch operations and manual evidence checklist.")

    def handle(self, *args, **options):
        available = get_commands()
        launch_ops = staff_launch_operations_summary()
        command_results: list[dict[str, Any]] = []

        if not options["skip_module_checks"]:
            for label, command_name, command_args in SAFE_MODULE_COMMANDS:
                if command_name not in available:
                    command_results.append({
                        "label": label,
                        "command": command_name,
                        "args": command_args,
                        "state": "blocked",
                        "detail": "management command is missing",
                    })
                    continue
                result = _run_safe_command(command_name, command_args)
                result["label"] = label
                command_results.append(result)

        blocked = [item for item in command_results if item.get("state") == "blocked"]
        needs_evidence = [item for item in command_results if item.get("state") == "needs_evidence"]
        launch_no_go = launch_ops.get("go_no_go") == "no_go"
        final_status = "no_go" if blocked or launch_no_go else "conditional_go" if needs_evidence or launch_ops.get("go_no_go") != "go" else "go"

        payload = {
            "version": "phase_14_final_launch_smoke",
            "final_status": final_status,
            "launch_ops": {
                "go_no_go": launch_ops.get("go_no_go"),
                "readiness_percent": launch_ops.get("readiness_percent"),
                "critical_blockers": launch_ops.get("summary", {}).get("critical_blockers", 0),
                "warnings": launch_ops.get("summary", {}).get("warnings", 0),
                "blockers": launch_ops.get("blockers", []),
                "warning_keys": launch_ops.get("warnings", []),
            },
            "module_checks": command_results,
            "manual_evidence_required": MANUAL_EVIDENCE_AREAS,
            "rollback_notes": [
                "Keep current production release artifact identifiers in the release ticket.",
                "Rollback Django and Nest independently; verify health endpoints after each rollback.",
                "Keep legacy wallet/KIS-credit-as-money flags disabled during rollback.",
                "Disable public indexing, live AI/provider calls, and live charges if any incident appears.",
            ],
            "privacy": {
                "no_secret_values": True,
                "no_raw_provider_payloads": True,
                "no_private_media_paths": True,
                "no_private_health_or_payment_data": True,
            },
        }

        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True, default=str))
        else:
            self.stdout.write(f"KIS final launch smoke status: {final_status}")
            self.stdout.write(f"Launch ops: {payload['launch_ops']['go_no_go']} ({payload['launch_ops']['readiness_percent']}% ready)")
            if command_results:
                self.stdout.write("Module checks:")
                for item in command_results:
                    self.stdout.write(f"- {item.get('label')}: {item.get('state')} ({item.get('command')})")
            else:
                self.stdout.write("Module checks skipped; manual evidence checklist emitted.")
            self.stdout.write("Manual evidence still required:")
            for item in MANUAL_EVIDENCE_AREAS:
                self.stdout.write(f"- {item}")
            self.stdout.write("Rollback notes:")
            for item in payload["rollback_notes"]:
                self.stdout.write(f"- {item}")

        if options["strict"] and final_status == "no_go":
            raise CommandError("Final launch smoke is no-go; attach missing evidence or fix blockers before release.")
