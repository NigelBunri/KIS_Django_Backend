from django.contrib import admin

from .models import (
    Medium,
    Service,
    BroadcastHealthProfile,
    BroadcastHealthInstitution,
    BroadcastHealthInstitutionMember,
    BroadcastHealthInstitutionService,
    BroadcastItem,
)

@admin.register(Medium)
class HealthMediumAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'description', 'created_at')
    search_fields = ('name',)

@admin.register(Service)
class HealthServiceAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'description', 'created_at')
    search_fields = ('name',)

@admin.register(BroadcastHealthProfile)
class BroadcastHealthProfileAdmin(admin.ModelAdmin):
    list_display = ('id', 'profile', 'payload', 'created_at', 'updated_at')
    search_fields = ('id',)


@admin.register(BroadcastHealthInstitution)
class BroadcastHealthInstitutionAdmin(admin.ModelAdmin):
    list_display = ('id', 'health_profile', 'institution_uid', 'name', 'institution_type', 'updated_at')
    search_fields = ('institution_uid', 'name')


@admin.register(BroadcastHealthInstitutionMember)
class BroadcastHealthInstitutionMemberAdmin(admin.ModelAdmin):
    list_display = ('id', 'institution', 'member_uid', 'name', 'role', 'updated_at')
    search_fields = ('member_uid', 'name', 'phone', 'email')


@admin.register(BroadcastHealthInstitutionService)
class BroadcastHealthInstitutionServiceAdmin(admin.ModelAdmin):
    list_display = ('id', 'institution', 'service_uid', 'name', 'active', 'updated_at')
    search_fields = ('service_uid', 'name')


@admin.register(BroadcastItem)
class BroadcastItemAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'source_type',
        'source_id',
        'broadcasted_by',
        'broadcasted_at',
        'expires_at',
        'is_deleted',
    )
    list_filter = ('source_type', 'is_deleted')
    search_fields = ('source_id', 'metadata')
    readonly_fields = ('created_at', 'updated_at')
