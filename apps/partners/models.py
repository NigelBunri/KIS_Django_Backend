# apps/partners/models.py
import uuid

from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.chat.models import Conversation
from common.media_urls import normalize_image_payload


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

    def save(self, *args, **kwargs):
        self.avatar_url = normalize_image_payload(self.avatar_url)
        super().save(*args, **kwargs)

    @property
    def display_name(self) -> str:
        """
        Backfill a display_name alias so legacy serializers/views that expect this attribute keep working.
        """
        return self.name

    @property
    def is_system_deactivated(self) -> bool:
        return self.deactivation_source == Partner.DeactivationSource.SYSTEM and not self.is_active


class PartnerSubscription(models.Model):
    """
    Workspace-level plan for this Partner — deliberately separate from the
    owner's personal apps.accounts.models.Subscription. Before this model
    existed, every partner-scoped feature gate (webhooks, automation,
    integrations, insight/analytics, access control) checked the REQUESTING
    USER's own personal tier (see apps/partners/tiers.py's
    require_partner_feature, and the views.py call sites it replaced) — so
    two bugs at once: (1) an org's capabilities silently changed if its
    owner's personal subscription lapsed or changed, with no connection to
    the organization's own activity or plan, and (2) a manager/admin acting
    on behalf of the org was gated by THEIR OWN personal tier rather than
    the org's, so the same organization effectively had a different feature
    set depending on which staff member happened to be logged in.

    Mirrors apps.accounts.models.Subscription's shape closely (status
    vocabulary, started_at/ends_at/cancel_at_period_end/grace_ends_at) so
    the two systems stay easy to reason about side by side, but is
    intentionally its own table rather than a nullable-partner column
    bolted onto Subscription — Subscription is billing-critical and widely
    queried assuming a non-null user; widening it to double as the
    workspace-billing entity would have meant auditing every existing call
    site for a partner-shaped row it never expected to see instead of
    building a narrow, independently-reasoned-about model.

    Real payment/checkout integration for workspace plans (Stripe or
    otherwise) is intentionally out of scope here — this lays the
    structural foundation (a workspace has ITS OWN tier, independent of any
    one person) so that a following pass wiring up checkout doesn't also
    have to re-litigate the ownership question.
    """

    STATUS_ACTIVE = "active"
    STATUS_CANCELLED = "cancelled"
    STATUS_EXPIRED = "expired"
    STATUS_SUPERSEDED = "superseded"
    STATUS_REFUNDED = "refunded"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_SUPERSEDED, "Superseded"),
        (STATUS_REFUNDED, "Refunded"),
    ]

    partner = models.OneToOneField(
        Partner,
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    tier = models.ForeignKey(
        "accounts.AccountTier",
        on_delete=models.SET_NULL,
        null=True,
        related_name="partner_subscriptions",
    )
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    started_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    canceled_at = models.DateTimeField(null=True, blank=True)
    grace_ends_at = models.DateTimeField(null=True, blank=True)
    pending_tier = models.ForeignKey(
        "accounts.AccountTier",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pending_partner_subscriptions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        tier_name = self.tier.name if self.tier_id else "no tier"
        return f"{self.partner.slug} ({tier_name}, {self.status})"


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
    is_muted = models.BooleanField(default=False)
    muted_until = models.DateTimeField(null=True, blank=True)
    timed_out_until = models.DateTimeField(null=True, blank=True)
    is_banned = models.BooleanField(default=False)
    banned_at = models.DateTimeField(null=True, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_membership"
        unique_together = [("partner", "user")]
        indexes = [
            models.Index(fields=["partner", "status"]),
            models.Index(fields=["partner", "is_banned"]),
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
    location = models.CharField(max_length=200, blank=True, default="")
    is_remote = models.BooleanField(default=False)
    job_type = models.CharField(
        max_length=30,
        choices=[("full_time", "Full Time"), ("part_time", "Part Time"), ("contract", "Contract"), ("freelance", "Freelance"), ("internship", "Internship")],
        default="full_time",
    )
    salary_min_cents = models.PositiveIntegerField(null=True, blank=True)
    salary_max_cents = models.PositiveIntegerField(null=True, blank=True)
    salary_currency = models.CharField(max_length=10, blank=True, default="USD")
    tags = models.JSONField(default=list, blank=True)
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


def generate_partner_invite_code() -> str:
    return uuid.uuid4().hex[:12].upper()


class PartnerInvite(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="invites",
    )
    code = models.CharField(max_length=32, unique=True, default=generate_partner_invite_code)
    label = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="partner_invites_created",
    )
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    use_count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    membership_role = models.CharField(max_length=16, default="member")
    auto_assign = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_invite"
        indexes = [
            models.Index(fields=["partner", "is_active"]),
            models.Index(fields=["partner", "code"]),
        ]

    def __str__(self) -> str:
        return f"{self.partner.name} / {self.code}"

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and self.expires_at <= timezone.now())

    @property
    def has_uses_remaining(self) -> bool:
        return self.max_uses is None or self.use_count < self.max_uses

    def is_redeemable(self) -> bool:
        return self.is_active and not self.is_expired and self.has_uses_remaining


class PartnerOnboardingProgress(models.Model):
    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="onboarding_progress",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="partner_onboarding_progress",
    )
    invite = models.ForeignKey(
        PartnerInvite,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="onboarding_progress",
    )
    rules_accepted_at = models.DateTimeField(null=True, blank=True)
    selected_role_ids = models.JSONField(default=list, blank=True)
    selected_channel_ids = models.JSONField(default=list, blank=True)
    onboarding_snapshot = models.JSONField(default=dict, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_onboarding_progress"
        unique_together = [("partner", "user")]
        indexes = [
            models.Index(fields=["partner", "user"]),
        ]


class PartnerModerationAction(models.Model):
    class ActionType(models.TextChoices):
        MUTE = "mute", "Mute"
        UNMUTE = "unmute", "Unmute"
        TIMEOUT = "timeout", "Timeout"
        KICK = "kick", "Kick"
        BAN = "ban", "Ban"
        UNBAN = "unban", "Unban"

    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="moderation_actions",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="partner_moderation_actions",
    )
    actor = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="partner_moderation_actions_made",
    )
    membership = models.ForeignKey(
        PartnerMembership,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="moderation_actions",
    )
    action_type = models.CharField(max_length=16, choices=ActionType.choices)
    reason = models.CharField(max_length=500, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "partner_moderation_action"
        indexes = [
            models.Index(fields=["partner", "user", "action_type"]),
            models.Index(fields=["partner", "created_at"]),
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


class PartnerPostStatus(models.TextChoices):
    PUBLISHED = "published", "Published"
    SCHEDULED = "scheduled", "Scheduled"


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
    status = models.CharField(max_length=16, choices=PartnerPostStatus.choices, default=PartnerPostStatus.PUBLISHED)
    scheduled_for = models.DateTimeField(null=True, blank=True, db_index=True)
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
            models.Index(fields=["status", "scheduled_for"]),
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

    def save(self, *args, **kwargs):
        self.logo_url = normalize_image_payload(self.logo_url)
        super().save(*args, **kwargs)


class PartnerServerCategory(models.Model):
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="server_categories",
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    order = models.PositiveIntegerField(default=0)
    is_private = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_server_category"
        ordering = ["order", "name"]
        unique_together = [("partner", "slug")]
        indexes = [
            models.Index(fields=["partner", "order"]),
        ]

    def __str__(self) -> str:
        return f"{self.partner.name} / {self.name}"


class PartnerChannelPermissionOverwrite(models.Model):
    class SubjectType(models.TextChoices):
        ROLE = "role", "Role"
        MEMBER = "member", "Member"

    class PermissionCode(models.TextChoices):
        # Only genuinely per-channel concepts belong here. Partner-wide
        # actions (kick/ban members, manage roles/categories/webhooks) are
        # granted via PartnerRole.permissions codenames instead (see
        # apps.partners.services.user_has_partner_permission) — a channel
        # can't meaningfully "allow kicking members" independent of others.
        VIEW_CHANNEL = "view_channel", "View channel"
        SEND_MESSAGES = "send_messages", "Send messages"
        MANAGE_CHANNEL = "manage_channel", "Manage channel"
        MENTION_EVERYONE = "mention_everyone", "Mention @everyone"

    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="channel_permission_overwrites",
    )
    channel = models.ForeignKey(
        "channels.Channel",
        on_delete=models.CASCADE,
        related_name="permission_overwrites",
    )
    subject_type = models.CharField(max_length=16, choices=SubjectType.choices)
    role = models.ForeignKey(
        PartnerRole,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="channel_permission_overwrites",
    )
    user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="partner_channel_permission_overwrites",
    )
    allow_permissions = models.JSONField(default=list, blank=True)
    deny_permissions = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_channel_permission_overwrite"
        indexes = [
            models.Index(fields=["partner", "channel", "subject_type"]),
            models.Index(fields=["channel", "role"]),
            models.Index(fields=["channel", "user"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "role"],
                condition=models.Q(subject_type="role", role__isnull=False),
                name="partner_channel_overwrite_unique_role",
            ),
            models.UniqueConstraint(
                fields=["channel", "user"],
                condition=models.Q(subject_type="member", user__isnull=False),
                name="partner_channel_overwrite_unique_user",
            ),
        ]

    def clean(self):
        super().clean()
        if self.subject_type == self.SubjectType.ROLE:
            if not self.role_id or self.user_id:
                raise ValidationError("Role overwrites must target exactly one role.")
        if self.subject_type == self.SubjectType.MEMBER:
            if not self.user_id or self.role_id:
                raise ValidationError("Member overwrites must target exactly one user.")
        if self.channel_id and self.partner_id and self.channel.partner_id != self.partner_id:
            raise ValidationError("Overwrite partner must match the channel partner.")
        if self.role_id and self.role.partner_id != self.partner_id:
            raise ValidationError("Overwrite role must belong to the same partner.")

    def __str__(self) -> str:
        target = self.role.name if self.role_id else getattr(self.user, "username", self.user_id)
        return f"{self.channel_id} / {self.subject_type} / {target}"


