"""
AI Integration Django App (ai_integration)

Purpose:
- This app provides a full-featured AI orchestration layer for KIS: job management, pipelines, QnA sessions, translation requests, feedback loops, and scheduling.
- Designed as a modular, extensible free-tier-only implementation. Replace service adapters with paid providers (OpenAI, Hugging Face, etc.) later.

Key components included:
- Models: AIJob, TranslationRequest, QnASession, AIModel, AIPipeline, AIJobFeedback, AISchedule
- Serializers and ViewSets using DRF for API exposure
- Celery tasks for asynchronous execution and django-celery-beat / cron support guidance
- Admin registrations
- Service adapters located in services.py (stubs for free tier)

Quick install (assumes an existing Django project):
1. Add "ai_integration" to INSTALLED_APPS.
2. Ensure DRF and Celery are installed and configured.
3. Run migrations: python manage.py makemigrations ai_integration && python manage.py migrate
4. Start Celery worker: celery -A your_project worker -l info
5. (Optional) Start celery beat for scheduling: celery -A your_project beat -l info

Notes and next steps:
- Replace the stub implementations in services.py with connectors to real model providers.
- Add rate limiting, quota enforcement and billing layer when moving to paid tier.
- Add WebSocket/Channels for streaming QnA sessions (recommended: Django Channels + Redis).
- Possibly split heavy processing to separate microservices for scalability.

"""

import uuid
from django.db import models
# from django.contrib.postgres.fields import JSONField
from django.db.models import JSONField



class BaseEntity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        abstract = True


class AIModel(BaseEntity):
    name = models.CharField(max_length=200)
    version = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    trained_at = models.DateTimeField(null=True, blank=True)
    metadata = JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = (('name', 'version'),)

    def __str__(self):
        return f"{self.name}@{self.version}"


class AIJob(BaseEntity):
    JOB_TYPES = [
        ('TRANSLATION', 'Translation'),
        ('SUMMARIZATION', 'Summarization'),
        ('RECOMMENDATION', 'Recommendation'),
        ('MODERATION', 'Moderation'),
        ('CUSTOM', 'Custom'),
    ]
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('RUNNING', 'Running'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('RETRY', 'Retry'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job_type = models.CharField(max_length=50, choices=JOB_TYPES)
    input_ref_type = models.CharField(max_length=100)
    input_ref_id = models.UUIDField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    result_ref = models.CharField(max_length=255, blank=True)
    error = models.TextField(blank=True)
    model_version = models.CharField(max_length=200, blank=True)
    priority = models.CharField(max_length=20, default='normal')
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    retries = models.IntegerField(default=0)
    metadata = JSONField(default=dict, blank=True)
    # 64, not 20: apps.ai_integration.views stores str(request.user.id) here
    # (a 36-char UUID) on every real job/pipeline creation — the previous
    # max_length=20 made every such insert fail with a DB-level
    # DataError, unconditionally. AIJobFeedback's own triggered_by
    # (below) already uses max_length=50 for the same value shape; 64
    # gives headroom beyond a bare UUID without being unbounded.
    triggered_by = models.CharField(max_length=64, default='USER')

    def __str__(self):
        return f"AIJob {self.id} ({self.job_type})"


class TranslationRequest(BaseEntity):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.OneToOneField(AIJob, on_delete=models.CASCADE, related_name='translation_request')
    source_lang = models.CharField(max_length=16)
    target_lang = models.CharField(max_length=16)
    text_chars = models.IntegerField()
    result_text = models.TextField(blank=True)
    quality_score = models.FloatField(null=True, blank=True)

    def __str__(self):
        return f"TranslationRequest {self.id} {self.source_lang}->{self.target_lang}"


class QnASession(BaseEntity):
    SESSION_STATUS = [('ACTIVE', 'Active'), ('CLOSED', 'Closed'), ('ARCHIVED', 'Archived')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.UUIDField(null=True, blank=True)
    context = models.TextField(blank=True)
    last_interaction_at = models.DateTimeField(null=True, blank=True)
    session_status = models.CharField(max_length=20, choices=SESSION_STATUS, default='ACTIVE')
    conversation_history = JSONField(default=list, blank=True)
    feedback_score = models.FloatField(null=True, blank=True)


class AIPipeline(BaseEntity):
    STATUS = [('DRAFT', 'Draft'), ('ACTIVE', 'Active'), ('DISABLED', 'Disabled')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    job_order = JSONField(default=list, blank=True)  # ordered list of AIJob definitions (not instances)
    status = models.CharField(max_length=20, choices=STATUS, default='DRAFT')
    triggered_by = models.CharField(max_length=50, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)


class AIJobFeedback(BaseEntity):
    FEEDBACK_TYPES = [('POSITIVE', 'Positive'), ('NEGATIVE', 'Negative'), ('CORRECTION', 'Correction')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(AIJob, on_delete=models.CASCADE, related_name='feedbacks')
    user_id = models.UUIDField(null=True, blank=True)
    feedback_type = models.CharField(max_length=20, choices=FEEDBACK_TYPES)
    feedback_text = models.TextField(blank=True)
    processed = models.BooleanField(default=False)


class AISchedule(BaseEntity):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(AIJob, on_delete=models.CASCADE, related_name='schedules')
    cron_expression = models.CharField(max_length=120)
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    enabled = models.BooleanField(default=False)


class Organization(BaseEntity):
    PROFILE_TYPES = [
        ('HOSPITAL', 'Hospital'),
        ('CLINIC', 'Clinic'),
        ('LABORATORY', 'Laboratory'),
        ('PHARMACY', 'Pharmacy'),
        ('DIAGNOSTIC', 'Diagnostic Center'),
        ('WELLNESS', 'Wellness Center'),
    ]
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('ACTIVE', 'Active'),
        ('SUSPENDED', 'Suspended'),
        ('ARCHIVED', 'Archived'),
    ]
    tenant_id = models.UUIDField()
    name = models.CharField(max_length=255)
    profile_type = models.CharField(max_length=24, choices=PROFILE_TYPES)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default='DRAFT')
    metadata = JSONField(default=dict, blank=True)
    location_count = models.IntegerField(default=0)
    emergency_mode = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.name} ({self.profile_type})"


class Department(BaseEntity):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='departments')
    name = models.CharField(max_length=150)
    ward = models.BooleanField(default=False)
    clinical_scope = models.TextField(blank=True)
    services = JSONField(default=list, blank=True)

    class Meta:
        unique_together = (('organization', 'name'),)

    def __str__(self):
        return f"{self.organization.name} · {self.name}"


class StaffProfile(BaseEntity):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='staff')
    user_id = models.UUIDField()
    title = models.CharField(max_length=120)
    role = models.CharField(max_length=80)
    scope = JSONField(default=dict, blank=True)
    licenses = JSONField(default=list, blank=True)
    shifts = JSONField(default=list, blank=True)
    is_on_call = models.BooleanField(default=False)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"{self.title} ({self.role})"


