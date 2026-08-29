from django.contrib import admin
import json
from django.utils.html import format_html
from django.urls import reverse
from django.utils import timezone

from .models import (
    ProfileFieldVisibility,
    User,
    Profile,
    AccountTier,
    Subscription,
    Session,
    Device,
    RestoreCredential,
    UsageQuota,
    AuditLog,
    ApiToken,
    Experience,
    Education,
    UserSkill,
    Project,
    Recommendation,
    TwoFactor,
    BillingAccount,
    OrganizationLink,
    FeatureFlag,
    AIAccess,
    RevenueAccount,
    GDPRRequest,
)


class HasHealthProfileFilter(admin.SimpleListFilter):
    title = 'has health profile'
    parameter_name = 'has_health_profile'

    def lookups(self, request, model_admin):
        return (
            ('yes', 'Yes'),
            ('no', 'No'),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value not in {'yes', 'no'}:
            return queryset

        matched_ids = []
        for user in queryset:
            prefs = getattr(user, 'preferences', {}) or {}
            profiles = prefs.get('profiles', {}) if isinstance(prefs, dict) else {}
            health = profiles.get('health') if isinstance(profiles, dict) else None
            institutions = []
            if isinstance(health, dict):
                institutions = health.get('institutions') or []
            has_health = bool(isinstance(institutions, list) and len(institutions) > 0)
            if (value == 'yes' and has_health) or (value == 'no' and not has_health):
                matched_ids.append(user.id)
        return queryset.filter(id__in=matched_ids)


# -------------------------
# Inline helpers
# -------------------------
class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = 'profile'
    fk_name = 'user'
    readonly_fields = ('created_at', 'updated_at', 'completion_score')

class ApiTokenInline(admin.TabularInline):
    model = ApiToken
    fields = ('name', 'expires_at', 'last_used_at', 'last_used_ip', 'is_deleted')
    readonly_fields = ('last_used_at', 'last_used_ip')
    extra = 0
    show_change_link = True

@admin.register(ProfileFieldVisibility)
class ProfileFieldVisibilityAdmin(admin.ModelAdmin):
    fields = ('field_key', 'visibility', 'created_at')
    search_fields = ('field_key',)
    list_filter = ('visibility',)
    readonly_fields = ('created_at', 'updated_at')

# -------------------------
# User admin
# -------------------------
@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('id', 'email','phone', 'display_name', 'username', 'tier', 'health_institutions_count', 'health_profile_updated_at', 'email_verified', 'is_staff', 'created_at')
    search_fields = ('email', 'display_name', 'username', 'phone')
    readonly_fields = ('created_at', 'updated_at', 'last_login_at', 'last_password_change_at', 'health_profile_updated_at', 'health_institutions_count', 'health_institutions_json')
    list_filter = ('tier', 'status', 'is_staff', 'email_verified', HasHealthProfileFilter)
    inlines = (ProfileInline, ApiTokenInline)
    ordering = ('-created_at',)

    actions = ['deactivate_users', 'export_user_ids']

    def _health_profile_data(self, obj):
        prefs = getattr(obj, 'preferences', {}) or {}
        profiles = prefs.get('profiles', {}) if isinstance(prefs, dict) else {}
        health = profiles.get('health') if isinstance(profiles, dict) else None
        return health if isinstance(health, dict) else {}

    def health_institutions_count(self, obj):
        health = self._health_profile_data(obj)
        institutions = health.get('institutions') if isinstance(health, dict) else []
        return len(institutions) if isinstance(institutions, list) else 0
    health_institutions_count.short_description = 'health institutions'

    def health_profile_updated_at(self, obj):
        health = self._health_profile_data(obj)
        return health.get('updated_at') if isinstance(health, dict) else None
    health_profile_updated_at.short_description = 'health profile updated at'

    def health_institutions_json(self, obj):
        health = self._health_profile_data(obj)
        institutions = health.get('institutions') if isinstance(health, dict) else []
        if not isinstance(institutions, list):
            institutions = []
        return json.dumps(institutions, indent=2, ensure_ascii=False)
    health_institutions_json.short_description = 'health institutions json'


    def deactivate_users(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"Deactivated {updated} users")
    deactivate_users.short_description = 'Deactivate selected users'

    def export_user_ids(self, request, queryset):
        ids = ",".join(str(u.id) for u in queryset)
        self.message_user(request, f"IDs: {ids}")
    export_user_ids.short_description = 'Copy selected user IDs'

# -------------------------
# Profile admin
# -------------------------
@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user_email', 'headline', 'completion_score', 'visibility', 'created_at')
    search_fields = ('user__email', 'headline', 'bio')
    readonly_fields = ('created_at', 'updated_at')

    def user_email(self, obj):
        return obj.user.email
    user_email.short_description = 'user'

# -------------------------
# ApiToken admin
# -------------------------
@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin):
    list_display = ('id', 'user_email', 'name', 'expires_at', 'last_used_at', 'last_used_ip', 'is_deleted')
    search_fields = ('user__email', 'user__phone', 'name')
    readonly_fields = ('token_hash', 'created_at', 'updated_at', 'last_used_at', 'last_used_ip')
    list_filter = ('is_deleted', 'created_via')
    actions = ['revoke_tokens']

    def user_email(self, obj):
        return obj.user.email if obj.user else None

    def revoke_tokens(self, request, queryset):
        for t in queryset:
            try:
                t.revoke()
            except Exception:
                pass
        self.message_user(request, f"Revoked {queryset.count()} tokens")
    revoke_tokens.short_description = 'Revoke selected tokens'

