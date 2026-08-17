"""
Business & Economy models for the KIS platform.

Covers: Crowdfunding, Savings Groups (Susu/ROSCA), Job Board,
Business Mentorship, Co-Working Spaces, Kingdom Certifications,
and Impact Reports.
"""

import uuid

from django.db import models
from django.db.models import JSONField

from apps.health_ops.models import TimeStampedUUIDModel
from apps.accounts.models import User


# Must match CURRENCIES in KIS/src/screens/business/CreateCampaignScreen.tsx —
# these models track real-world fiat amounts (unlike the KIS-coin-only
# marketplace), so a fixed but broader currency list applies here.
SUPPORTED_FIAT_CURRENCIES = (
    "USD", "GBP", "EUR", "NGN", "GHS", "KES", "ZAR", "CAD", "AUD",
)
CURRENCY_CHOICES = [(code, code) for code in SUPPORTED_FIAT_CURRENCIES]


# ---------------------------------------------------------------------------
# Crowdfunding
# ---------------------------------------------------------------------------

class CrowdfundCampaign(TimeStampedUUIDModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        FUNDED = "funded", "Funded"
        CLOSED = "closed", "Closed"

    class Category(models.TextChoices):
        BUSINESS = "business", "Business"
        MINISTRY = "ministry", "Ministry"
        EDUCATION = "education", "Education"
        HEALTH = "health", "Health"
        COMMUNITY = "community", "Community"
        OTHER = "other", "Other"

    creator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="crowdfund_campaigns"
    )
    title = models.CharField(max_length=255)
    description = models.TextField()
    target_amount = models.DecimalField(max_digits=15, decimal_places=2)
    raised_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="USD")
    deadline = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.BUSINESS
    )
    image_url = models.URLField(blank=True)
    updates = JSONField(default=list)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Crowdfund Campaign"
        verbose_name_plural = "Crowdfund Campaigns"

    def __str__(self):
        return self.title


class CrowdfundContribution(TimeStampedUUIDModel):
    campaign = models.ForeignKey(
        CrowdfundCampaign,
        on_delete=models.CASCADE,
        related_name="contributions",
    )
    contributor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="crowdfund_contributions"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    is_anonymous = models.BooleanField(default=False)
    message = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Crowdfund Contribution"
        verbose_name_plural = "Crowdfund Contributions"

    def __str__(self):
        return f"{self.contributor_id} -> {self.campaign_id} ({self.amount})"


# ---------------------------------------------------------------------------
# Savings Groups (Susu / ROSCA)
# ---------------------------------------------------------------------------

class SavingsGroup(TimeStampedUUIDModel):
    class Type(models.TextChoices):
        SUSU = "susu", "Susu"
        ROSCA = "rosca", "ROSCA"
        INVESTMENT = "investment", "Investment"

    name = models.CharField(max_length=255)
    creator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="savings_groups_created"
    )
    type = models.CharField(
        max_length=20, choices=Type.choices, default=Type.SUSU
    )
    contribution_amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="USD")
    cycle_days = models.IntegerField(default=30)
    max_members = models.IntegerField(default=12)
    current_round = models.IntegerField(default=1)
    is_active = models.BooleanField(default=True)
    rules = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Savings Group"
        verbose_name_plural = "Savings Groups"

    def __str__(self):
        return self.name


class SavingsGroupMembership(TimeStampedUUIDModel):
    group = models.ForeignKey(
        SavingsGroup, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="savings_memberships"
    )
    payout_position = models.IntegerField()
    total_contributed = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    has_received_payout = models.BooleanField(default=False)

    class Meta:
        unique_together = [["group", "user"]]
        ordering = ["payout_position"]
        verbose_name = "Savings Group Membership"
        verbose_name_plural = "Savings Group Memberships"

    def __str__(self):
        return f"{self.user_id} in {self.group_id} (pos {self.payout_position})"


# ---------------------------------------------------------------------------
# Job Board
# ---------------------------------------------------------------------------

