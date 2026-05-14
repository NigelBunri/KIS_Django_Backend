import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from .constants import (
    PUBLIC_BADGE_LABELS,
    VerificationBadgeStatus,
    VerificationCaseStatus,
    VerificationCheckStatus,
    VerificationSubjectType,
)


def uuid4():
    return uuid.uuid4()


class TimeStampedUUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class VerificationSubject(TimeStampedUUIDModel):
    subject_type = models.CharField(max_length=40, choices=VerificationSubjectType.CHOICES, db_index=True)
    subject_id = models.UUIDField(db_index=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="verification_subjects",
    )
    display_name = models.CharField(max_length=255, blank=True)
    country = models.CharField(max_length=2, blank=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    current_level = models.CharField(max_length=64, blank=True, db_index=True)
    current_status = models.CharField(
        max_length=32,
        choices=VerificationCaseStatus.CHOICES,
        default=VerificationCaseStatus.DRAFT,
        db_index=True,
    )
    last_verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "verification_subject"
        constraints = [
            models.UniqueConstraint(fields=["subject_type", "subject_id"], name="verification_unique_subject"),
        ]
        indexes = [
            models.Index(fields=["subject_type", "subject_id"]),
            models.Index(fields=["owner", "subject_type"]),
            models.Index(fields=["current_status", "current_level"]),
        ]

    def __str__(self):
        name = self.display_name or str(self.subject_id)
        return f"{self.subject_type}:{name}"


class VerificationCase(TimeStampedUUIDModel):
    subject = models.ForeignKey(VerificationSubject, on_delete=models.CASCADE, related_name="cases")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="verification_cases_requested",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="verification_cases_reviewed",
    )
    level = models.CharField(max_length=64, db_index=True)
    status = models.CharField(
        max_length=32,
        choices=VerificationCaseStatus.CHOICES,
        default=VerificationCaseStatus.DRAFT,
        db_index=True,
    )
    provider = models.CharField(max_length=32, blank=True, db_index=True)
    provider_applicant_id = models.CharField(max_length=255, blank=True, db_index=True)
    provider_case_id = models.CharField(max_length=255, blank=True, db_index=True)
    provider_status = models.CharField(max_length=64, blank=True)
    risk_score = models.FloatField(null=True, blank=True)
    evidence_metadata = models.JSONField(default=dict, blank=True)
    provider_payload = models.JSONField(default=dict, blank=True)
    reviewer_notes = models.TextField(blank=True)
    public_summary = models.JSONField(default=dict, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "verification_case"
        indexes = [
            models.Index(fields=["subject", "status"]),
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["provider", "provider_case_id"]),
            models.Index(fields=["level", "status"]),
        ]

    def __str__(self):
        return f"{self.subject_id}:{self.level}:{self.status}"


class VerificationCheck(TimeStampedUUIDModel):
    case = models.ForeignKey(VerificationCase, on_delete=models.CASCADE, related_name="checks")
    check_type = models.CharField(max_length=64, db_index=True)
    status = models.CharField(
        max_length=32,
        choices=VerificationCheckStatus.CHOICES,
        default=VerificationCheckStatus.PENDING,
        db_index=True,
    )
    provider = models.CharField(max_length=32, blank=True, db_index=True)
    provider_check_id = models.CharField(max_length=255, blank=True, db_index=True)
    confidence = models.FloatField(null=True, blank=True)
    result_code = models.CharField(max_length=128, blank=True)
    result_summary = models.CharField(max_length=255, blank=True)
    evidence_metadata = models.JSONField(default=dict, blank=True)
    raw_result_metadata = models.JSONField(default=dict, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "verification_check"
        indexes = [
            models.Index(fields=["case", "check_type"]),
            models.Index(fields=["status", "check_type"]),
            models.Index(fields=["provider", "provider_check_id"]),
        ]

    def __str__(self):
        return f"{self.case_id}:{self.check_type}:{self.status}"


class VerificationBadge(TimeStampedUUIDModel):
    subject = models.ForeignKey(VerificationSubject, on_delete=models.CASCADE, related_name="badges")
    case = models.ForeignKey(
        VerificationCase,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="badges",
    )
    code = models.CharField(max_length=80, db_index=True)
    label = models.CharField(max_length=128, blank=True)
    level = models.CharField(max_length=64, blank=True, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=VerificationBadgeStatus.CHOICES,
        default=VerificationBadgeStatus.ACTIVE,
        db_index=True,
    )
    public = models.BooleanField(default=True, db_index=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="verification_badges_issued",
    )
    issued_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoke_reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "verification_badge"
        constraints = [
            models.UniqueConstraint(fields=["subject", "code"], name="verification_unique_subject_badge"),
        ]
        indexes = [
            models.Index(fields=["subject", "status", "public"]),
            models.Index(fields=["code", "status"]),
            models.Index(fields=["expires_at"]),
        ]

    def save(self, *args, **kwargs):
        if not self.label:
            self.label = PUBLIC_BADGE_LABELS.get(self.code, self.code.replace("_", " ").title())
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.subject_id}:{self.code}:{self.status}"


class VerificationAuditEvent(TimeStampedUUIDModel):
    subject = models.ForeignKey(
        VerificationSubject,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    case = models.ForeignKey(
        VerificationCase,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="verification_audit_events",
    )
    action = models.CharField(max_length=128, db_index=True)
    provider = models.CharField(max_length=32, blank=True, db_index=True)
    ip_address = models.CharField(max_length=45, blank=True)
    user_agent = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "verification_audit_event"
        indexes = [
            models.Index(fields=["subject", "created_at"]),
            models.Index(fields=["case", "created_at"]),
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["provider", "created_at"]),
        ]

    def __str__(self):
        return f"{self.action}:{self.created_at.isoformat()}"