# -------------------------
# Simple model registrations
# -------------------------
@admin.register(AccountTier)
class AccountTierAdmin(admin.ModelAdmin):
    list_display = ('name', 'price_cents')
    readonly_fields = ('created_at', 'updated_at')

@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user_email', 'tier', 'status', 'started_at', 'ends_at')
    search_fields = ('user__email', 'tier__name')
    list_filter = ('status',)

    def user_email(self, obj):
        return obj.user.email if obj.user else None

@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ('user_email', 'ip_address', 'expires_at', 'user_agent')
    search_fields = ('user__email', 'ip_address')
    readonly_fields = ('created_at', 'updated_at')

    def user_email(self, obj):
        return obj.user.email if obj.user else None


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = (
        'user_email',
        'device_id',
        'platform',
        'name',
        'last_ip',
        'last_seen_at',
        'token_version',
        'revoked_at',
        'revoke_reason',
    )
    search_fields = ('user__email', 'user__phone', 'device_id', 'name', 'last_ip')
    list_filter = ('platform', 'revoked_at')
    readonly_fields = (
        'created_at',
        'updated_at',
        'last_seen_at',
        'last_ip',
        'user_agent',
        'token_version',
        'revoked_at',
        'revoke_reason',
    )
    actions = ['revoke_devices']

    def user_email(self, obj):
        return obj.user.email if obj.user else None

    def revoke_devices(self, request, queryset):
        now = timezone.now()
        updated = 0
        for device in queryset.filter(revoked_at__isnull=True):
            device.token_version = int(device.token_version or 1) + 1
            device.revoked_at = now
            device.revoke_reason = 'admin_revoke'
            device.save(update_fields=['token_version', 'revoked_at', 'revoke_reason', 'updated_at'])
            AuditLog.log(
                actor=getattr(device, 'user', None),
                action='security.device.revoked',
                meta={'device_id': device.device_id, 'reason': 'admin_revoke', 'admin_id': str(request.user.id)},
            )
            updated += 1
        self.message_user(request, f"Revoked {updated} devices")
    revoke_devices.short_description = 'Revoke selected devices'


