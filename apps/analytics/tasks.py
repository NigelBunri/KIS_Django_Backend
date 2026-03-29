from celery import shared_task
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

@shared_task
def compute_predictive_metrics(metric_id):
    # placeholder: load historical values and run a simple sklearn model
    metric = Metric.objects.get(id=metric_id)
    # fake prediction
    metric.predicted_value = metric.value * 1.05
    metric.confidence = 0.85
    metric.save()

@shared_task
def process_event_stream(event_id):
    ev = EventStream.objects.get(id=event_id)
    # parse event and create metrics
    # example: message_sent -> increment counters
    ev.processed = True
    ev.save()

@shared_task
def compute_engagement_for_target(target_id):
    # aggregate metrics and compute engagement score
    score = EngagementScore.objects.create(target_id=target_id, score_type='activity', value=42.0, calculated_at=timezone.now(), metadata={})
    return str(score.id)


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