PARTNER_ORG_APP_VISIBILITY_ROLES = ["owner", "admin", "manager", "member", "analyst", "viewer"]


class PartnerOrganizationAppType(models.TextChoices):
    KIS = "kis", "KIS App"
    BIBLE = "bible", "Bible App"
    EXTERNAL = "external", "External App"


class PartnerOrganizationAppStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    REVIEW = "review", "In review"
    PUBLISHED = "published", "Published"
    ARCHIVED = "archived", "Archived"


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
    status = models.CharField(
        max_length=16,
        choices=PartnerOrganizationAppStatus.choices,
        default=PartnerOrganizationAppStatus.DRAFT,
        db_index=True,
    )
    is_promoted_global = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Only KCAN/platform-controlled apps should be promoted into the main app navigation.",
    )
    promoted_order = models.PositiveIntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True)
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
            models.Index(fields=["is_promoted_global", "status"], name="partner_org_app_global_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.partner_id})"


class PartnerOrganizationAppTab(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    app = models.ForeignKey(
        PartnerOrganizationApp,
        on_delete=models.CASCADE,
        related_name="tabs",
    )
    title = models.CharField(max_length=120)
    slug = models.SlugField(max_length=120)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=128, blank=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    visible_to = models.JSONField(default=default_app_visibility, blank=True)
    config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_organization_app_tab"
        ordering = ["order", "title"]
        unique_together = [("app", "slug")]
        indexes = [
            models.Index(fields=["app", "order"], name="partner_org_tab_app_order_idx"),
            models.Index(fields=["app", "is_active"], name="partner_org_tab_app_active_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.app.name} / {self.title}"


class PartnerOrganizationAppContentBlock(models.Model):
    class BlockType(models.TextChoices):
        TEXT = "text", "Text"
        RICH_TEXT = "rich_text", "Rich text"
        VIDEO = "video", "Video"
        IMAGE = "image", "Image"
        LINK = "link", "Link"
        FILE = "file", "File"
        EMBED = "embed", "Embed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tab = models.ForeignKey(
        PartnerOrganizationAppTab,
        on_delete=models.CASCADE,
        related_name="content_blocks",
    )
    block_type = models.CharField(max_length=32, choices=BlockType.choices, default=BlockType.TEXT)
    title = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True)
    media_url = models.URLField(blank=True)
    payload = models.JSONField(default=dict, blank=True)
    order = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=16,
        choices=PartnerOrganizationAppStatus.choices,
        default=PartnerOrganizationAppStatus.DRAFT,
        db_index=True,
    )
    is_active = models.BooleanField(default=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_partner_app_blocks",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_organization_app_content_block"
        ordering = ["order", "created_at"]
        indexes = [
            models.Index(fields=["tab", "order"], name="partner_block_tab_order_idx"),
            models.Index(fields=["tab", "status"], name="partner_block_tab_status_idx"),
        ]

    def __str__(self) -> str:
        return self.title or f"{self.block_type} block"


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


class UserAppShortcut(models.Model):
    """Tracks device home-screen shortcuts created by users for partner org apps."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="app_shortcuts",
    )
    app = models.ForeignKey(
        "PartnerOrganizationApp",
        on_delete=models.CASCADE,
        related_name="shortcuts",
        null=True,
        blank=True,
    )
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="shortcuts",
    )
    device_id = models.CharField(max_length=255, blank=True)
    shortcut_name = models.CharField(max_length=128)
    icon = models.URLField(blank=True)
    deep_link = models.CharField(max_length=512)
    pinned = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    last_opened_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "user_app_shortcut"
        indexes = [
            models.Index(fields=["user", "partner"]),
            models.Index(fields=["user", "device_id"]),
        ]

    def __str__(self):
        return f"{self.user_id} → {self.shortcut_name}"


class PartnerOrganizationLinkType(models.TextChoices):
    """Subset of apps.websites.models.WebsiteOwnerType — everything except
    PARTNER itself, since a partner linking to another partner isn't a
    supported relationship here."""

    SHOP = "shop", "Shop"
    HEALTH_INSTITUTION = "health_institution", "Health Institution"
    EDUCATION_INSTITUTION = "education_institution", "Education Institution"
    BROADCAST_CHANNEL = "broadcast_channel", "Broadcast Channel"


class PartnerOrganizationLink(models.Model):
    """Connects a Partner to one of its owned organizations (Shop, Health
    Institution, Education Institution, Broadcast Channel).

    Deliberately NOT a new FK column on each of those four separate
    models (which live in commerce/health_ops/broadcasts) — that would
    give apps.partners four new cross-app dependencies for one feature.
    Instead this reuses the polymorphic owner_type/owner_id pattern
    apps.websites.owner_resolution already established for the exact
    same five entity types, resolved lazily via django.apps at read time.

    One organization can only ever be linked to one partner at a time
    (unique on owner_type+owner_id, not on partner+owner_type+owner_id) —
    an institution belongs to a single partner umbrella, not several.

    Answers a different question than Shop.partner/HealthInstitution.partner/
    EducationInstitution.partner (added later, directly on those models):
    this table is a personal-ownership directory listing only — linking
    requires the requester to already personally own the organization
    (_resolve_linkable_organization in apps/partners/views.py), and linking
    grants no management rights to anyone. Those other .partner FKs are the
    actual management-delegation mechanism (any manager of the partner
    gains the same rights as the organization's own owner, via
    apps.partners.services.partner_user_can_manage) - the two are
    independent and can disagree (an org can be listed here under one
    partner while delegated to a different one via .partner, or vice
    versa). Not unified yet - a future pass could make this table read
    from/write through the .partner FKs instead of tracking its own state.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name="organization_links")
    owner_type = models.CharField(max_length=32, choices=PartnerOrganizationLinkType.choices)
    owner_id = models.UUIDField()
    linked_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "partner_organization_link"
        constraints = [
            models.UniqueConstraint(fields=["owner_type", "owner_id"], name="partner_org_link_unique_owner"),
        ]
        indexes = [
            models.Index(fields=["partner"]),
        ]

    def __str__(self) -> str:
        return f"{self.partner_id} ↔ {self.owner_type}:{self.owner_id}"


class PartnerDepartment(models.Model):
    """Org Setup > Departments & Units. A named group within a partner
    organization that members can belong to — separate from PartnerRole
    (which grants permissions) and Channel/Group (which are chat spaces);
    a department is purely an org-structure grouping, e.g. "Finance" or
    "Youth Ministry", with an optional lead and member roster."""

    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name="departments")
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True)
    lead = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_department"
        unique_together = [("partner", "name")]
        ordering = ["order", "name"]
        indexes = [models.Index(fields=["partner"])]

    def __str__(self) -> str:
        return f"{self.name} ({self.partner_id})"


