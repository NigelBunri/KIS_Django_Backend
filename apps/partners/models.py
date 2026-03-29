# apps/partners/models.py
import uuid

from django.db import models
from django.utils import timezone

from apps.accounts.models import User
from apps.chat.models import Conversation
from apps.chat.models import Conversation


class Partner(models.Model):
    """
    Partner entity (e.g. organization, ministry, company).

    A Partner can own multiple Communities, Groups, Channels, etc.
    It can also have an optional main_conversation for partner-wide announcements/chat.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    description = models.TextField(blank=True)

    avatar_url = models.URLField(
        blank=True,
        help_text="Optional logo/avatar URL for this partner.",
    )

    owner = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="owned_partners",
        help_text="Primary owner of this partner.",
    )

    main_conversation = models.OneToOneField(
        Conversation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="partner_main",
        help_text="Optional main conversation for this partner (e.g. announcements).",
    )

    is_active = models.BooleanField(default=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivated_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deactivated_partners",
    )
    class DeactivationSource(models.TextChoices):
        USER = "user", "User"
        SYSTEM = "system", "System"
    deactivation_source = models.CharField(
        max_length=16,
        choices=DeactivationSource.choices,
        null=True,
        blank=True,
    )
    grace_expires_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_partner"

    def __str__(self) -> str:
        return self.name

    @property
    def display_name(self) -> str:
        """
        Backfill a display_name alias so legacy serializers/views that expect this attribute keep working.
        """
        return self.name

    @property
    def is_system_deactivated(self) -> bool:
        return self.deactivation_source == Partner.DeactivationSource.SYSTEM and not self.is_active


def _default_join_methods():
    return [
        "application",
        "subscription",
        "invite",
        "referral",
        "auto_approve",
        "staff_pick",
        "event_pass",
        "donation",
        "volunteer",
        "external_verification",
        "course_completion",
    ]


class PartnerJoinConfig(models.Model):
    partner = models.OneToOneField(
        Partner,
        on_delete=models.CASCADE,
        related_name="join_config",
    )
    allow_public_listing = models.BooleanField(default=True)
    allow_apply = models.BooleanField(default=True)
    allow_subscribe = models.BooleanField(default=True)
    auto_approve = models.BooleanField(default=False)
    require_profile = models.BooleanField(default=True)
    methods = models.JSONField(default=_default_join_methods, blank=True)
    criteria = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_join_config"


class PartnerMembershipStatus(models.TextChoices):
    MEMBER = "member", "Member"
    SUBSCRIBER = "subscriber", "Subscriber"
    APPLICANT = "applicant", "Applicant"
    PENDING = "pending", "Pending"
    REJECTED = "rejected", "Rejected"
    REMOVED = "removed", "Removed"


class PartnerMembership(models.Model):
    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="partner_memberships",
    )
    status = models.CharField(
        max_length=16,
        choices=PartnerMembershipStatus.choices,
        default=PartnerMembershipStatus.MEMBER,
        db_index=True,
    )
    role = models.CharField(max_length=16, default="member")
    lesson_access_only = models.BooleanField(
        default=False,
        help_text="True when membership exists solely because of a lesson enrollment.",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_membership"
        unique_together = [("partner", "user")]
        indexes = [
            models.Index(fields=["partner", "status"]),
        ]


class PartnerJobPost(models.Model):
    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="job_posts",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    requirements = models.TextField(blank=True)
    steps = models.JSONField(default=list, blank=True)
    auto_assign = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_job_post"
        indexes = [
            models.Index(fields=["partner", "is_active"]),
        ]


class PartnerApplicationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    WITHDRAWN = "withdrawn", "Withdrawn"


class PartnerApplication(models.Model):
    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="applications",
    )
    job_post = models.ForeignKey(
        PartnerJobPost,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="applications",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="partner_applications",
    )
    method = models.CharField(max_length=32, default="application")
    message = models.TextField(blank=True)
    answers = models.JSONField(default=dict, blank=True)
    profile_visible = models.BooleanField(default=True)
    status = models.CharField(
        max_length=16,
        choices=PartnerApplicationStatus.choices,
        default=PartnerApplicationStatus.PENDING,
        db_index=True,
    )
    stage_index = models.IntegerField(default=0)
    stage_state = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_application"
        indexes = [
            models.Index(fields=["partner", "status"]),
        ]


class PartnerFeatureFlag(models.Model):
    """
    Feature-level enablement flags for partner settings.
    """
    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="feature_flags",
    )
    key = models.CharField(max_length=128)
    is_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_feature_flag"
        unique_together = [("partner", "key")]
        indexes = [
            models.Index(fields=["partner", "key"]),
        ]


class PartnerPost(models.Model):
    """
    Lightweight feed posts for a Partner's general feed page.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="posts",
    )
    author = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="partner_posts",
    )
    text = models.JSONField(
        default=dict,
        blank=True,
        help_text="ProseMirror-style document that captures the formatted text.",
    )
    text_plain = models.TextField(
        blank=True,
        help_text="Plain text extracted from the rich document.",
    )
    text_preview = models.CharField(
        max_length=512,
        blank=True,
        help_text="Preview text for lists/previews.",
    )
    attachments = models.JSONField(default=list, blank=True)
    poll = models.JSONField(default=dict, blank=True)
    event = models.JSONField(default=dict, blank=True)
    link = models.URLField(blank=True)
    is_broadcast = models.BooleanField(default=False)
    comment_conversation = models.ForeignKey(
        Conversation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="partner_post_comments",
    )
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_post"
        indexes = [
            models.Index(fields=["partner", "created_at"]),
        ]


