from django.contrib import admin

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


@admin.register(Petition)
class PetitionAdmin(admin.ModelAdmin):
    list_display = ["title", "creator", "status", "category", "country", "signatures_count", "created_at"]
    list_filter = ["status", "category", "country"]
    search_fields = ["title", "description", "target"]
    readonly_fields = ["id", "signatures_count", "created_at", "updated_at"]


@admin.register(PetitionSignature)
class PetitionSignatureAdmin(admin.ModelAdmin):
    list_display = ["petition", "user", "is_anonymous", "created_at"]
    list_filter = ["is_anonymous"]
    search_fields = ["petition__title", "user__email"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(CivicPoll)
class CivicPollAdmin(admin.ModelAdmin):
    list_display = ["question", "creator", "scope", "is_public", "start_date", "end_date"]
    list_filter = ["scope", "is_public"]
    search_fields = ["question"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(PollVote)
class PollVoteAdmin(admin.ModelAdmin):
    list_display = ["poll", "user", "option_key", "created_at"]
    search_fields = ["poll__question", "user__email"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(LegalAidEntry)
class LegalAidEntryAdmin(admin.ModelAdmin):
    list_display = ["name", "specialty", "country", "state", "is_pro_bono", "is_active"]
    list_filter = ["country", "is_pro_bono", "is_active"]
    search_fields = ["name", "specialty", "description"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(LegalDocumentTemplate)
class LegalDocumentTemplateAdmin(admin.ModelAdmin):
    list_display = ["title", "type", "country", "is_public", "created_at"]
    list_filter = ["type", "country", "is_public"]
    search_fields = ["title", "description"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(DiasporaCommunity)
class DiasporaCommunityAdmin(admin.ModelAdmin):
    list_display = ["name", "country_of_origin", "host_country", "member_count", "is_active"]
    list_filter = ["country_of_origin", "host_country", "is_active"]
    search_fields = ["name", "description"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(DiasporaCommunityMembership)
class DiasporaCommunityMembershipAdmin(admin.ModelAdmin):
    list_display = ["community", "user", "join_date"]
    search_fields = ["community__name", "user__email"]
    readonly_fields = ["id", "join_date"]


@admin.register(NGOProfile)
class NGOProfileAdmin(admin.ModelAdmin):
    list_display = ["name", "country", "is_verified", "owner", "created_at"]
    list_filter = ["country", "is_verified"]
    search_fields = ["name", "description", "reg_number"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(GrantApplication)
class GrantApplicationAdmin(admin.ModelAdmin):
    list_display = ["title", "user", "ngo", "amount_requested", "currency", "status", "submission_date"]
    list_filter = ["status", "currency"]
    search_fields = ["title", "description", "grant_source"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(ComplianceDeadline)
class ComplianceDeadlineAdmin(admin.ModelAdmin):
    list_display = ["title", "user", "type", "due_date", "status", "reminder_sent"]
    list_filter = ["type", "status", "reminder_sent"]
    search_fields = ["title", "notes"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(WhistleblowerReport)
class WhistleblowerReportAdmin(admin.ModelAdmin):
    list_display = ["case_ref", "category", "status", "country", "submitted_at"]
    list_filter = ["status", "category", "country"]
    search_fields = ["case_ref"]
    readonly_fields = ["id", "case_ref", "submitted_at"]


@admin.register(BoardElection)
class BoardElectionAdmin(admin.ModelAdmin):
    list_display = ["title", "org_id", "created_by", "start_date", "end_date", "is_closed"]
    list_filter = ["is_closed"]
    search_fields = ["title"]
    readonly_fields = ["id", "results", "created_at", "updated_at"]


@admin.register(ElectionVote)
class ElectionVoteAdmin(admin.ModelAdmin):
    list_display = ["election", "user", "candidate_index", "created_at"]
    search_fields = ["election__title", "user__email"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(BoardResolution)
class BoardResolutionAdmin(admin.ModelAdmin):
    list_display = ["title", "org_id", "created_by", "meeting_date", "passed"]
    list_filter = ["passed", "meeting_date"]
    search_fields = ["title", "content"]
    readonly_fields = ["id", "created_at", "updated_at"]
