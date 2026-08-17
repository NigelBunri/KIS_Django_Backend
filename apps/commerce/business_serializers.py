"""
Serializers for Business & Economy models.
"""

from rest_framework import serializers

from .business_models import (
    CrowdfundCampaign,
    CrowdfundContribution,
    SavingsGroup,
    SavingsGroupMembership,
    JobListing,
    JobApplication,
    BusinessMentorship,
    CoWorkingSpace,
    KingdomCertification,
    ImpactReport,
)


# ---------------------------------------------------------------------------
# Crowdfunding
# ---------------------------------------------------------------------------

class CrowdfundContributionSerializer(serializers.ModelSerializer):
    contributor_display = serializers.SerializerMethodField()

    class Meta:
        model = CrowdfundContribution
        fields = [
            "id",
            "campaign",
            "contributor",
            "contributor_display",
            "amount",
            "is_anonymous",
            "message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "contributor", "created_at", "updated_at"]

    def get_contributor_display(self, obj):
        if obj.is_anonymous:
            return "Anonymous"
        if obj.contributor:
            return getattr(obj.contributor, "get_full_name", lambda: str(obj.contributor))()
        return None


class CrowdfundCampaignSerializer(serializers.ModelSerializer):
    progress_percent = serializers.SerializerMethodField()

    class Meta:
        model = CrowdfundCampaign
        fields = [
            "id",
            "creator",
            "title",
            "description",
            "target_amount",
            "raised_amount",
            "currency",
            "deadline",
            "status",
            "category",
            "image_url",
            "updates",
            "progress_percent",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "creator",
            "raised_amount",
            "status",
            "created_at",
            "updated_at",
        ]

    def get_progress_percent(self, obj):
        if obj.target_amount and obj.target_amount > 0:
            return round(float(obj.raised_amount) / float(obj.target_amount) * 100, 2)
        return 0.0


# ---------------------------------------------------------------------------
# Savings Groups
# ---------------------------------------------------------------------------

class SavingsGroupMembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = SavingsGroupMembership
        fields = [
            "id",
            "group",
            "user",
            "payout_position",
            "total_contributed",
            "has_received_payout",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "total_contributed", "created_at", "updated_at"]


class SavingsGroupSerializer(serializers.ModelSerializer):
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = SavingsGroup
        fields = [
            "id",
            "name",
            "creator",
            "type",
            "contribution_amount",
            "currency",
            "cycle_days",
            "max_members",
            "current_round",
            "is_active",
            "rules",
            "member_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "creator", "created_at", "updated_at"]

    def get_member_count(self, obj):
        return obj.memberships.count()


# ---------------------------------------------------------------------------
# Job Board
# ---------------------------------------------------------------------------

class JobListingSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobListing
        fields = [
            "id",
            "poster",
            "title",
            "description",
            "location",
            "remote_allowed",
            "job_type",
            "salary_min",
            "salary_max",
            "currency",
            "required_skills",
            "deadline",
            "is_active",
            "is_kingdom_certified",
            "application_count",
            "country",
            "industry",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "poster", "application_count", "created_at", "updated_at"]


class JobApplicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobApplication
        fields = [
            "id",
            "listing",
            "applicant",
            "cover_letter",
            "resume_url",
            "status",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "applicant", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# Business Mentorship
# ---------------------------------------------------------------------------

class BusinessMentorshipSerializer(serializers.ModelSerializer):
    class Meta:
        model = BusinessMentorship
        fields = [
            "id",
            "mentor",
            "mentee",
            "domain",
            "description",
            "status",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id", "mentor", "mentee", "started_at", "completed_at",
            "created_at", "updated_at",
        ]


# ---------------------------------------------------------------------------
# Co-Working Spaces
# ---------------------------------------------------------------------------

class CoWorkingSpaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = CoWorkingSpace
        fields = [
            "id",
            "name",
            "address",
            "city",
            "country",
            "description",
            "amenities",
            "price_per_day",
            "currency",
            "contact_email",
            "website",
            "cover_url",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# Kingdom Certification
# ---------------------------------------------------------------------------

class KingdomCertificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = KingdomCertification
        fields = [
            "id",
            "business_name",
            "owner",
            "type",
            "score",
            "issued_at",
            "valid_until",
            "audit_notes",
            "is_active",
            "badge_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "owner", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# Impact Report
# ---------------------------------------------------------------------------

class ImpactReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImpactReport
        fields = [
            "id",
            "org_id",
            "user",
            "title",
            "year",
            "content",
            "pdf_url",
            "is_public",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "created_at", "updated_at"]
