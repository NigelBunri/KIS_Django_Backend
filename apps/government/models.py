import uuid
from django.db import models
from django.utils import timezone

from apps.accounts.models import User


class TimeStampedUUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# ---------------------------------------------------------------------------
# Petitions
# ---------------------------------------------------------------------------

class PetitionStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    CLOSED = "closed", "Closed"
    WON = "won", "Won"


class PetitionCategory(models.TextChoices):
    CIVIC = "civic", "Civic"
    FAITH = "faith", "Faith"
    BUSINESS = "business", "Business"
    EDUCATION = "education", "Education"
    HEALTH = "health", "Health"
    GENERAL = "general", "General"


class Petition(TimeStampedUUIDModel):
    creator = models.ForeignKey(User, on_delete=models.CASCADE, related_name="petitions")
    title = models.CharField(max_length=255)
    description = models.TextField()
    target = models.CharField(max_length=255)
    target_count = models.IntegerField(default=1000)
    signatures_count = models.IntegerField(default=0)
    deadline = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=PetitionStatus.choices,
        default=PetitionStatus.ACTIVE,
    )
    category = models.CharField(
        max_length=20,
        choices=PetitionCategory.choices,
        default=PetitionCategory.GENERAL,
    )
    country = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class PetitionSignature(TimeStampedUUIDModel):
    petition = models.ForeignKey(Petition, on_delete=models.CASCADE, related_name="signatures")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="petition_signatures")
    is_anonymous = models.BooleanField(default=False)
    comment = models.TextField(blank=True)

    class Meta:
        unique_together = [["petition", "user"]]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} signed {self.petition}"


# ---------------------------------------------------------------------------
# Civic Polls
# ---------------------------------------------------------------------------

class PollScope(models.TextChoices):
    NEIGHBORHOOD = "neighborhood", "Neighborhood"
    CHURCH = "church", "Church"
    ORGANIZATION = "organization", "Organization"
    REGIONAL = "regional", "Regional"
    NATIONAL = "national", "National"


class CivicPoll(TimeStampedUUIDModel):
    creator = models.ForeignKey(User, on_delete=models.CASCADE, related_name="civic_polls")
    question = models.TextField()
    options = models.JSONField(default=list)  # [{"key": "a", "label": "Yes"}, ...]
    scope = models.CharField(
        max_length=20,
        choices=PollScope.choices,
        default=PollScope.ORGANIZATION,
    )
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    is_public = models.BooleanField(default=True)
    church_id = models.UUIDField(null=True, blank=True)
    org_id = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.question[:80]


class PollVote(TimeStampedUUIDModel):
    poll = models.ForeignKey(CivicPoll, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="poll_votes")
    option_key = models.CharField(max_length=50)

    class Meta:
        unique_together = [["poll", "user"]]

    def __str__(self):
        return f"{self.user} voted {self.option_key} on {self.poll}"


# ---------------------------------------------------------------------------
# Legal Aid
# ---------------------------------------------------------------------------

class LegalAidEntry(TimeStampedUUIDModel):
    name = models.CharField(max_length=255)
    specialty = models.CharField(max_length=255)
    country = models.CharField(max_length=100)
    state = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    contact_phone = models.CharField(max_length=50, blank=True)
    contact_email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    description = models.TextField(blank=True)
    is_pro_bono = models.BooleanField(default=False)
    languages = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="legal_aid_entries"
    )

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Legal Aid Entries"

    def __str__(self):
        return f"{self.name} ({self.country})"


class LegalDocumentTemplate(TimeStampedUUIDModel):
    title = models.CharField(max_length=255)
    type = models.CharField(max_length=100)
    country = models.CharField(max_length=100, blank=True)
    content_markdown = models.TextField()
    description = models.TextField(blank=True)
    is_public = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="legal_doc_templates"
    )

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title


# ---------------------------------------------------------------------------
# Diaspora
# ---------------------------------------------------------------------------

