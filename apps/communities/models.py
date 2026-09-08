# apps/communities/models.py
import uuid
from django.db import models
from django.utils import timezone

from apps.accounts.models import User
from apps.chat.models import Conversation
from apps.chat.models import Conversation
from common.media_urls import normalize_image_payload


class CommunityVisibility(models.TextChoices):
    PUBLIC = "public", "Public"
    PRIVATE = "private", "Private"
    HIDDEN = "hidden", "Hidden"


class CommunityJoinPolicy(models.TextChoices):
    OPEN = "open", "Open"
    REQUEST = "request", "Request approval"
    INVITE_ONLY = "invite_only", "Invite only"


class CommunityPostPolicy(models.TextChoices):
    ALL_MEMBERS = "all_members", "All members"
    ADMINS_ONLY = "admins_only", "Admins only"
    MODS_ONLY = "mods_only", "Moderators and admins"


class CommunityRole(models.TextChoices):
    OWNER = "owner", "Owner"
    ADMIN = "admin", "Admin"
    MOD = "mod", "Moderator"
    MEMBER = "member", "Member"


class Community(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    partner = models.ForeignKey(
        "partners.Partner",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="communities",
    )

    owner = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="communities_owned",
    )

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    avatar_url = models.URLField(blank=True)

    is_active = models.BooleanField(default=True)

    visibility = models.CharField(
        max_length=16,
        choices=CommunityVisibility.choices,
        default=CommunityVisibility.PUBLIC,
        db_index=True,
    )
    join_policy = models.CharField(
        max_length=16,
        choices=CommunityJoinPolicy.choices,
        default=CommunityJoinPolicy.REQUEST,
        db_index=True,
    )
    post_policy = models.CharField(
        max_length=16,
        choices=CommunityPostPolicy.choices,
        default=CommunityPostPolicy.ALL_MEMBERS,
    )
    allow_comments = models.BooleanField(default=True)
    allow_reactions = models.BooleanField(default=True)
    allow_media = models.BooleanField(default=True)
    allow_polls = models.BooleanField(default=True)
    allow_events = models.BooleanField(default=True)
    allow_links = models.BooleanField(default=True)
    allow_broadcasts = models.BooleanField(
        default=True,
        help_text="When false, community posts cannot be promoted to broadcasts.",
    )
    require_post_approval = models.BooleanField(default=False)
    allow_post_link_copy = models.BooleanField(
        default=True,
        help_text="If false, only privileged roles can copy post permalinks.",
    )
    allow_join_link = models.BooleanField(
        default=True,
        help_text="If false, join links are disabled; joins must go through approval.",
    )
    invite_token = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text="Short random token used to build a shareable invite link.",
    )
    require_join_survey = models.BooleanField(
        default=False,
        help_text="When true, new joiners must complete a survey/workflow before being approved.",
    )

    # existing main chat / lobby
    main_conversation = models.OneToOneField(
        Conversation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="community_main",
    )

    # 🔥 NEW: posts / feed conversation for the community
    posts_conversation = models.OneToOneField(
        Conversation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="community_posts",
    )

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "kis_community"  # or whatever you already use

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.avatar_url = normalize_image_payload(self.avatar_url)
        super().save(*args, **kwargs)


class CommunityMembershipStatus(models.TextChoices):
    """
    The single source of truth for whether a membership row currently
    grants access. Every membership check in this app must filter on this
    field — not on left_at/is_banned directly — see
    CommunityMembershipQuerySet.active() below.

    ACTIVE  — full participant, currently in the community.
    LEFT    — the user left voluntarily. May rejoin depending on the
              community's join_policy (same rules as a first-time joiner).
    REMOVED — an admin/mod removed them. Not permanently banned — future
              rejoining follows the same join_policy as anyone else,
              exactly like LEFT, but recorded distinctly for audit/UX
              ("you were removed by an admin" vs "you left").
    BANNED  — explicitly prohibited from joining or participating in any
              way until an admin unbans them. Overrides join_policy
              entirely: no join, no invite link, no join request, no
              re-approval can lift this — only an explicit unban.
    """

    ACTIVE = "active", "Active"
    LEFT = "left", "Left"
    REMOVED = "removed", "Removed"
    BANNED = "banned", "Banned"


class CommunityMembershipQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status=CommunityMembershipStatus.ACTIVE)

    def banned(self):
        return self.filter(status=CommunityMembershipStatus.BANNED)