class PartnerDepartmentMembership(models.Model):
    id = models.BigAutoField(primary_key=True)
    department = models.ForeignKey(PartnerDepartment, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="partner_department_memberships")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "partner_department_membership"
        unique_together = [("department", "user")]


class PartnerLocation(models.Model):
    """Org Setup > Locations & Branches. A physical location/branch/campus
    belonging to a partner organization — address + contact info, with one
    location per partner flaggable as the primary/HQ site."""

    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name="locations")
    name = models.CharField(max_length=128)
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=128, blank=True)
    country = models.CharField(max_length=128, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    notes = models.TextField(blank=True)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_location"
        ordering = ["-is_primary", "name"]
        indexes = [models.Index(fields=["partner"])]

    def __str__(self) -> str:
        return f"{self.name} ({self.partner_id})"


class PartnerDepartmentNoteCategory(models.TextChoices):
    GENERAL = "general", "General"
    SUCCESSION = "succession", "Succession planning"
    GOALS = "goals", "Leadership goals"
    ONBOARDING = "onboarding", "Onboarding path"


class PartnerDepartmentNote(models.Model):
    """Leadership & Org Tree > org_tree_notes/succession_plan/
    leadership_goals/onboarding_paths. One lightweight categorized note
    model covering all four rather than four separate ones — they're all
    "a leader writes something down about a department" at heart, and
    none of them need more structure than that yet."""

    id = models.BigAutoField(primary_key=True)
    department = models.ForeignKey(PartnerDepartment, on_delete=models.CASCADE, related_name="notes")
    category = models.CharField(max_length=16, choices=PartnerDepartmentNoteCategory.choices, default=PartnerDepartmentNoteCategory.GENERAL)
    title = models.CharField(max_length=200, blank=True)
    body = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_department_note"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["department", "category"])]


class PartnerResourceKind(models.TextChoices):
    FILE = "file", "File"
    ARTICLE = "article", "Article"


class PartnerResource(models.Model):
    """General Tools > Resource Library + Knowledge Base — one model
    covering both, since a "resource" is either an uploaded file
    (playbook, template doc, ...) or a written article, distinguished by
    `kind`. Files go through the same presigned-S3 pipeline every other
    upload in this codebase uses (apps.media), rather than a bespoke one."""

    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name="resources")
    kind = models.CharField(max_length=16, choices=PartnerResourceKind.choices)
    title = models.CharField(max_length=200)
    category = models.CharField(max_length=64, blank=True)
    body = models.TextField(blank=True)
    asset = models.ForeignKey(
        "media.MediaAsset", on_delete=models.SET_NULL, null=True, blank=True, related_name="partner_resources",
    )
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_resource"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["partner", "kind"]), models.Index(fields=["partner", "category"])]


