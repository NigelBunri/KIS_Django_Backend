import uuid

from django.db import models
from django.db.models import JSONField
from django.utils import timezone

from apps.accounts.models import User
from apps.health_ops.models import TimeStampedUUIDModel


class ClassroomStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    LIVE = "live", "Live"
    ENDED = "ended", "Ended"
    CANCELLED = "cancelled", "Cancelled"


class SubmissionStatus(models.TextChoices):
    SUBMITTED = "submitted", "Submitted"
    GRADED = "graded", "Graded"
    RETURNED = "returned", "Returned"


class ScholarshipApplicationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SHORTLISTED = "shortlisted", "Shortlisted"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class LiveClassroom(TimeStampedUUIDModel):
    channel_id = models.CharField(max_length=100, db_index=True)
    title = models.CharField(max_length=255)
    host = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="hosted_classrooms",
    )
    scheduled_at = models.DateTimeField()
    status = models.CharField(
        max_length=20,
        choices=ClassroomStatus.choices,
        default=ClassroomStatus.SCHEDULED,
        db_index=True,
    )
    meeting_url = models.URLField(blank=True)
    recording_url = models.URLField(blank=True)
    whiteboard_url = models.URLField(blank=True)
    max_participants = models.IntegerField(null=True, blank=True)
    metadata = JSONField(default=dict)

    class Meta:
        ordering = ["-scheduled_at"]

    def __str__(self):
        return f"{self.title} ({self.status})"


class ClassAssignment(TimeStampedUUIDModel):
    classroom = models.ForeignKey(
        LiveClassroom,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    due_date = models.DateTimeField(null=True, blank=True)
    max_score = models.FloatField(default=100)
    rubric = JSONField(default=dict)
    file_urls = JSONField(default=list)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class AssignmentSubmission(TimeStampedUUIDModel):
    assignment = models.ForeignKey(
        ClassAssignment,
        on_delete=models.CASCADE,
        related_name="submissions",
    )
    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="education_assignment_submissions",
    )
    file_urls = JSONField(default=list)
    text_response = models.TextField(blank=True)
    score = models.FloatField(null=True, blank=True)
    feedback = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=SubmissionStatus.choices,
        default=SubmissionStatus.SUBMITTED,
        db_index=True,
    )
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [["assignment", "student"]]
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.student} – {self.assignment}"


class Scholarship(TimeStampedUUIDModel):
    title = models.CharField(max_length=255)
    sponsor = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    deadline = models.DateField(null=True, blank=True)
    requirements = models.TextField(blank=True)
    application_url = models.URLField(blank=True)
    country = models.CharField(max_length=100, blank=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_scholarships",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class ScholarshipApplication(TimeStampedUUIDModel):
    scholarship = models.ForeignKey(
        Scholarship,
        on_delete=models.CASCADE,
        related_name="applications",
    )
    applicant = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="scholarship_applications",
    )
    statement = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=ScholarshipApplicationStatus.choices,
        default=ScholarshipApplicationStatus.PENDING,
        db_index=True,
    )
    documents = JSONField(default=list)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.applicant} – {self.scholarship}"


class DigitalBadge(TimeStampedUUIDModel):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    criteria = models.TextField(blank=True)
    image_url = models.URLField(blank=True)
    issuer = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class StudentBadge(TimeStampedUUIDModel):
    badge = models.ForeignKey(
        DigitalBadge,
        on_delete=models.CASCADE,
        related_name="student_badges",
    )
    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="earned_badges",
    )
    issued_at = models.DateTimeField(auto_now_add=True)
    verif_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        unique_together = [["badge", "student"]]
        ordering = ["-issued_at"]

    def __str__(self):
        return f"{self.student} – {self.badge}"


class StudentProgressReport(TimeStampedUUIDModel):
    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="progress_reports",
    )
    classroom = models.ForeignKey(
        LiveClassroom,
        on_delete=models.CASCADE,
        related_name="progress_reports",
    )
    attendance_count = models.IntegerField(default=0)
    assignments_submitted = models.IntegerField(default=0)
    average_score = models.FloatField(null=True, blank=True)
    last_active = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.student} – {self.classroom}"