class PatientMaster(BaseEntity):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='patients')
    patient_id = models.UUIDField()
    scope = JSONField(default=dict, blank=True)
    identifiers = JSONField(default=dict, blank=True)
    family_links = JSONField(default=list, blank=True)
    consent_records = JSONField(default=list, blank=True)

    def __str__(self):
        return f"Patient {self.patient_id}"


class EncounterRecord(BaseEntity):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='encounters')
    patient = models.ForeignKey(PatientMaster, on_delete=models.CASCADE, related_name='encounters')
    encounter_type = models.CharField(max_length=80)
    status = models.CharField(max_length=32, default='PLANNED')
    summary = models.TextField(blank=True)
    payload = JSONField(default=dict, blank=True)

    def __str__(self):
        return f"Encounter {self.patient.patient_id} ({self.encounter_type})"


class ConsentRecord(BaseEntity):
    patient = models.ForeignKey(PatientMaster, on_delete=models.CASCADE, related_name='consents')
    consent_type = models.CharField(max_length=60)
    status = models.CharField(max_length=32, default='GRANTED')
    issued_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    metadata = JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.patient.patient_id} · {self.consent_type}"


class GroqAIRequestLog(BaseEntity):
    user_id = models.UUIDField(null=True, blank=True)
    organization = models.ForeignKey(Organization, on_delete=models.SET_NULL, null=True, blank=True)
    prompt = models.TextField()
    response_summary = models.TextField(blank=True)
    model_name = models.CharField(max_length=120)
    mode = models.CharField(max_length=120, default='clinical-assistant')
    metadata = JSONField(default=dict, blank=True)
    risk_level = models.CharField(max_length=32, default='LOW')
    safe_to_proceed = models.BooleanField(default=True)

    def __str__(self):
        return f"GroqLog {self.id}"