class PartnerCalendarEventVisibility(models.TextChoices):
    ALL_MEMBERS = "all_members", "All members"
    ADMINS_ONLY = "admins_only", "Admins only"


class PartnerCalendarEvent(models.Model):
    """General Tools > Events Calendar — org-wide events, distinct from
    the platform's global/personal apps.events (which is per-user and
    unrelated to partner membership). Deliberately not built on
    apps.events since that app's Event.owner_id/permissions model is
    global-only; this is a much smaller, partner-scoped model matching
    the convention used for every other Partners feature in this file."""

    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name="calendar_events")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    virtual_url = models.URLField(blank=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    visibility = models.CharField(
        max_length=16, choices=PartnerCalendarEventVisibility.choices, default=PartnerCalendarEventVisibility.ALL_MEMBERS,
    )
    department = models.ForeignKey(
        PartnerDepartment, on_delete=models.SET_NULL, null=True, blank=True, related_name="calendar_events",
    )
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_calendar_event"
        ordering = ["start_at"]
        indexes = [models.Index(fields=["partner", "start_at"])]


class PartnerCalendarRsvpStatus(models.TextChoices):
    GOING = "going", "Going"
    MAYBE = "maybe", "Maybe"
    DECLINED = "declined", "Declined"


class PartnerCalendarRsvp(models.Model):
    id = models.BigAutoField(primary_key=True)
    event = models.ForeignKey(PartnerCalendarEvent, on_delete=models.CASCADE, related_name="rsvps")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="partner_calendar_rsvps")
    status = models.CharField(max_length=16, choices=PartnerCalendarRsvpStatus.choices, default=PartnerCalendarRsvpStatus.GOING)
    responded_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_calendar_rsvp"
        unique_together = [("event", "user")]