class JobListing(TimeStampedUUIDModel):
    class JobType(models.TextChoices):
        FULL_TIME = "full_time", "Full Time"
        PART_TIME = "part_time", "Part Time"
        CONTRACT = "contract", "Contract"
        INTERNSHIP = "internship", "Internship"
        VOLUNTEER = "volunteer", "Volunteer"

    poster = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="job_listings"
    )
    title = models.CharField(max_length=255)
    description = models.TextField()
    location = models.CharField(max_length=255, blank=True)
    remote_allowed = models.BooleanField(default=False)
    job_type = models.CharField(
        max_length=20, choices=JobType.choices, default=JobType.FULL_TIME
    )
    salary_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    salary_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="USD")
    required_skills = JSONField(default=list)
    deadline = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_kingdom_certified = models.BooleanField(default=False)
    application_count = models.IntegerField(default=0)
    country = models.CharField(max_length=100, blank=True)
    industry = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Job Listing"
        verbose_name_plural = "Job Listings"

    def __str__(self):
        return self.title


class JobApplication(TimeStampedUUIDModel):
    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        REVIEWING = "reviewing", "Reviewing"
        SHORTLISTED = "shortlisted", "Shortlisted"
        INTERVIEWED = "interviewed", "Interviewed"
        OFFERED = "offered", "Offered"
        REJECTED = "rejected", "Rejected"

    listing = models.ForeignKey(
        JobListing, on_delete=models.CASCADE, related_name="applications"
    )
    applicant = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="job_applications"
    )
    cover_letter = models.TextField(blank=True)
    resume_url = models.URLField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SUBMITTED
    )
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [["listing", "applicant"]]
        ordering = ["-created_at"]
        verbose_name = "Job Application"
        verbose_name_plural = "Job Applications"

    def __str__(self):
        return f"{self.applicant_id} -> {self.listing_id}"


# ---------------------------------------------------------------------------
# Business Mentorship
# ---------------------------------------------------------------------------

class BusinessMentorship(TimeStampedUUIDModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        MATCHED = "matched", "Matched"
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"

    mentor = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="biz_mentor"
    )
    mentee = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="biz_mentee",
    )
    domain = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Business Mentorship"
        verbose_name_plural = "Business Mentorships"

    def __str__(self):
        return f"Mentorship: {self.mentor_id} ({self.domain}) [{self.status}]"


# ---------------------------------------------------------------------------
# Co-Working Spaces
# ---------------------------------------------------------------------------

class CoWorkingSpace(TimeStampedUUIDModel):
    name = models.CharField(max_length=255)
    address = models.TextField()
    city = models.CharField(max_length=100)
    country = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    amenities = JSONField(default=list)
    price_per_day = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="USD")
    contact_email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    cover_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["country", "city", "name"]
        verbose_name = "Co-Working Space"
        verbose_name_plural = "Co-Working Spaces"

    def __str__(self):
        return f"{self.name} ({self.city}, {self.country})"


# ---------------------------------------------------------------------------
# Kingdom Certification
# ---------------------------------------------------------------------------

class KingdomCertification(TimeStampedUUIDModel):
    class Type(models.TextChoices):
        ETHICAL = "ethical", "Ethical"
        FAIR_TRADE = "fair_trade", "Fair Trade"
        CHRISTIAN_OWNED = "christian_owned", "Christian Owned"
        SOCIAL_ENTERPRISE = "social_enterprise", "Social Enterprise"

    business_name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="kingdom_certifications"
    )
    type = models.CharField(
        max_length=30, choices=Type.choices, default=Type.ETHICAL
    )
    score = models.IntegerField(null=True, blank=True)  # 0-100
    issued_at = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    audit_notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    badge_url = models.URLField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Kingdom Certification"
        verbose_name_plural = "Kingdom Certifications"

    def __str__(self):
        return f"{self.business_name} [{self.type}]"


# ---------------------------------------------------------------------------
# Impact Report
# ---------------------------------------------------------------------------

class ImpactReport(TimeStampedUUIDModel):
    org_id = models.UUIDField()
    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="impact_reports"
    )
    title = models.CharField(max_length=255)
    year = models.IntegerField()
    content = JSONField(default=dict)  # structured impact data
    pdf_url = models.URLField(blank=True)
    is_public = models.BooleanField(default=False)

    class Meta:
        ordering = ["-year", "-created_at"]
        verbose_name = "Impact Report"
        verbose_name_plural = "Impact Reports"

    def __str__(self):
        return f"{self.title} ({self.year})"
