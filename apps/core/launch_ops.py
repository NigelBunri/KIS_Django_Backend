from __future__ import annotations

import os
from typing import Any

from django.conf import settings
from django.utils import timezone

from .safety_command_center import staff_safety_command_center_summary
from .security_launch_gate import security_privacy_child_safety_launch_gate


def _configured(name: str) -> bool:
    return bool(str(os.environ.get(name, '')).strip())


def _safe_flag(name: str, default: bool = False) -> bool:
    return bool(getattr(settings, name, default))


def _check(key: str, label: str, ready: bool, detail: str, severity: str = 'critical') -> dict[str, Any]:
    return {
        'key': key,
        'label': label,
        'status': 'ready' if ready else 'blocked',
        'severity': severity,
        'detail': detail,
    }


def _readiness_percent(checks: list[dict[str, Any]]) -> int:
    if not checks:
        return 0
    ready = sum(1 for item in checks if item.get('status') == 'ready')
    return round((ready / len(checks)) * 100)


def staff_launch_operations_summary() -> dict[str, Any]:
    safety = staff_safety_command_center_summary()
    security = security_privacy_child_safety_launch_gate()

    security_summary = security.get('summary', {})
    safety_status = safety.get('overall_status', 'warning')
    safety_counts = safety.get('counts', {})
    security_go_live = security_summary.get('go_live_status', 'blocked')

    provider_checks = [
        _check(
            'firebase_admin_evidence',
            'Firebase/admin proof',
            _configured('FIREBASE_CREDENTIALS_FILE') or _configured('FIREBASE_CREDENTIALS_JSON'),
            'Push provider credentials must be mounted through environment or secret files before production notification launch.',
            'warning',
        ),
        _check(
            'flutterwave_callback_evidence',
            'Flutterwave callback proof',
            _configured('KIS_FLUTTERWAVE_CALLBACK_EVIDENCE_URL') or _configured('FLUTTERWAVE_WEBHOOK_SECRET'),
            'Direct USD payment callback signing and replay proof must be recorded before payment launch.',
            'critical',
        ),
        _check(
            'backup_restore_evidence',
            'Backup/restore proof',
            _configured('KIS_BACKUP_RESTORE_EVIDENCE_URL'),
            'Database backup and restore drill evidence must be attached before production launch.',
            'critical',
        ),
        _check(
            'rollback_drill_evidence',
            'Rollback drill proof',
            _configured('KIS_ROLLBACK_DRILL_EVIDENCE_URL'),
            'Application rollback and environment rollback proof must be attached before launch.',
            'critical',
        ),
        _check(
            'private_media_tabletop',
            'Private media tabletop proof',
            _configured('KIS_PRIVATE_MEDIA_TABLETOP_EVIDENCE_URL'),
            'Private media access, signed references, and incident response must be rehearsed without exposing raw paths.',
            'warning',
        ),
    ]

    flag_checks = [
        _check(
            'legacy_wallet_checkout_disabled',
            'Legacy wallet checkout disabled',
            not _safe_flag('KIS_LEGACY_WALLET_CHECKOUT_ENABLED', False),
            'Wallet/KIS-credit checkout must remain disabled by default; direct USD provider payments are the launch path.',
        ),
        _check(
            'wallet_deposit_disabled',
            'Wallet deposit disabled',
            not _safe_flag('KIS_LEGACY_WALLET_DEPOSIT_ENABLED', False),
            'Coin-as-money deposit/top-up behavior must remain disabled.',
        ),
        _check(
            'wallet_transfer_disabled',
            'Wallet transfer disabled',
            not _safe_flag('KIS_LEGACY_WALLET_TRANSFER_ENABLED', False),
            'Peer-to-peer wallet/credit transfer must remain disabled.',
        ),
        _check(
            'public_indexing_gated',
            'Public indexing gated',
            not _safe_flag('KIS_PUBLIC_WEB_INDEXING_ENABLED', False) or _configured('KIS_PUBLIC_INDEXING_APPROVAL_URL'),
            'Public robots/sitemap indexing should stay disabled until product/legal approval evidence exists.',
            'warning',
        ),
        _check(
            'live_ai_disabled',
            'Live AI calls gated',
            not _safe_flag('KIS_AI_PROVIDER_LIVE_CALLS_ENABLED', False),
            'Live AI provider calls must stay disabled until Christian/safety boundaries are reviewed.',
            'warning',
        ),
    ]

    operational_checks = [
        _check(
            'safety_command_center_available',
            'Safety command center healthy',
            safety_status in {'healthy', 'warning'},
            'Staff command center returns redacted media, moderation, payment, messaging, notification, and provider signals.',
        ),
        _check(
            'security_gate_no_critical_failures',
            'Security launch gate has no critical failures',
            int(security_summary.get('critical_failures') or 0) == 0,
            'Production security/privacy/child-safety gate must have zero critical failures.',
        ),
        _check(
            'media_queue_visible',
            'Media safety queue visible',
            'media_open_queue' in safety_counts,
            'Staff can see quarantined, blocked, failed, and review-required media counts.',
        ),
        _check(
            'payment_incidents_visible',
            'Payment incidents visible',
            'payment_pending_intents' in safety_counts,
            'Staff can see direct-payment pending/failed state without raw provider payloads.',
        ),
        _check(
            'messaging_health_visible',
            'Messaging health visible',
            'messaging_active_conversations' in safety_counts,
            'Staff can see conversation/subroom health summaries without private message bodies.',
        ),
    ]

    all_checks = operational_checks + provider_checks + flag_checks
    critical_blockers = [item for item in all_checks if item['status'] != 'ready' and item['severity'] == 'critical']
    warnings = [item for item in all_checks if item['status'] != 'ready' and item['severity'] == 'warning']

    if critical_blockers or security_go_live == 'blocked':
        go_no_go = 'no_go'
    elif warnings or safety_status == 'warning' or security_go_live == 'conditional':
        go_no_go = 'conditional_go'
    else:
        go_no_go = 'go'

    return {
        'version': 'phase_13_code_completion_launch_ops',
        'generated_at': timezone.now().isoformat(),
        'go_no_go': go_no_go,
        'readiness_percent': _readiness_percent(all_checks),
        'summary': {
            'critical_blockers': len(critical_blockers),
            'warnings': len(warnings),
            'safety_status': safety_status,
            'security_status': security_go_live,
            'checks_total': len(all_checks),
            'checks_ready': sum(1 for item in all_checks if item['status'] == 'ready'),
        },
        'sections': {
            'operational': operational_checks,
            'provider_evidence': provider_checks,
            'production_flags': flag_checks,
        },
        'safe_counts': {
            'media_open_queue': safety_counts.get('media_open_queue', 0),
            'moderation_pending_flags': safety_counts.get('moderation_pending_flags', 0),
            'verification_open_cases': safety_counts.get('verification_open_cases', 0),
            'payment_pending_intents': safety_counts.get('payment_pending_intents', 0),
            'notification_failed_deliveries': safety_counts.get('notification_failed_deliveries', 0),
            'messaging_active_conversations': safety_counts.get('messaging_active_conversations', 0),
        },
        'blockers': [item['key'] for item in critical_blockers],
        'warnings': [item['key'] for item in warnings],
        'privacy': {
            'staff_only': True,
            'redacted': True,
            'no_secret_values': True,
            'no_raw_provider_payloads': True,
            'no_raw_documents': True,
            'no_private_health_records': True,
            'no_payment_instrument_data': True,
            'no_raw_storage_paths': True,
        },
    }