class SupportTicketStatus(models.TextChoices):
    OPEN = "open", "Open"
    IN_PROGRESS = "in_progress", "In progress"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


class SupportTicketPriority(models.TextChoices):
    LOW = "low", "Low"
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"
    URGENT = "urgent", "Urgent"


class SupportTicket(models.Model):
    """General Tools > Support Inbox + Helpdesk — one model covering
    both catalog keys, since a helpdesk ticket routed to a department IS
    the support inbox item; there's no meaningful product distinction
    between the two catalog entries. Named SupportTicket (not Ticket) to
    avoid confusion with apps.events.Ticket, an unrelated paid-event-seat
    model. Replies live in SupportTicketReply below rather than routing
    through apps.chat.Conversation (the pattern PartnerPost uses) — that
    path requires the separate NestJS/Mongo chat service; a ticket reply
    thread is simple enough to keep fully in Django/Postgres."""

    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name="support_tickets")
    requester = models.ForeignKey(User, on_delete=models.CASCADE, related_name="submitted_support_tickets")
    subject = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=SupportTicketStatus.choices, default=SupportTicketStatus.OPEN)
    priority = models.CharField(max_length=16, choices=SupportTicketPriority.choices, default=SupportTicketPriority.NORMAL)
    department = models.ForeignKey(
        PartnerDepartment, on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets",
    )
    assignee = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_support_tickets",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_support_ticket"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["partner", "status"]),
            models.Index(fields=["partner", "assignee"]),
        ]