class PartnerPostComment(models.Model):
    id = models.BigAutoField(primary_key=True)
    post = models.ForeignKey(
        PartnerPost,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    author = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="partner_post_comments",
    )
    text = models.TextField()
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_post_comment"
        indexes = [
            models.Index(fields=["post", "created_at"]),
        ]


class PartnerPostReaction(models.Model):
    id = models.BigAutoField(primary_key=True)
    post = models.ForeignKey(
        PartnerPost,
        on_delete=models.CASCADE,
        related_name="reactions",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="partner_post_reactions",
    )
    emoji = models.CharField(max_length=32)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "partner_post_reaction"
        unique_together = [("post", "user")]
        indexes = [
            models.Index(fields=["post", "created_at"]),
        ]


def _default_partner_policy():
    return {
        "security": {
            "require_mfa": False,
            "session_timeout_minutes": 60,
            "allow_external_sharing": True,
        },
        "compliance": {
            "audit_enabled": True,
            "legal_hold_enabled": False,
        },
        "retention": {
            "message_retention_days": 365,
            "file_retention_days": 365,
        },
        "dlp": {
            "enabled": False,
            "block_patterns": [],
            "warn_patterns": [],
        },
        "data_residency": {
            "region": "auto",
            "allow_cross_region": True,
        },
        "automation": {
            "webhooks_enabled": False,
            "event_triggers": [],
        },
        "rate_limits": {
            "messages_per_minute": 120,
            "uploads_per_hour": 200,
        },
        "integrations": {
            "sso_required": False,
            "scim_enabled": False,
            "api_access_enabled": True,
        },
    }


class PartnerPolicy(models.Model):
    """
    Enterprise policy configuration for a partner.
    """
    id = models.BigAutoField(primary_key=True)
    partner = models.OneToOneField(
        Partner,
        on_delete=models.CASCADE,
        related_name="policy",
    )
    settings = models.JSONField(default=_default_partner_policy, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_policy"


class PartnerRole(models.Model):
    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="roles",
    )
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True)
    permissions = models.JSONField(default=list, blank=True)
    parent_role = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="child_roles",
    )
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_role"
        unique_together = [("partner", "name")]
        indexes = [
            models.Index(fields=["partner", "name"]),
        ]


class PartnerRoleAssignment(models.Model):
    """
    Assign a partner-scoped role to a user, optionally scoped to a unit.
    """
    SCOPE_GLOBAL = "global"
    SCOPE_COMMUNITY = "community"
    SCOPE_GROUP = "group"
    SCOPE_CHANNEL = "channel"
    SCOPE_CHOICES = [
        (SCOPE_GLOBAL, "Global"),
        (SCOPE_COMMUNITY, "Community"),
        (SCOPE_GROUP, "Group"),
        (SCOPE_CHANNEL, "Channel"),
    ]

    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="role_assignments",
    )
    role = models.ForeignKey(
        PartnerRole,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="partner_role_assignments",
    )
    scope_type = models.CharField(max_length=32, choices=SCOPE_CHOICES, default=SCOPE_GLOBAL)
    scope_id = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "partner_role_assignment"
        indexes = [
            models.Index(fields=["partner", "user"]),
            models.Index(fields=["partner", "scope_type", "scope_id"]),
        ]


class PartnerAuditEvent(models.Model):
    """
    Immutable audit log entries for partner governance.
    """
    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="audit_events",
    )
    actor = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="partner_audit_events",
    )
    action = models.CharField(max_length=128)
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=128, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "partner_audit_event"
        indexes = [
            models.Index(fields=["partner", "created_at"]),
            models.Index(fields=["partner", "action"]),
        ]


class PartnerIntegration(models.Model):
    """
    External integration config (SSO/SCIM/webhooks placeholders).
    """
    KIND_SSO = "sso"
    KIND_SCIM = "scim"
    KIND_WEBHOOK = "webhook"
    KIND_CHOICES = [
        (KIND_SSO, "SSO"),
        (KIND_SCIM, "SCIM"),
        (KIND_WEBHOOK, "Webhook"),
    ]

    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="integrations",
    )
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    provider = models.CharField(max_length=128, blank=True)
    config = models.JSONField(default=dict, blank=True)
    is_enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_integration"
        indexes = [
            models.Index(fields=["partner", "kind"]),
        ]


