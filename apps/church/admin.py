from django.contrib import admin

from .models import (
    ChurchGiving,
    TithePledge,
    OfferingCampaign,
    ChurchMembership,
    AttendanceRecord,
    ChurchLifeEvent,
    MemberBirthdayAnniversary,
    SmallGroup,
    SmallGroupMembership,
    SmallGroupAttendance,
    DiscipleshipJourney,
    SpiritualGiftsAssessment,
    AccountabilityPartner,
    PrayerRequest,
    PrayerWallEntry,
    FastingRecord,
    PrayerChainSlot,
    Song,
    SetList,
    MinistryDepartment,
    VolunteerSignup,
    OutreachCampaign,
    EvangelismRecord,
)


@admin.register(OfferingCampaign)
class OfferingCampaignAdmin(admin.ModelAdmin):
    list_display = ["name", "type", "currency", "raised_amount", "target_amount", "is_active"]
    list_filter = ["type", "is_active", "currency"]
    search_fields = ["name"]


@admin.register(ChurchGiving)
class ChurchGivingAdmin(admin.ModelAdmin):
    list_display = ["user", "type", "amount", "currency", "is_anonymous", "created_at"]
    list_filter = ["type", "currency", "is_anonymous"]
    search_fields = ["user__email", "payment_ref"]
    raw_id_fields = ["user", "campaign"]


@admin.register(TithePledge)
class TithePledgeAdmin(admin.ModelAdmin):
    list_display = ["user", "amount", "currency", "period", "fulfilled_amount", "is_active"]
    list_filter = ["period", "is_active", "currency"]
    raw_id_fields = ["user"]


@admin.register(ChurchMembership)
class ChurchMembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "church_id", "tier", "status", "join_date"]
    list_filter = ["tier", "status"]
    search_fields = ["user__email"]
    raw_id_fields = ["user"]


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ["user", "church_id", "date", "type", "check_in_method"]
    list_filter = ["type", "check_in_method"]
    raw_id_fields = ["user"]


@admin.register(ChurchLifeEvent)
class ChurchLifeEventAdmin(admin.ModelAdmin):
    list_display = ["user", "type", "event_date", "officiant"]
    list_filter = ["type"]
    raw_id_fields = ["user"]


@admin.register(MemberBirthdayAnniversary)
class MemberBirthdayAnniversaryAdmin(admin.ModelAdmin):
    list_display = ["user", "type", "date", "notify_church"]
    list_filter = ["type", "notify_church"]
    raw_id_fields = ["user"]


@admin.register(SmallGroup)
class SmallGroupAdmin(admin.ModelAdmin):
    list_display = ["name", "church_id", "type", "district", "zone", "is_active"]
    list_filter = ["type", "is_active"]
    search_fields = ["name"]
    raw_id_fields = ["leader"]


@admin.register(SmallGroupMembership)
class SmallGroupMembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "group", "role", "join_date"]
    list_filter = ["role"]
    raw_id_fields = ["user", "group"]


@admin.register(SmallGroupAttendance)
class SmallGroupAttendanceAdmin(admin.ModelAdmin):
    list_display = ["group", "date"]
    raw_id_fields = ["group"]


@admin.register(DiscipleshipJourney)
class DiscipleshipJourneyAdmin(admin.ModelAdmin):
    list_display = ["user", "stage", "is_active", "salvation_date", "baptism_date"]
    list_filter = ["stage", "is_active"]
    raw_id_fields = ["user", "mentor"]


@admin.register(SpiritualGiftsAssessment)
class SpiritualGiftsAssessmentAdmin(admin.ModelAdmin):
    list_display = ["user", "completed_at"]
    raw_id_fields = ["user"]


@admin.register(AccountabilityPartner)
class AccountabilityPartnerAdmin(admin.ModelAdmin):
    list_display = ["user1", "user2", "focus_area", "is_active", "started_at"]
    list_filter = ["is_active"]
    raw_id_fields = ["user1", "user2"]


@admin.register(PrayerRequest)
class PrayerRequestAdmin(admin.ModelAdmin):
    list_display = ["user", "privacy", "is_answered", "prayer_count", "created_at"]
    list_filter = ["privacy", "is_answered"]
    raw_id_fields = ["user"]


@admin.register(PrayerWallEntry)
class PrayerWallEntryAdmin(admin.ModelAdmin):
    list_display = ["user", "is_public", "prayer_count", "church_id", "created_at"]
    list_filter = ["is_public"]
    raw_id_fields = ["user"]


@admin.register(FastingRecord)
class FastingRecordAdmin(admin.ModelAdmin):
    list_display = ["user", "type", "start_date", "end_date", "is_community", "is_active"]
    list_filter = ["type", "is_community", "is_active"]
    raw_id_fields = ["user"]


@admin.register(PrayerChainSlot)
class PrayerChainSlotAdmin(admin.ModelAdmin):
    list_display = ["user", "church_id", "start_time", "end_time", "is_filled"]
    list_filter = ["is_filled"]
    raw_id_fields = ["user"]


@admin.register(Song)
class SongAdmin(admin.ModelAdmin):
    list_display = ["title", "artist", "key", "tempo_bpm", "ccli_number"]
    search_fields = ["title", "artist", "ccli_number"]
    raw_id_fields = ["created_by"]


@admin.register(SetList)
class SetListAdmin(admin.ModelAdmin):
    list_display = ["title", "church_id", "service_date", "created_by"]
    raw_id_fields = ["created_by"]


@admin.register(MinistryDepartment)
class MinistryDepartmentAdmin(admin.ModelAdmin):
    list_display = ["name", "church_id", "type", "is_active"]
    list_filter = ["type", "is_active"]
    search_fields = ["name"]
    raw_id_fields = ["leader"]


@admin.register(VolunteerSignup)
class VolunteerSignupAdmin(admin.ModelAdmin):
    list_display = ["user", "role", "event_date", "status", "department"]
    list_filter = ["status"]
    raw_id_fields = ["user", "department"]


@admin.register(OutreachCampaign)
class OutreachCampaignAdmin(admin.ModelAdmin):
    list_display = ["title", "church_id", "start_date", "end_date", "current_count", "target_count"]
    search_fields = ["title"]
    raw_id_fields = ["created_by"]


@admin.register(EvangelismRecord)
class EvangelismRecordAdmin(admin.ModelAdmin):
    list_display = ["user", "date", "shares", "follow_ups", "salvations"]
    raw_id_fields = ["user"]
