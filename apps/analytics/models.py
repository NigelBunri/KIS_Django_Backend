from decimal import Decimal

from django.conf import settings
from django.db import models
import uuid
from django.utils import timezone
from django.db.models import JSONField
from apps.core import models as core_models

class BaseEntity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        abstract = True

class Metric(BaseEntity):
    KIND_CHOICES = [
        ('system','system'),('engagement','engagement'),('partner','partner'),('predictive','predictive')
    ]
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    name = models.CharField(max_length=255)
    value = models.FloatField()
    unit = models.CharField(max_length=32, blank=True)
    captured_at = models.DateTimeField(default=timezone.now)
    tags = JSONField(default=dict)
    source = models.CharField(max_length=64, default='internal')
    predicted_value = models.FloatField(null=True, blank=True)
    confidence = models.FloatField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=['name','captured_at']), models.Index(fields=['kind'])]

class EventStream(BaseEntity):
    event_type = models.CharField(max_length=128)
    payload = JSONField(default=dict)
    timestamp = models.DateTimeField(default=timezone.now)
    processed = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=['event_type','timestamp'])]

class Dashboard(BaseEntity):
    org_id = models.UUIDField(null=True, blank=True)
    partner_id = models.UUIDField(null=True, blank=True)
    name = models.CharField(max_length=255)
    definition = JSONField(default=dict)  # widgets, layout, queries
    is_shared = models.BooleanField(default=False)
    auto_update = models.BooleanField(default=True)

class AppSetting(BaseEntity):
    SCOPE_CHOICES = [('global','global'),('org','org'),('user','user'),('partner','partner')]
    key = models.CharField(max_length=255)
    value = JSONField(default=dict)
    scope = models.CharField(max_length=32, choices=SCOPE_CHOICES, default='global')
    audience = JSONField(null=True, blank=True)
    adaptive_rules = JSONField(null=True, blank=True)

    class Meta:
        unique_together = [('key','scope')]

class FeatureFlag(BaseEntity):
    key = models.CharField(max_length=255, unique=True)
    enabled = models.BooleanField(default=False)
    audience = JSONField(default=dict)
    experiment_id = models.UUIDField(null=True, blank=True)
    partner_visible = models.BooleanField(default=False)

class Alert(BaseEntity):
    SEVERITY_CHOICES = [('low','low'),('medium','medium'),('high','high'),('critical','critical')]
    metric = models.ForeignKey(Metric, related_name='alerts', on_delete=models.CASCADE)
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES)
    condition = JSONField(default=dict)
    triggered_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.UUIDField(null=True, blank=True)
    audience = JSONField(null=True, blank=True)

class EngagementScore(BaseEntity):
    target_id = models.UUIDField()
    score_type = models.CharField(max_length=64)
    value = models.FloatField()
    calculated_at = models.DateTimeField(default=timezone.now)
    metadata = JSONField(default=dict)

    class Meta:
        indexes = [models.Index(fields=['target_id','score_type','calculated_at'])]


class ClinicalAnalyticsReport(BaseEntity):
    REPORT_TYPE_CHOICES = [
        ('clinical_summary', 'Clinical summary'),
        ('population_health', 'Population health'),
    ]

    profile = models.ForeignKey(
        core_models.MedicalProfile,
        on_delete=models.CASCADE,
        related_name='analytics_reports',
    )
    organization = models.ForeignKey(
        core_models.HealthcareOrganization,
        on_delete=models.CASCADE,
        related_name='analytics_reports',
    )
    report_type = models.CharField(max_length=32, choices=REPORT_TYPE_CHOICES)
    summary = models.TextField(blank=True)
    metrics = JSONField(default=dict, blank=True)
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=32, default='draft')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='analytics_reports',
    )

    class Meta:
        db_table = 'analytics_clinical_report'
        ordering = ['-created_at']


class RiskStratification(BaseEntity):
    RISK_LEVEL_CHOICES = [
        ('low', 'Low'),
        ('moderate', 'Moderate'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ]

    patient = models.ForeignKey(
        core_models.PatientMasterRecord,
        on_delete=models.CASCADE,
        related_name='risk_assessments',
    )
    profile = models.ForeignKey(
        core_models.MedicalProfile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='risk_assessments',
    )
    score = models.DecimalField(decimal_places=2, max_digits=5, default=Decimal('0.00'))
    level = models.CharField(max_length=16, choices=RISK_LEVEL_CHOICES, default='low')
    drivers = JSONField(default=list, blank=True)
    assessed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'analytics_risk_stratification'
        ordering = ['-assessed_at']


class OutcomeBenchmark(BaseEntity):
    profile = models.ForeignKey(
        core_models.MedicalProfile,
        on_delete=models.CASCADE,
        related_name='outcome_benchmarks',
    )
    metric_name = models.CharField(max_length=120)
    actual_value = models.DecimalField(decimal_places=2, max_digits=10)
    target_value = models.DecimalField(decimal_places=2, max_digits=10)
    period_start = models.DateField()
    period_end = models.DateField()
    notes = models.TextField(blank=True)

    class Meta:
        db_table = 'analytics_outcome_benchmark'
        ordering = ['-period_start']


class PatientSatisfactionScore(BaseEntity):
    CHANNEL_CHOICES = [
        ('app', 'App'),
        ('sms', 'SMS'),
        ('email', 'Email'),
        ('call', 'Call'),
    ]

    patient = models.ForeignKey(
        core_models.PatientMasterRecord,
        on_delete=models.CASCADE,
        related_name='satisfaction_scores',
    )
    profile = models.ForeignKey(
        core_models.MedicalProfile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='satisfaction_scores',
    )
    score = models.IntegerField(default=0)
    channel = models.CharField(max_length=32, choices=CHANNEL_CHOICES, default='app')
    comments = models.TextField(blank=True)
    recorded_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=32, default='completed')

    class Meta:
        db_table = 'analytics_patient_satisfaction'
        ordering = ['-recorded_at']


class OutreachCampaign(BaseEntity):
    STATUS_CHOICES = [
        ('planned', 'Planned'),
        ('active', 'Active'),
        ('completed', 'Completed'),
    ]

    profile = models.ForeignKey(
        core_models.MedicalProfile,
        on_delete=models.CASCADE,
        related_name='outreach_campaigns',
    )
    name = models.CharField(max_length=160)
    channel = models.CharField(max_length=64)
    target_population = JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='planned')
    launched_at = models.DateTimeField(null=True, blank=True)
    metrics = JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'analytics_outreach_campaign'
        ordering = ['-created_at']


class WellnessChallenge(BaseEntity):
    profile = models.ForeignKey(
        core_models.MedicalProfile,
        on_delete=models.CASCADE,
        related_name='wellness_challenges',
    )
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    goal = models.CharField(max_length=200)
    participation_target = models.PositiveIntegerField(default=0)
    metadata = JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'analytics_wellness_challenge'
        ordering = ['-start_date']


class HabitTrackingEntry(BaseEntity):
    challenge = models.ForeignKey(
        WellnessChallenge,
        on_delete=models.CASCADE,
        related_name='habit_entries',
    )
    patient = models.ForeignKey(
        core_models.PatientMasterRecord,
        on_delete=models.CASCADE,
        related_name='habit_entries',
    )
    habit_name = models.CharField(max_length=120)
    progress_value = models.DecimalField(decimal_places=2, max_digits=8, default=Decimal('0.00'))
    notes = models.TextField(blank=True)
    logged_at = models.DateTimeField(default=timezone.now)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'analytics_habit_tracking'
        ordering = ['-logged_at']
