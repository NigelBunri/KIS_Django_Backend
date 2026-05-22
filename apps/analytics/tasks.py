import math

from celery import shared_task
from django.db.models import F, Sum
from .models import (
    Metric,
    EventStream,
    EngagementScore,
    RiskStratification,
    ClinicalAnalyticsReport,
)
from django.utils import timezone
from decimal import Decimal
from apps.core import models as core_models

# event_type -> (kind, metric_name, unit)
_EVENT_METRIC_MAP = {
    'message_sent':     ('engagement', 'messages_sent',     'count'),
    'message_read':     ('engagement', 'messages_read',     'count'),
    'broadcast_view':   ('engagement', 'broadcast_views',   'count'),
    'channel_follow':   ('engagement', 'channel_follows',   'count'),
    'channel_unfollow': ('engagement', 'channel_unfollows', 'count'),
    'status_view':      ('engagement', 'status_views',      'count'),
    'status_created':   ('engagement', 'statuses_created',  'count'),
    'call_started':     ('engagement', 'calls_started',     'count'),
    'call_ended':       ('engagement', 'calls_ended',       'count'),
    'shop_view':        ('partner',    'shop_views',        'count'),
    'product_view':     ('partner',    'product_views',     'count'),
    'order_placed':     ('partner',    'orders_placed',     'count'),
    'login':            ('system',     'logins',            'count'),
    'signup':           ('system',     'signups',           'count'),
}


@shared_task
def process_event_stream(event_id):
    ev = EventStream.objects.filter(id=event_id, processed=False).first()
    if not ev:
        return

    payload = ev.payload or {}
    event_type = ev.event_type

    if event_type in _EVENT_METRIC_MAP:
        kind, name, unit = _EVENT_METRIC_MAP[event_type]
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        source = f'event:{event_type}'
        tags = {k: str(v) for k, v in payload.items() if k in ('target_id', 'user_id', 'channel_id', 'shop_id')}

        updated = Metric.objects.filter(
            name=name, kind=kind, source=source, captured_at__gte=today_start
        ).update(value=F('value') + 1)

        if not updated:
            Metric.objects.create(
                name=name,
                kind=kind,
                source=source,
                value=1.0,
                unit=unit,
                captured_at=timezone.now(),
                tags=tags,
            )

    ev.processed = True
    ev.save(update_fields=['processed'])


@shared_task
def compute_engagement_for_target(target_id):
    since = timezone.now() - timezone.timedelta(days=7)

    metrics = Metric.objects.filter(
        tags__target_id=str(target_id),
        captured_at__gte=since,
        kind='engagement',
    )
    total_events = float(metrics.aggregate(total=Sum('value'))['total'] or 0.0)
    distinct_types = metrics.values('name').distinct().count()

    diversity = min(1.0, distinct_types / 5.0)
    raw_score = math.log1p(total_events) * 10 * diversity
    score = min(100.0, raw_score)

    obj = EngagementScore.objects.create(
        target_id=target_id,
        score_type='activity',
        value=round(score, 2),
        calculated_at=timezone.now(),
        metadata={
            'window_days': 7,
            'total_events': total_events,
            'distinct_metric_types': distinct_types,
        },
    )
    return str(obj.id)


@shared_task
def compute_predictive_metrics(metric_id):
    metric = Metric.objects.get(id=metric_id)

    # Collect recent history, oldest-first
    history = list(
        Metric.objects.filter(
            name=metric.name,
            kind=metric.kind,
            source=metric.source,
        ).order_by('-captured_at')[:30].values_list('value', flat=True)
    )

    if not history:
        metric.predicted_value = metric.value
        metric.confidence = 0.5
        metric.save(update_fields=['predicted_value', 'confidence'])
        return

    history.reverse()

    # EWMA with alpha=0.3 (heavier weight on recent observations)
    alpha = 0.3
    ewma = float(history[0])
    for val in history[1:]:
        ewma = alpha * float(val) + (1 - alpha) * ewma

    # One-step-ahead forecast using linear trend component
    if len(history) >= 2:
        recent_trend = (float(history[-1]) - float(history[0])) / len(history)
        predicted = ewma + recent_trend
    else:
        predicted = ewma

    # Confidence from coefficient of variation of residuals
    if len(history) >= 3:
        variance = sum((float(v) - ewma) ** 2 for v in history) / len(history)
        std = math.sqrt(variance)
        cv = std / (abs(ewma) + 1e-9)
        confidence = max(0.1, min(0.99, 1.0 - min(1.0, cv)))
    else:
        confidence = 0.6

    metric.predicted_value = round(predicted, 4)
    metric.confidence = round(confidence, 4)
    metric.save(update_fields=['predicted_value', 'confidence'])