@admin.register(RestoreCredential)
class RestoreCredentialAdmin(admin.ModelAdmin):
    list_display = (
        'user_email',
        'credential_id',
        'origin_device_id',
        'sign_count',
        'last_used_at',
        'revoked_at',
    )
    search_fields = ('user__email', 'user__phone', 'credential_id', 'origin_device_id')
    list_filter = ('revoked_at',)
    readonly_fields = (
        'created_at',
        'updated_at',
        'credential_id',
        'public_key',
        'sign_count',
        'last_used_at',
    )
    actions = ['revoke_restore_credentials']

    def user_email(self, obj):
        return obj.user.email if obj.user else None

    def revoke_restore_credentials(self, request, queryset):
        now = timezone.now()
        updated = queryset.filter(revoked_at__isnull=True).update(
            revoked_at=now, revoke_reason='admin_revoke', updated_at=now,
        )
        self.message_user(request, f"Revoked {updated} restore credentials")
    revoke_restore_credentials.short_description = 'Revoke selected restore credentials'


@admin.register(UsageQuota)
class UsageQuotaAdmin(admin.ModelAdmin):
    list_display = ('user_email', 'quotas_json', 'last_reset_at')
    search_fields = ('user__email',)
    readonly_fields = ('created_at', 'updated_at')

    def user_email(self, obj):
        return obj.user.email if obj.user else None

# -------------------------
# Experience / Education / Skills / Projects
# -------------------------
@admin.register(Experience)
class ExperienceAdmin(admin.ModelAdmin):
    list_display = ('user_email', 'title', 'start_date', 'end_date', 'currently_working')
    search_fields = ('title', 'user__email')
    readonly_fields = ('created_at', 'updated_at')

    def user_email(self, obj):
        return obj.user.email if obj.user else None

@admin.register(Education)
class EducationAdmin(admin.ModelAdmin):
    list_display = ('user_email', 'school', 'start_date', 'end_date', 'currently_studying')
    search_fields = ('school', 'user__email')
    readonly_fields = ('created_at', 'updated_at')

    def user_email(self, obj):
        return obj.user.email if obj.user else None

@admin.register(UserSkill)
class UserSkillAdmin(admin.ModelAdmin):
    list_display = ('user_email', 'skill_id', 'verified', 'endorsements')
    search_fields = ('user__email', 'skill_id')
    readonly_fields = ('created_at', 'updated_at')

    def user_email(self, obj):
        return obj.user.email if obj.user else None

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('user_email', 'name', 'start_date', 'end_date')
    search_fields = ('name', 'user__email')
    readonly_fields = ('created_at', 'updated_at')

    def user_email(self, obj):
        return obj.user.email if obj.user else None

@admin.register(Recommendation)
class RecommendationAdmin(admin.ModelAdmin):
    list_display = ('recommended_user_email', 'recommender_user_email', 'approved', 'created_at')
    search_fields = ('recommended_user__email', 'recommender_user__email')
    list_filter = ('approved',)
    readonly_fields = ('created_at', 'updated_at')

    def recommended_user_email(self, obj):
        return obj.recommended_user.email if obj.recommended_user else None

    def recommender_user_email(self, obj):
        return obj.recommender_user.email if obj.recommender_user else None

# -------------------------
# Misc admin registrations
# -------------------------
admin.site.register(TwoFactor)
admin.site.register(BillingAccount)
admin.site.register(OrganizationLink)
admin.site.register(FeatureFlag)
admin.site.register(AIAccess)
admin.site.register(RevenueAccount)
admin.site.register(GDPRRequest)

# Audit log - read-only
@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('actor_id', 'action', 'severity', 'ip_address', 'created_at')
    readonly_fields = ('actor_id', 'action', 'meta', 'created_at', 'updated_at')
    search_fields = ('action',)
    list_filter = ('action', 'created_at')

    def severity(self, obj):
        return (obj.meta or {}).get('severity', '')

    def ip_address(self, obj):
        return (obj.meta or {}).get('ip', '')