class CommunityMembership(models.Model):
    id = models.BigAutoField(primary_key=True)
    community = models.ForeignKey(
        Community,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="community_memberships",
    )
    role = models.CharField(
        max_length=16,
        choices=CommunityRole.choices,
        default=CommunityRole.MEMBER,
    )
    status = models.CharField(
        max_length=16,
        choices=CommunityMembershipStatus.choices,
        default=CommunityMembershipStatus.ACTIVE,
        db_index=True,
        help_text="Single source of truth for membership state - see CommunityMembershipStatus.",
    )
    can_access_all_groups = models.BooleanField(
        default=False,
        help_text="If true, member can access all groups in the community.",
    )
    joined_at = models.DateTimeField(default=timezone.now)
    left_at = models.DateTimeField(null=True, blank=True)
    is_muted = models.BooleanField(default=False)
    # is_banned is kept as a derived, write-through mirror of
    # status == BANNED for any external/legacy code (admin filters,
    # reports, other apps) that still queries it directly - status is the
    # only field new code should branch on. Always kept in sync by the
    # mark_*()/reactivate() methods below; never set directly elsewhere.
    is_banned = models.BooleanField(default=False)
    lesson_access_only = models.BooleanField(
        default=False,
        help_text="Only enrolled for lesson-focused access.",
    )

    objects = CommunityMembershipQuerySet.as_manager()

    class Meta:
        db_table = "community_membership"
        unique_together = [("community", "user")]
        indexes = [
            models.Index(fields=["community", "user"]),
            models.Index(fields=["user", "joined_at"]),
            models.Index(fields=["community", "status"]),
        ]

    @property
    def is_active(self) -> bool:
        return self.status == CommunityMembershipStatus.ACTIVE

    def reactivate(self, *, role: str | None = None) -> None:
        """Transition to ACTIVE from any prior state (join/rejoin/approve/unban)."""
        self.status = CommunityMembershipStatus.ACTIVE
        self.left_at = None
        self.is_banned = False
        if role is not None:
            self.role = role
        self.joined_at = timezone.now()
        self.save(update_fields=["status", "left_at", "is_banned", "role", "joined_at"])

    def mark_left(self) -> None:
        self.status = CommunityMembershipStatus.LEFT
        self.left_at = timezone.now()
        self.save(update_fields=["status", "left_at"])

    def mark_removed(self) -> None:
        self.status = CommunityMembershipStatus.REMOVED
        self.left_at = timezone.now()
        self.save(update_fields=["status", "left_at"])

    def mark_banned(self) -> None:
        self.status = CommunityMembershipStatus.BANNED
        self.left_at = timezone.now()
        self.is_banned = True
        self.save(update_fields=["status", "left_at", "is_banned"])


class CommunityJoinRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class CommunityJoinRequest(models.Model):
    id = models.BigAutoField(primary_key=True)
    community = models.ForeignKey(
        Community,
        on_delete=models.CASCADE,
        related_name="join_requests",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="community_join_requests",
    )
    message = models.CharField(max_length=500, blank=True)
    status = models.CharField(
        max_length=16,
        choices=CommunityJoinRequestStatus.choices,
        default=CommunityJoinRequestStatus.PENDING,
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="community_join_requests_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "community_join_request"
        unique_together = [("community", "user")]
        indexes = [
            models.Index(fields=["community", "status"]),
        ]


class CommunityBan(models.Model):
    id = models.BigAutoField(primary_key=True)
    community = models.ForeignKey(
        Community,
        on_delete=models.CASCADE,
        related_name="bans",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="community_bans",
    )
    reason = models.CharField(max_length=500, blank=True)
    banned_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="community_bans_made",
    )
    banned_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "community_ban"
        unique_together = [("community", "user")]
        indexes = [
            models.Index(fields=["community", "user"]),
        ]


class CommunityPostStatus(models.TextChoices):
    PUBLISHED = "published", "Published"
    PENDING = "pending", "Pending approval"
    REJECTED = "rejected", "Rejected"


class CommunityPost(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    community = models.ForeignKey(
        Community,
        on_delete=models.CASCADE,
        related_name="posts",
    )
    author = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="community_posts",
    )
    text = models.JSONField(
        default=dict,
        blank=True,
        help_text="ProseMirror-style document for the post content.",
    )
    text_plain = models.TextField(
        blank=True,
        help_text="Plain text fallback (used for previews/search).",
    )
    text_preview = models.CharField(
        max_length=512,
        blank=True,
        help_text="Short preview extracted from the rich document.",
    )
    attachments = models.JSONField(default=list, blank=True)
    poll = models.JSONField(default=dict, blank=True)
    event = models.JSONField(default=dict, blank=True)
    link = models.URLField(blank=True)
    status = models.CharField(
        max_length=16,
        choices=CommunityPostStatus.choices,
        default=CommunityPostStatus.PUBLISHED,
        db_index=True,
    )
    is_pinned = models.BooleanField(default=False)
    is_broadcast = models.BooleanField(default=False)
    comment_conversation = models.ForeignKey(
        Conversation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="community_post_comments",
    )
    pinned_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="community_posts_pinned",
    )
    pinned_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "community_post"
        indexes = [
            models.Index(fields=["community", "created_at"]),
            models.Index(fields=["community", "status"]),
            models.Index(fields=["community", "is_pinned"]),
        ]


class CommunityPostComment(models.Model):
    id = models.BigAutoField(primary_key=True)
    post = models.ForeignKey(
        CommunityPost,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    author = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="community_post_comments",
    )
    text = models.TextField()
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "community_post_comment"
        indexes = [
            models.Index(fields=["post", "created_at"]),
        ]


class CommunityPostReaction(models.Model):
    id = models.BigAutoField(primary_key=True)
    post = models.ForeignKey(
        CommunityPost,
        on_delete=models.CASCADE,
        related_name="reactions",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="community_post_reactions",
    )
    emoji = models.CharField(max_length=32)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "community_post_reaction"
        unique_together = [("post", "user")]
        indexes = [
            models.Index(fields=["post", "created_at"]),
        ]


class CommunityCommentReaction(models.Model):
    id = models.BigAutoField(primary_key=True)
    comment = models.ForeignKey(
        CommunityPostComment,
        on_delete=models.CASCADE,
        related_name="reactions",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="community_comment_reactions",
    )
    emoji = models.CharField(max_length=32)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "community_comment_reaction"
        unique_together = [("comment", "user")]
        indexes = [
            models.Index(fields=["comment", "created_at"]),
        ]