class SupportTicketReply(models.Model):
    id = models.BigAutoField(primary_key=True)
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="replies")
    author = models.ForeignKey(User, on_delete=models.PROTECT, related_name="support_ticket_replies")
    body = models.TextField()
    is_internal_note = models.BooleanField(
        default=False, help_text="Visible to admins/assignees only, hidden from the requester.",
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "partner_support_ticket_reply"
        ordering = ["created_at"]
        indexes = [models.Index(fields=["ticket", "created_at"])]


class PartnerPostTemplate(models.Model):
    """General Tools > Templates — reusable canned text admins can pull
    into the feed/broadcast composer instead of retyping common
    announcements (closures, welcome messages, event reminders, ...)."""

    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name="post_templates")
    title = models.CharField(max_length=120)
    body = models.TextField()
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_post_template"
        ordering = ["title"]
        indexes = [models.Index(fields=["partner"])]


class PartnerSurveyStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"


class PartnerSurveyQuestionType(models.TextChoices):
    SINGLE_CHOICE = "single_choice", "Single choice"
    MULTIPLE_CHOICE = "multiple_choice", "Multiple choice"
    RATING = "rating", "Rating (1-5)"
    TEXT = "text", "Open text"


class PartnerSurvey(models.Model):
    """General Tools > Feedback Hub + Surveys — one model covering both
    catalog keys, same rationale as SupportTicket unifying support_inbox
    and helpdesk: a structured-feedback survey IS a poll, there's no real
    product distinction. Deliberately not built on apps.surveys, a global
    social/gamified poll system (owner_id-only "ownership", no partner
    FK, no membership-aware permissions) — see SupportTicket/
    PartnerCalendarEvent docstrings for the same reasoning applied here."""

    id = models.BigAutoField(primary_key=True)
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name="surveys")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=PartnerSurveyStatus.choices, default=PartnerSurveyStatus.DRAFT)
    is_anonymous = models.BooleanField(default=False)
    closes_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partner_survey"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["partner", "status"])]


class PartnerSurveyQuestion(models.Model):
    id = models.BigAutoField(primary_key=True)
    survey = models.ForeignKey(PartnerSurvey, on_delete=models.CASCADE, related_name="questions")
    text = models.CharField(max_length=500)
    question_type = models.CharField(max_length=20, choices=PartnerSurveyQuestionType.choices)
    options = models.JSONField(
        default=list, blank=True,
        help_text="For single_choice/multiple_choice: [{'id': 'opt1', 'label': 'Yes'}, ...].",
    )
    required = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "partner_survey_question"
        ordering = ["order"]
        indexes = [models.Index(fields=["survey", "order"])]


class PartnerSurveyResponse(models.Model):
    id = models.BigAutoField(primary_key=True)
    survey = models.ForeignKey(PartnerSurvey, on_delete=models.CASCADE, related_name="responses")
    respondent = models.ForeignKey(User, on_delete=models.CASCADE, related_name="partner_survey_responses")
    submitted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "partner_survey_response"
        unique_together = [("survey", "respondent")]


class PartnerSurveyAnswer(models.Model):
    id = models.BigAutoField(primary_key=True)
    response = models.ForeignKey(PartnerSurveyResponse, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(PartnerSurveyQuestion, on_delete=models.CASCADE, related_name="answers")
    value = models.JSONField(
        help_text="Shape depends on question_type: {'choice_id': 'opt1'} | "
        "{'choice_ids': ['opt1','opt2']} | {'value': 4} | {'text': '...'}.",
    )

    class Meta:
        db_table = "partner_survey_answer"
        unique_together = [("response", "question")]
        indexes = [models.Index(fields=["question"])]