class PartnerWebhook(models.Model):
    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="webhooks",
    )
    name = models.CharField(max_length=128)
    url = models.URLField(max_length=500)
    events = models.JSONField(default=list, blank=True)
    secret = models.CharField(max_length=128, blank=True)
    is_active = models.BooleanField(default=True)
    retry_limit = models.PositiveIntegerField(default=3)
    retry_backoff_seconds = models.PositiveIntegerField(default=60)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_webhook"
        indexes = [
            models.Index(fields=["partner", "is_active"]),
        ]


class PartnerWebhookDeliveryStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"


class PartnerWebhookDelivery(models.Model):
    id = models.BigAutoField(primary_key=True)
    webhook = models.ForeignKey(
        PartnerWebhook,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    event = models.CharField(max_length=128)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=16,
        choices=PartnerWebhookDeliveryStatus.choices,
        default=PartnerWebhookDeliveryStatus.PENDING,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    response_code = models.IntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_webhook_delivery"
        indexes = [
            models.Index(fields=["status", "next_retry_at"]),
            models.Index(fields=["webhook", "created_at"]),
        ]


class PartnerAutomationRule(models.Model):
    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="automation_rules",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    trigger = models.CharField(max_length=64, db_index=True)
    conditions = models.JSONField(default=dict, blank=True)
    actions = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="partner_automation_rules",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_run_status = models.CharField(max_length=32, blank=True)
    last_run_message = models.TextField(blank=True)

    class Meta:
        db_table = "partner_automation_rule"
        indexes = [
            models.Index(fields=["partner", "trigger"]),
            models.Index(fields=["partner", "is_active"]),
        ]


class PartnerReportSnapshot(models.Model):
    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="report_snapshots",
    )
    kind = models.CharField(max_length=64, db_index=True)
    data = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="partner_report_snapshots",
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "partner_report_snapshot"
        indexes = [
            models.Index(fields=["partner", "kind"]),
            models.Index(fields=["partner", "created_at"]),
        ]


class PartnerExportStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class PartnerExportJob(models.Model):
    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="export_jobs",
    )
    kind = models.CharField(max_length=64, db_index=True)
    export_format = models.CharField(max_length=16, default="csv")
    status = models.CharField(
        max_length=16,
        choices=PartnerExportStatus.choices,
        default=PartnerExportStatus.PENDING,
    )
    file_path = models.CharField(max_length=512, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="partner_export_jobs",
    )
    created_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "partner_export_job"
        indexes = [
            models.Index(fields=["partner", "status"]),
            models.Index(fields=["partner", "kind"]),
        ]


class PartnerAccessRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class PartnerAccessRequest(models.Model):
    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="access_requests",
    )
    requester = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="partner_access_requests",
    )
    target_user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="partner_access_targets",
    )
    requested_role = models.ForeignKey(
        "PartnerRole",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="access_requests",
    )
    scope_type = models.CharField(max_length=32, default="global")
    scope_id = models.CharField(max_length=128, blank=True)
    justification = models.TextField(blank=True)
    status = models.CharField(
        max_length=16,
        choices=PartnerAccessRequestStatus.choices,
        default=PartnerAccessRequestStatus.PENDING,
        db_index=True,
    )
    decided_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="partner_access_decisions",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "partner_access_request"
        indexes = [
            models.Index(fields=["partner", "status"]),
        ]


class PartnerAccessReviewStatus(models.TextChoices):
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"


class PartnerAccessReview(models.Model):
    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="access_reviews",
    )
    name = models.CharField(max_length=255)
    scope_type = models.CharField(max_length=32, default="global")
    scope_id = models.CharField(max_length=128, blank=True)
    findings = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=16,
        choices=PartnerAccessReviewStatus.choices,
        default=PartnerAccessReviewStatus.OPEN,
    )
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="partner_access_reviews",
    )
    created_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "partner_access_review"
        indexes = [
            models.Index(fields=["partner", "status"]),
        ]


class PartnerExportScheduleFrequency(models.TextChoices):
    DAILY = "daily", "Daily"
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"


class PartnerExportSchedule(models.Model):
    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="export_schedules",
    )
    kind = models.CharField(max_length=64, db_index=True)
    export_format = models.CharField(max_length=16, default="csv")
    frequency = models.CharField(
        max_length=16,
        choices=PartnerExportScheduleFrequency.choices,
        default=PartnerExportScheduleFrequency.WEEKLY,
    )
    is_active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="partner_export_schedules",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_export_schedule"
        indexes = [
            models.Index(fields=["partner", "is_active"]),
            models.Index(fields=["partner", "kind"]),
        ]