class DiasporaCommunity(TimeStampedUUIDModel):
    name = models.CharField(max_length=255)
    country_of_origin = models.CharField(max_length=100)
    host_country = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    founder = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="founded_diaspora_communities"
    )
    member_count = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    banner_url = models.URLField(blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Diaspora Communities"

    def __str__(self):
        return f"{self.name} ({self.country_of_origin} in {self.host_country})"


class DiasporaCommunityMembership(TimeStampedUUIDModel):
    community = models.ForeignKey(
        DiasporaCommunity, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="diaspora_memberships")
    join_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [["community", "user"]]

    def __str__(self):
        return f"{self.user} in {self.community}"


# ---------------------------------------------------------------------------
# NGO & Grants
# ---------------------------------------------------------------------------

class NGOProfile(TimeStampedUUIDModel):
    name = models.CharField(max_length=255)
    reg_number = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100)
    focus_areas = models.JSONField(default=list)
    description = models.TextField(blank=True)
    contact_email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    is_verified = models.BooleanField(default=False)
    owner = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="ngo_profiles"
    )
    annual_budget = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class GrantApplicationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    UNDER_REVIEW = "under_review", "Under Review"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class GrantApplication(TimeStampedUUIDModel):
    ngo = models.ForeignKey(
        NGOProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name="grant_applications"
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="grant_applications")
    title = models.CharField(max_length=255)
    description = models.TextField()
    amount_requested = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(
        max_length=20,
        choices=GrantApplicationStatus.choices,
        default=GrantApplicationStatus.DRAFT,
    )
    grant_source = models.CharField(max_length=255, blank=True)
    submission_date = models.DateField(null=True, blank=True)
    supporting_docs = models.JSONField(default=list)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


# ---------------------------------------------------------------------------
# Compliance
# ---------------------------------------------------------------------------

class ComplianceType(models.TextChoices):
    FILING = "filing", "Filing"
    RENEWAL = "renewal", "Renewal"
    REPORT = "report", "Report"
    LICENSE = "license", "License"
    TAX = "tax", "Tax"


class ComplianceDeadlineStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    COMPLETE = "complete", "Complete"
    OVERDUE = "overdue", "Overdue"


class ComplianceDeadline(TimeStampedUUIDModel):
    org_id = models.UUIDField()
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="compliance_deadlines")
    title = models.CharField(max_length=255)
    type = models.CharField(
        max_length=20,
        choices=ComplianceType.choices,
        default=ComplianceType.FILING,
    )
    due_date = models.DateField()
    status = models.CharField(
        max_length=20,
        choices=ComplianceDeadlineStatus.choices,
        default=ComplianceDeadlineStatus.PENDING,
    )
    notes = models.TextField(blank=True)
    reminder_sent = models.BooleanField(default=False)

    class Meta:
        ordering = ["due_date"]

    def __str__(self):
        return f"{self.title} (due {self.due_date})"


# ---------------------------------------------------------------------------
# Whistleblower
# ---------------------------------------------------------------------------

class WhistleblowerStatus(models.TextChoices):
    RECEIVED = "received", "Received"
    REVIEWING = "reviewing", "Reviewing"
    RESOLVED = "resolved", "Resolved"


class WhistleblowerCategory(models.TextChoices):
    CORRUPTION = "corruption", "Corruption"
    FRAUD = "fraud", "Fraud"
    ABUSE = "abuse", "Abuse"
    SAFETY = "safety", "Safety"
    OTHER = "other", "Other"


class WhistleblowerReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    encrypted_content = models.TextField()
    submitted_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=20,
        choices=WhistleblowerStatus.choices,
        default=WhistleblowerStatus.RECEIVED,
    )
    category = models.CharField(max_length=20, choices=WhistleblowerCategory.choices)
    case_ref = models.CharField(max_length=16, unique=True)
    country = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"Report {self.case_ref}"


# ---------------------------------------------------------------------------
# Board Elections
# ---------------------------------------------------------------------------

class BoardElection(TimeStampedUUIDModel):
    org_id = models.UUIDField()
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="board_elections"
    )
    title = models.CharField(max_length=255)
    candidates = models.JSONField(default=list)  # [{"name": "...", "bio": "..."}]
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    results = models.JSONField(default=dict)
    is_closed = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class ElectionVote(TimeStampedUUIDModel):
    election = models.ForeignKey(BoardElection, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="election_votes")
    candidate_index = models.IntegerField()

    class Meta:
        unique_together = [["election", "user"]]

    def __str__(self):
        return f"{self.user} voted in {self.election}"


class BoardResolution(TimeStampedUUIDModel):
    org_id = models.UUIDField()
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="board_resolutions"
    )
    title = models.CharField(max_length=255)
    content = models.TextField()
    meeting_date = models.DateField()
    passed = models.BooleanField(null=True)
    vote_results = models.JSONField(default=dict)
    tags = models.JSONField(default=list)

    class Meta:
        ordering = ["-meeting_date"]

    def __str__(self):
        return self.title