def _risk_level_from_score(score: Decimal) -> str:
    if score >= Decimal('80.00'):
        return 'critical'
    if score >= Decimal('60.00'):
        return 'high'
    if score >= Decimal('40.00'):
        return 'moderate'
    return 'low'


@shared_task
def compute_risk_stratification():
    active_patients = core_models.PatientMasterRecord.objects.filter(status=core_models.PatientMasterRecord.STATUS_ACTIVE)
    for patient in active_patients:
        vitals = core_models.VitalSign.objects.filter(patient=patient).order_by('-recorded_at')[:5]
        clinical_tasks = core_models.ClinicalTask.objects.filter(patient=patient).exclude(status=core_models.ClinicalTask.STATUS_COMPLETED)
        abnormal_vitals = sum(
            1
            for vital in vitals
            if vital.value is not None and (
                vital.vital_type in (core_models.VitalSign.TYPE_BP, core_models.VitalSign.TYPE_TEMPERATURE)
                and float(vital.value) > 100
            )
        )
        task_count = clinical_tasks.count()
        score = Decimal(str(min(100, 20 + abnormal_vitals * 15 + min(task_count, 5) * 5)))
        level = _risk_level_from_score(score)
        drivers = {
            'abnormal_vitals': abnormal_vitals,
            'pending_tasks': task_count,
        }
        patient_profile = None
        if patient.organization:
            patient_profile = patient.organization.profiles.first()
        RiskStratification.objects.update_or_create(
            patient=patient,
            defaults={
                'profile': patient_profile,
                'score': score,
                'level': level,
                'drivers': drivers,
                'assessed_at': timezone.now(),
            },
        )


@shared_task
def generate_clinical_analytics_report(profile_id: str | None = None):
    profiles = core_models.MedicalProfile.objects.filter(status=core_models.MedicalProfile.STATUS_ACTIVE)
    if profile_id:
        profiles = profiles.filter(id=profile_id)
    period_end = timezone.now().date()
    period_start = period_end - timezone.timedelta(days=30)
    for profile in profiles:
        organization = profile.organization
        pending_tasks = core_models.ClinicalTask.objects.filter(profile=profile, status__in=[
            core_models.ClinicalTask.STATUS_PENDING, core_models.ClinicalTask.STATUS_IN_PROGRESS,
        ]).count()
        escalations = core_models.EmergencyEscalation.objects.filter(
            patient__organization=organization,
            status__in=[core_models.EmergencyEscalation.STATUS_PENDING, core_models.EmergencyEscalation.STATUS_ESCALATED],
        ).count()
        triage_elevated = core_models.TriageRecord.objects.filter(
            patient__organization=organization,
            acuity_level__in=[core_models.TriageRecord.ACUITY_ELEVATED, core_models.TriageRecord.ACUITY_URGENT],
        ).count()
        population_size = core_models.PatientMasterRecord.objects.filter(organization=organization).count()
        metrics = {
            'pending_tasks': pending_tasks,
            'active_escalations': escalations,
            'elevated_triage': triage_elevated,
            'population_size': population_size,
        }
        ClinicalAnalyticsReport.objects.update_or_create(
            profile=profile,
            report_type='clinical_summary',
            defaults={
                'organization': organization,
                'summary': f'{profile.name} has {pending_tasks} pending tasks and {escalations} active escalations.',
                'metrics': metrics,
                'period_start': period_start,
                'period_end': period_end,
                'status': 'published',
            },
        )