class PartnerSetting(models.Model):
    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="settings",
    )
    key = models.CharField(max_length=128, db_index=True)
    config = models.JSONField(default=dict, blank=True)
    updated_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="partner_settings_updates",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_setting"
        unique_together = [("partner", "key")]
        indexes = [
            models.Index(fields=["partner", "key"]),
        ]


class PartnerOrganizationProfile(models.Model):
    partner = models.OneToOneField(
        Partner,
        on_delete=models.CASCADE,
        related_name="organization_profile",
        primary_key=True,
    )
    display_name = models.CharField(max_length=255)
    legal_name = models.CharField(max_length=255, blank=True)
    tagline = models.CharField(max_length=255, blank=True)
    mission = models.TextField(blank=True)
    vision = models.TextField(blank=True)
    website = models.URLField(blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    industry = models.CharField(max_length=128, blank=True)
    size = models.CharField(max_length=64, blank=True)
    founded_year = models.IntegerField(null=True, blank=True)
    headquarters = models.CharField(max_length=255, blank=True)
    logo_url = models.URLField(blank=True)
    brand_colors = models.JSONField(default=list, blank=True)
    social_links = models.JSONField(default=dict, blank=True)
    public_fields = models.JSONField(default=dict, blank=True)
    updated_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="partner_org_profile_updates",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_organization_profile"


PARTNER_ORG_APP_VISIBILITY_ROLES = ["owner", "admin", "manager", "member", "analyst", "viewer"]


class PartnerOrganizationAppType(models.TextChoices):
    KIS = "kis", "KIS App"
    BIBLE = "bible", "Bible App"
    EXTERNAL = "external", "External App"


def default_app_visibility():
    return ["owner", "admin", "manager"]


def _default_app_visibility():
    return default_app_visibility()

class PartnerOrganizationApp(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="organization_apps",
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, blank=True)
    type = models.CharField(max_length=32, choices=PartnerOrganizationAppType.choices)
    description = models.TextField(blank=True)
    link = models.URLField(blank=True)
    module = models.CharField(max_length=128, blank=True)
    icon = models.CharField(max_length=128, blank=True)
    config = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    visible_to = models.JSONField(default=default_app_visibility, blank=True)
    group = models.CharField(max_length=128, blank=True)
    badge_label = models.CharField(max_length=128, blank=True)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_organization_app"
        ordering = ["order", "name"]
        indexes = [
            models.Index(fields=["partner", "type"], name="partner_org_app_pt_idx"),
            models.Index(fields=["partner", "order"], name="partner_org_app_po_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.partner_id})"


class PartnerOrganizationAppAccessLog(models.Model):
    id = models.BigAutoField(primary_key=True)
    app = models.ForeignKey(
        PartnerOrganizationApp,
        on_delete=models.CASCADE,
        related_name="access_logs",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="organization_app_access_logs",
    )
    action = models.CharField(max_length=64)
    data_scope = models.JSONField(default=list, blank=True)
    consent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "partner_organization_app_access_log"
        indexes = [
            models.Index(fields=["app", "created_at"]),
            models.Index(fields=["user", "created_at"]),
        ]
        verbose_name = "Partner organization app access log"


def _default_partner_profile_link_analytics():
    return {
        "enrollments": 0,
        "completions": 0,
        "watchMinutes": 0,
        "revenueCents": 0,
    }


class PartnerProfileLink(models.Model):
    PROFILE_BROADCAST = "broadcast_feed"
    PROFILE_HEALTH = "health"
    PROFILE_MARKET = "market"
    PROFILE_EDUCATION = "education"

    ROLE_ADMIN = "admin"
    ROLE_EDITOR = "editor"
    ROLE_VIEWER = "viewer"

    PROFILE_CHOICES = (
        (PROFILE_BROADCAST, "Broadcast"),
        (PROFILE_HEALTH, "Health"),
        (PROFILE_MARKET, "Market"),
        (PROFILE_EDUCATION, "Education"),
    )

    ROLE_CHOICES = (
        (ROLE_ADMIN, "Admin"),
        (ROLE_EDITOR, "Editor"),
        (ROLE_VIEWER, "Viewer"),
    )

    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="profile_links",
    )
    profile_key = models.CharField(
        max_length=32,
        choices=PROFILE_CHOICES,
    )
    linked = models.BooleanField(default=False)
    role = models.CharField(
        max_length=16,
        choices=ROLE_CHOICES,
        default=ROLE_VIEWER,
    )
    analytics = models.JSONField(
        default=_default_partner_profile_link_analytics,
        blank=True,
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_profile_link"
        unique_together = [("partner", "profile_key")]

    def __str__(self):
        return f"{self.partner.name} - {self.profile_key}"
