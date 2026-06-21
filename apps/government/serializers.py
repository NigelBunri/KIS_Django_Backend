from rest_framework import serializers

from .models import (
    Petition,
    PetitionSignature,
    CivicPoll,
    PollVote,
    LegalAidEntry,
    LegalDocumentTemplate,
    DiasporaCommunity,
    DiasporaCommunityMembership,
    NGOProfile,
    GrantApplication,
    ComplianceDeadline,
    WhistleblowerReport,
    BoardElection,
    ElectionVote,
    BoardResolution,
)


class PetitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Petition
        fields = [
            "id",
            "creator",
            "title",
            "description",
            "target",
            "target_count",
            "signatures_count",
            "deadline",
            "status",
            "category",
            "country",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "creator", "signatures_count", "created_at", "updated_at"]


class PetitionSignatureSerializer(serializers.ModelSerializer):
    class Meta:
        model = PetitionSignature
        fields = [
            "id",
            "petition",
            "user",
            "is_anonymous",
            "comment",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "petition", "user", "created_at", "updated_at"]


class CivicPollSerializer(serializers.ModelSerializer):
    class Meta:
        model = CivicPoll
        fields = [
            "id",
            "creator",
            "question",
            "options",
            "scope",
            "start_date",
            "end_date",
            "is_public",
            "church_id",
            "org_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "creator", "created_at", "updated_at"]


class PollVoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = PollVote
        fields = [
            "id",
            "poll",
            "user",
            "option_key",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "poll", "user", "created_at", "updated_at"]


class LegalAidEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = LegalAidEntry
        fields = [
            "id",
            "name",
            "specialty",
            "country",
            "state",
            "city",
            "contact_phone",
            "contact_email",
            "website",
            "description",
            "is_pro_bono",
            "languages",
            "is_active",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "created_at", "updated_at"]


class LegalDocumentTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = LegalDocumentTemplate
        fields = [
            "id",
            "title",
            "type",
            "country",
            "content_markdown",
            "description",
            "is_public",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "created_at", "updated_at"]


class DiasporaCommunitySerializer(serializers.ModelSerializer):
    class Meta:
        model = DiasporaCommunity
        fields = [
            "id",
            "name",
            "country_of_origin",
            "host_country",
            "description",
            "founder",
            "member_count",
            "is_active",
            "banner_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "founder", "member_count", "created_at", "updated_at"]


class DiasporaCommunityMembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiasporaCommunityMembership
        fields = [
            "id",
            "community",
            "user",
            "join_date",
        ]
        read_only_fields = ["id", "community", "user", "join_date"]


class NGOProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = NGOProfile
        fields = [
            "id",
            "name",
            "reg_number",
            "country",
            "focus_areas",
            "description",
            "contact_email",
            "website",
            "is_verified",
            "owner",
            "annual_budget",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "owner", "is_verified", "created_at", "updated_at"]


class GrantApplicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = GrantApplication
        fields = [
            "id",
            "ngo",
            "user",
            "title",
            "description",
            "amount_requested",
            "currency",
            "status",
            "grant_source",
            "submission_date",
            "supporting_docs",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "created_at", "updated_at"]


class ComplianceDeadlineSerializer(serializers.ModelSerializer):
    class Meta:
        model = ComplianceDeadline
        fields = [
            "id",
            "org_id",
            "user",
            "title",
            "type",
            "due_date",
            "status",
            "notes",
            "reminder_sent",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "created_at", "updated_at"]


class WhistleblowerReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = WhistleblowerReport
        fields = [
            "id",
            "encrypted_content",
            "submitted_at",
            "status",
            "category",
            "case_ref",
            "country",
        ]
        read_only_fields = ["id", "submitted_at", "status", "case_ref"]


class WhistleblowerSubmitSerializer(serializers.Serializer):
    content = serializers.CharField()
    category = serializers.ChoiceField(choices=WhistleblowerReport.category.field.choices)
    country = serializers.CharField(required=False, allow_blank=True, default="")


class WhistleblowerStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = WhistleblowerReport
        fields = ["case_ref", "status", "submitted_at", "category"]
        read_only_fields = ["case_ref", "status", "submitted_at", "category"]


class BoardElectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = BoardElection
        fields = [
            "id",
            "org_id",
            "created_by",
            "title",
            "candidates",
            "start_date",
            "end_date",
            "results",
            "is_closed",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "results", "is_closed", "created_at", "updated_at"]


class ElectionVoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ElectionVote
        fields = [
            "id",
            "election",
            "user",
            "candidate_index",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "election", "user", "created_at", "updated_at"]


class BoardResolutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = BoardResolution
        fields = [
            "id",
            "org_id",
            "created_by",
            "title",
            "content",
            "meeting_date",
            "passed",
            "vote_results",
            "tags",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "created_at", "updated_at"]
