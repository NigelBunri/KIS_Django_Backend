import uuid
from decimal import Decimal
from typing import Any, Iterable, Optional, Set, List, Dict

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.db.models import F, Q, UniqueConstraint
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError

# Use JSONField from Django (3.1+)
JSONField = models.JSONField


# ---------------------------
# Base entity (audit fields)
# ---------------------------
class BaseEntity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# ---------------------------
# Permission, Role, RoleAssignment, ACE (scoped)
# ---------------------------
class Permission(BaseEntity):
    """
    Canonical permission codename (e.g. "group.post", "group.invite", "channel.create_thread").
    Keep a unique codename and optional description for admin UIs.
    """
    codename = models.CharField(max_length=128, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "core_permission"
        ordering = ("codename",)

    def __str__(self):
        return self.codename


class Role(BaseEntity):
    """
    Role is a named collection of permissions. Roles can be global or scoped by an intended
    'scope' string (e.g., "GROUP", "COMMUNITY", "CHANNEL") to clarify usage.
    """
    SCOPE_GROUP = "GROUP"
    SCOPE_COMMUNITY = "COMMUNITY"
    SCOPE_CHANNEL = "CHANNEL"
    SCOPE_GLOBAL = "GLOBAL"

    SCOPE_CHOICES = [
        (SCOPE_GLOBAL, "Global"),
        (SCOPE_COMMUNITY, "Community"),
        (SCOPE_GROUP, "Group"),
        (SCOPE_CHANNEL, "Channel"),
    ]

    name = models.CharField(max_length=128)
    scope = models.CharField(max_length=32, choices=SCOPE_CHOICES, default=SCOPE_GLOBAL)
    description = models.TextField(blank=True)
    permissions = models.ManyToManyField(Permission, blank=True, related_name="roles")
    parent_role = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="child_roles")
    is_default = models.BooleanField(default=False, help_text="Auto-assigned role for new members in a scope")

    class Meta:
        db_table = "core_role"
        unique_together = (("name", "scope"),)

    def __str__(self):
        return f"{self.scope}:{self.name}"

    def clean(self):
        # prevent cycles in parent_role
        p = self.parent_role
        visited = set()
        while p:
            if p.id == self.id:
                raise ValidationError("Role parent chain would create a cycle.")
            if p.id in visited:
                break
            visited.add(p.id)
            p = p.parent_role

    def all_permissions(self) -> Set[str]:
        """
        Return a set of permission codenames from this role and its parent chain.
        """
        perms: Set[str] = set()
        node = self
        visited = set()
        while node and node.id not in visited:
            visited.add(node.id)
            perms.update(node.permissions.values_list("codename", flat=True))
            node = node.parent_role
        return perms


class RoleAssignment(BaseEntity):
    """
    Assign a Role to a principal (user, team, service account, etc). Can be optionally
    scoped to a target object (Community/Group/Channel) via GenericForeignKey.
    - principal_content_type / principal_object_id : who gets the role
    - target_content_type / target_object_id : optional object the role applies to
    """
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="assignments")

    principal_content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    principal_object_id = models.CharField(max_length=255)
    principal = GenericForeignKey("principal_content_type", "principal_object_id")

    target_content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    target_object_id = models.CharField(max_length=255, null=True, blank=True)
    target = GenericForeignKey("target_content_type", "target_object_id")

    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "core_role_assignment"
        indexes = [
            models.Index(fields=["principal_content_type", "principal_object_id"]),
            models.Index(fields=["target_content_type", "target_object_id"]),
        ]

    def is_active(self) -> bool:
        return not (self.expires_at and timezone.now() > self.expires_at)


class AccessControlEntry(BaseEntity):
    """
    Fine-grained allow/deny (ACE) for a principal on a target object.
    - principal: user/role/team/public (represented via ContentType or NULL for PUBLIC)
    - target: any object (Community/Group/Channel) or NULL for global
    - permissions: list of permission codenames.
    Deny entries override allows when evaluating permissions.
    """
    EFFECT_ALLOW = "ALLOW"
    EFFECT_DENY = "DENY"
    EFFECT_CHOICES = [(EFFECT_ALLOW, "Allow"), (EFFECT_DENY, "Deny")]

    principal_content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    principal_object_id = models.CharField(max_length=255, null=True, blank=True)
    principal = GenericForeignKey("principal_content_type", "principal_object_id")

    target_content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    target_object_id = models.CharField(max_length=255, null=True, blank=True)
    target = GenericForeignKey("target_content_type", "target_object_id")

    permissions = JSONField(default=list)  # list[str]
    effect = models.CharField(max_length=8, choices=EFFECT_CHOICES, default=EFFECT_ALLOW)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "core_ace"
        indexes = [
            models.Index(fields=["principal_content_type", "principal_object_id"]),
            models.Index(fields=["target_content_type", "target_object_id"]),
        ]

    def clean(self):
        if not isinstance(self.permissions, list):
            raise ValidationError("permissions must be a list of codename strings.")
        self.permissions = sorted(set(map(str, self.permissions)))

    def is_active(self) -> bool:
        return not (self.expires_at and timezone.now() > self.expires_at)


# ---------------------------
# Core domain models: Community, Group, Channel
# ---------------------------
class Community(BaseEntity):
    """
    A Community is a top-level collection which may contain multiple Groups.
    Visibility controls who can discover/join.
    """
    VISIBILITY_PUBLIC = "PUBLIC"
    VISIBILITY_PRIVATE = "PRIVATE"
    VISIBILITY_HIDDEN = "HIDDEN"  # discoverable only by invite or direct link

    VISIBILITY_CHOICES = [
        (VISIBILITY_PUBLIC, "Public"),
        (VISIBILITY_PRIVATE, "Private"),
        (VISIBILITY_HIDDEN, "Hidden"),
    ]

    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    owner_content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    owner_object_id = models.CharField(max_length=255, null=True, blank=True)
    owner = GenericForeignKey("owner_content_type", "owner_object_id")  # optional owner (user or org)
    visibility = models.CharField(max_length=16, choices=VISIBILITY_CHOICES, default=VISIBILITY_PUBLIC)
    metadata = JSONField(default=dict, blank=True)  # arbitrary metadata
    archived = models.BooleanField(default=False, help_text="If archived, new activity is restricted.")

    class Meta:
        db_table = "core_community"
        indexes = [models.Index(fields=["slug"]), models.Index(fields=["archived"])]

    def __str__(self):
        return self.name

    def active_groups(self):
        return self.groups.filter(archived=False)

    def add_group(self, group):
        group.community = self
        group.save()


class Group(BaseEntity):
    """
    Group may exist standalone or inside a Community (community nullable).
    It represents the primary membership & discussion unit.
    """
    slug = models.SlugField(max_length=64)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    community = models.ForeignKey(Community, null=True, blank=True, on_delete=models.CASCADE, related_name="groups")
    is_public = models.BooleanField(default=True, help_text="If false, group is invite-only or request-only depending on membership settings.")
    archived = models.BooleanField(default=False)
    # Basic aggregated counters for fast queries (maintain via signals or code)
    member_count = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_group"
        unique_together = (("community", "slug"),)
        indexes = [models.Index(fields=["community", "slug"]), models.Index(fields=["archived"])]

    def __str__(self):
        return self.name

    def recalc_member_count(self):
        self.member_count = self.memberships.filter(status=Membership.STATUS_ACTIVE).count()
        self.save(update_fields=["member_count"])

    def is_member(self, user) -> bool:
        return self.memberships.filter(user_content_type=ContentType.objects.get_for_model(user.__class__),
                                       user_object_id=str(user.pk),
                                       status=Membership.STATUS_ACTIVE).exists()

    def can_user(self, user, permission_codename: str) -> bool:
        """
        High-level helper: checks whether a user has given permission on this Group.
        This checks:
         - role assignments that target this group
         - ACEs targeted to this group (deny overrides)
         - membership-specific role/permissions
         - member is owner/superuser
        """
        # quick superuser check
        if getattr(user, "is_superuser", False):
            return True

        # 1) membership role checks
        try:
            m = self.memberships.get(user_content_type=ContentType.objects.get_for_model(user.__class__),
                                     user_object_id=str(user.pk),
                                     status=Membership.STATUS_ACTIVE)
        except Membership.DoesNotExist:
            m = None

        if m:
            # evaluate membership role's permissions if assigned
            if m.role and permission_codename in m.role.all_permissions():
                return True

        # 2) role assignments to user scoped to this group
        role_ct = ContentType.objects.get_for_model(Role)
        user_ct = ContentType.objects.get_for_model(user.__class__)
        assigned_role_ids = RoleAssignment.objects.filter(
            principal_content_type=user_ct,
            principal_object_id=str(user.pk),
            target_content_type=ContentType.objects.get_for_model(self.__class__),
            target_object_id=str(self.pk),
            role__isnull=False,
            expires_at__isnull=True
        ).values_list("role_id", flat=True)
        if assigned_role_ids:
            roles = Role.objects.filter(id__in=assigned_role_ids)
            for r in roles:
                if permission_codename in r.all_permissions():
                    return True

        # 3) ACEs
        now = timezone.now()
        # principal candidates: user, roles assigned to user, public
        principals = []
        principals.append((user_ct, str(user.pk)))
        # include role principals by role assignments to user (global or targetless)
        role_asgs = RoleAssignment.objects.filter(principal_content_type=user_ct, principal_object_id=str(user.pk))
        for ra in role_asgs:
            principals.append((ContentType.objects.get_for_model(Role), str(ra.role_id)))
        principals.append((None, "PUBLIC"))

        # collect ACEs that apply to this group: exact target, type-wide, or global
        ace_q = Q()
        p_q = Q()
        for ct, pid in principals:
            if ct is None:
                p_q |= (Q(principal_content_type__isnull=True) & Q(principal_object_id="PUBLIC"))
            else:
                p_q |= (Q(principal_content_type=ct) & Q(principal_object_id=str(pid)))
        ace_q &= p_q

        ace_q &= (
            Q(target_content_type=ContentType.objects.get_for_model(self.__class__), target_object_id=str(self.pk))
            | (Q(target_content_type=ContentType.objects.get_for_model(self.__class__)) & Q(target_object_id__isnull=True))
            | (Q(target_content_type__isnull=True) & Q(target_object_id__isnull=True))
        )
        ace_q &= (Q(expires_at__isnull=True) | Q(expires_at__gt=now))

        aces = AccessControlEntry.objects.filter(ace_q)
        denies = set()
        allows = set()
        for ace in aces:
            perms = set(ace.permissions or [])
            if ace.effect == AccessControlEntry.EFFECT_DENY:
                denies.update(perms)
            else:
                allows.update(perms)

        if permission_codename in denies:
            return False
        if permission_codename in allows:
            return True

        return False  # default deny


class Channel(BaseEntity):
    """
    Channel: content spaces that can be linked to multiple Communities and/or multiple Groups.
    Channels can be public or private; they are often used for topic-based discussion within groups/communities.
    """
    slug = models.SlugField(max_length=64)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    is_public = models.BooleanField(default=True)
    archived = models.BooleanField(default=False)
    # A channel can belong to many communities and many groups (many-to-many)
    communities = models.ManyToManyField(Community, blank=True, related_name="channels")
    groups = models.ManyToManyField(Group, blank=True, related_name="channels")
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_channel"
        unique_together = (("slug",),)
        indexes = [models.Index(fields=["slug"]), models.Index(fields=["archived"])]

    def __str__(self):
        return self.name

    def can_user(self, user, permission_codename: str) -> bool:
        """
        Composite permission check for channels. A user can gain permission through:
         - membership/role in any linked group/community
         - channel role assignments / ACEs
         - global roles
         - superuser bypass
        Deny ACEs take precedence.
        """
        if getattr(user, "is_superuser", False):
            return True

        # collect principals and check ACEs that target channel specifically
        user_ct = ContentType.objects.get_for_model(user.__class__)
        principals = [(user_ct, str(user.pk))]
        # roles assigned directly to user
        for ra in RoleAssignment.objects.filter(principal_content_type=user_ct, principal_object_id=str(user.pk)):
            principals.append((ContentType.objects.get_for_model(Role), str(ra.role_id)))
        principals.append((None, "PUBLIC"))

        now = timezone.now()
        # ACEs on channel:
        ace_q = Q()
        p_q = Q()
        for ct, pid in principals:
            if ct is None:
                p_q |= (Q(principal_content_type__isnull=True) & Q(principal_object_id="PUBLIC"))
            else:
                p_q |= (Q(principal_content_type=ct) & Q(principal_object_id=str(pid)))
        ace_q &= p_q
        ace_q &= (
            Q(target_content_type=ContentType.objects.get_for_model(self.__class__), target_object_id=str(self.pk))
            | (Q(target_content_type=ContentType.objects.get_for_model(self.__class__)) & Q(target_object_id__isnull=True))
            | (Q(target_content_type__isnull=True) & Q(target_object_id__isnull=True))
        )
        ace_q &= (Q(expires_at__isnull=True) | Q(expires_at__gt=now))

        aces = AccessControlEntry.objects.filter(ace_q)
        denies = set()
        allows = set()
        for ace in aces:
            perms = set(ace.permissions or [])
            if ace.effect == AccessControlEntry.EFFECT_DENY:
                denies.update(perms)
            else:
                allows.update(perms)

        if permission_codename in denies:
            return False
        if permission_codename in allows:
            return True

        # fallback: if the user has the permission in any linked group/community
        # Check group membership roles
        user_ct = ContentType.objects.get_for_model(user.__class__)
        group_qs = self.groups.all()
        for g in group_qs:
            if g.can_user(user, permission_codename):
                return True
        for c in self.communities.all():
            # check community-level role assignments/aces similarly (reuse Group.can_user pattern adapted)
            # simple default: community owner or community-level roles might grant permission
            # For brevity, check RoleAssignment + ACEs for community like Group.can_user implementation
            # (implementation mirrored from Group.can_user if required)
            if CommunityPermissionHelper.can_user_on_community(user, c, permission_codename):
                return True

        return False


# ---------------------------
# Memberships, Invites, Requests, Moderation
# ---------------------------
class Membership(BaseEntity):
    """
    Membership of a User in a Group.
    The user is represented with GenericForeignKey to support different principal types (user, service account).
    Role is optional (e.g., moderator, member).
    """
    STATUS_ACTIVE = "ACTIVE"
    STATUS_PENDING = "PENDING"  # e.g., awaiting approval
    STATUS_INVITED = "INVITED"
    STATUS_BANNED = "BANNED"
    STATUS_LEFT = "LEFT"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_PENDING, "Pending"),
        (STATUS_INVITED, "Invited"),
        (STATUS_BANNED, "Banned"),
        (STATUS_LEFT, "Left"),
    ]

    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="memberships")
    user_content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    user_object_id = models.CharField(max_length=255)
    user = GenericForeignKey("user_content_type", "user_object_id")

    role = models.ForeignKey(Role, null=True, blank=True, on_delete=models.SET_NULL, related_name="memberships")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    joined_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)  # for temporary memberships
    is_moderator = models.BooleanField(default=False)
    preferences = JSONField(default=dict, blank=True)  # per-member settings (notifications, etc)

    class Meta:
        db_table = "core_membership"
        indexes = [
            models.Index(fields=["group", "status"]),
            models.Index(fields=["user_content_type", "user_object_id"]),
        ]
        constraints = [
            UniqueConstraint(fields=["group", "user_content_type", "user_object_id"], name="unique_group_user_membership")
        ]

    def is_active(self) -> bool:
        if self.status != self.STATUS_ACTIVE:
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        return True

    def promote_to_role(self, role: Role):
        self.role = role
        self.is_moderator = "moderator" in role.name.lower() or role.scope == Role.SCOPE_GROUP
        self.save(update_fields=["role", "is_moderator"])

    def demote(self):
        self.role = None
        self.is_moderator = False
        self.save(update_fields=["role", "is_moderator"])


class MembershipInvite(BaseEntity):
    """
    Invite token issued by a group/community for a principal to join.
    """
    token = models.CharField(max_length=128, unique=True)
    group = models.ForeignKey(Group, null=True, blank=True, on_delete=models.CASCADE, related_name="invites")
    community = models.ForeignKey(Community, null=True, blank=True, on_delete=models.CASCADE, related_name="invites")
    created_by_content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_by_object_id = models.CharField(max_length=255, null=True, blank=True)
    created_by = GenericForeignKey("created_by_content_type", "created_by_object_id")
    expires_at = models.DateTimeField(null=True, blank=True)
    max_uses = models.PositiveIntegerField(null=True, blank=True)  # None = unlimited
    uses = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "core_membership_invite"

    def is_valid(self) -> bool:
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        if self.max_uses is not None and self.uses >= self.max_uses:
            return False
        return True

    def use(self):
        if not self.is_valid():
            raise ValidationError("Invite is no longer valid")
        self.uses += 1
        self.save(update_fields=["uses"])


class MembershipRequest(BaseEntity):
    """
    A user requests to join a group (if group is request-only).
    """
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="join_requests")
    user_content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    user_object_id = models.CharField(max_length=255)
    user = GenericForeignKey("user_content_type", "user_object_id")
    message = models.TextField(blank=True)
    reviewed_by_content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_by_object_id = models.CharField(max_length=255, null=True, blank=True)
    reviewed_by = GenericForeignKey("reviewed_by_content_type", "reviewed_by_object_id")
    status = models.CharField(max_length=16, choices=[
        ("PENDING", "Pending"),
        ("APPROVED", "Approved"),
        ("REJECTED", "Rejected")
    ], default="PENDING")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "core_membership_request"
        indexes = [models.Index(fields=["group", "status"])]


class ModerationAction(BaseEntity):
    """
    Records moderation actions (ban, mute, remove post, warn, etc.) applied by moderators/admins.
    """
    ACTION_BAN = "BAN"
    ACTION_UNBAN = "UNBAN"
    ACTION_MUTE = "MUTE"
    ACTION_UNMUTE = "UNMUTE"
    ACTION_WARN = "WARN"
    ACTION_REMOVE = "REMOVE"

    ACTION_CHOICES = [
        (ACTION_BAN, "Ban"),
        (ACTION_UNBAN, "Unban"),
        (ACTION_MUTE, "Mute"),
        (ACTION_UNMUTE, "Unmute"),
        (ACTION_WARN, "Warn"),
        (ACTION_REMOVE, "Remove content"),
    ]

    target_content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    target_object_id = models.CharField(max_length=255)
    target = GenericForeignKey("target_content_type", "target_object_id")

    subject_content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    subject_object_id = models.CharField(max_length=255)
    subject = GenericForeignKey("subject_content_type", "subject_object_id")  # the user being acted upon

    action = models.CharField(max_length=16, choices=ACTION_CHOICES)
    reason = models.TextField(blank=True)
    performed_by_content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    performed_by_object_id = models.CharField(max_length=255, null=True, blank=True)
    performed_by = GenericForeignKey("performed_by_content_type", "performed_by_object_id")
    expires_at = models.DateTimeField(null=True, blank=True)  # e.g., temporary ban/mute

    class Meta:
        db_table = "core_moderation_action"
        indexes = [models.Index(fields=["action"]), models.Index(fields=["target_content_type", "target_object_id"])]


# ---------------------------
# Settings: GroupSettings / ChannelSettings
# ---------------------------
class GroupSettings(BaseEntity):
    """
    Per-group settings controlling membership behavior and messaging restrictions.
    These are separate to make them easy to edit and version independently.
    """
    group = models.OneToOneField(Group, on_delete=models.CASCADE, related_name="settings")
    allow_attachments = models.BooleanField(default=True)
    allowed_mimetypes = JSONField(default=list, blank=True, help_text="If non-empty, only these mimetypes are allowed.")
    message_rate_limit_seconds = models.PositiveIntegerField(default=0, help_text="Minimum seconds between messages (0 = no slow mode)")
    max_message_length = models.PositiveIntegerField(default=10000, validators=[MinValueValidator(1)])
    join_policy = models.CharField(max_length=16, choices=[
        ("OPEN", "Open: anyone can join"),
        ("INVITE", "Invite-only"),
        ("REQUEST", "Request to join (pending approval)")
    ], default="OPEN")
    discoverability = models.CharField(max_length=16, choices=[
        ("VISIBLE", "Visible in search"),
        ("HIDDEN", "Hidden from search")
    ], default="VISIBLE")
    profanity_filter = models.BooleanField(default=False)
    max_attachments_per_message = models.PositiveIntegerField(default=5, validators=[MinValueValidator(0)])

    class Meta:
        db_table = "core_group_settings"


class ChannelSettings(BaseEntity):
    """
    Per-channel settings. Example: topic-only channels can prevent posting of images/files etc.
    """
    channel = models.OneToOneField(Channel, on_delete=models.CASCADE, related_name="settings")
    is_read_only = models.BooleanField(default=False)
    allow_threads = models.BooleanField(default=True)
    allow_attachments = models.BooleanField(default=True)
    allowed_mimetypes = JSONField(default=list, blank=True)
    slow_mode_seconds = models.PositiveIntegerField(default=0)
    max_message_length = models.PositiveIntegerField(default=5000)
    moderation_queue_enabled = models.BooleanField(default=False, help_text="If true, new messages may require moderator approval.")

    class Meta:
        db_table = "core_channel_settings"


# ---------------------------
# Small helpers
# ---------------------------
class CommunityPermissionHelper:
    @staticmethod
    def can_user_on_community(user, community: Community, permission_codename: str) -> bool:
        """
        Permission check for community-level permissions. Mirrors the logic used in Group.can_user.
        This is intentionally compact; extend similarly to Group.can_user if you need more nuance.
        """
        if getattr(user, "is_superuser", False):
            return True

        user_ct = ContentType.objects.get_for_model(user.__class__)
        principals = [(user_ct, str(user.pk))]
        for ra in RoleAssignment.objects.filter(principal_content_type=user_ct, principal_object_id=str(user.pk)):
            principals.append((ContentType.objects.get_for_model(Role), str(ra.role_id)))
        principals.append((None, "PUBLIC"))

        now = timezone.now()
        p_q = Q()
        for ct, pid in principals:
            if ct is None:
                p_q |= (Q(principal_content_type__isnull=True) & Q(principal_object_id="PUBLIC"))
            else:
                p_q |= (Q(principal_content_type=ct) & Q(principal_object_id=str(pid)))

        ace_q = Q()
        ace_q &= p_q
        ace_q &= (
            Q(target_content_type=ContentType.objects.get_for_model(community.__class__), target_object_id=str(community.pk))
            | (Q(target_content_type=ContentType.objects.get_for_model(community.__class__)) & Q(target_object_id__isnull=True))
            | (Q(target_content_type__isnull=True) & Q(target_object_id__isnull=True))
        )
        ace_q &= (Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        aces = AccessControlEntry.objects.filter(ace_q)
        denies = set()
        allows = set()
        for ace in aces:
            perms = set(ace.permissions or [])
            if ace.effect == AccessControlEntry.EFFECT_DENY:
                denies.update(perms)
            else:
                allows.update(perms)

        if permission_codename in denies:
            return False
        if permission_codename in allows:
            return True

        return False


class HealthcareOrganization(BaseEntity):
    """
    Multi-tenant healthcare organization container for hospitals, clinics, labs, pharmacies, diagnostics, and wellness centers.
    """

    TYPE_HOSPITAL = "hospital"
    TYPE_CLINIC = "clinic"
    TYPE_LAB = "laboratory"
    TYPE_PHARMACY = "pharmacy"
    TYPE_DIAGNOSTIC = "diagnostic"
    TYPE_WELLNESS = "wellness"

    TYPE_CHOICES = [
        (TYPE_HOSPITAL, "Hospital"),
        (TYPE_CLINIC, "Clinic"),
        (TYPE_LAB, "Laboratory"),
        (TYPE_PHARMACY, "Pharmacy"),
        (TYPE_DIAGNOSTIC, "Diagnostic"),
        (TYPE_WELLNESS, "Wellness Center"),
    ]

    BASE_REQUIRED_DOCUMENTS = [
        "business_registration_certificate",
        "owner_government_id",
        "proof_of_address",
    ]

    DOCUMENT_REQUIREMENTS = {
        TYPE_CLINIC: [
            "clinic_license",
            "malpractice_insurance",
            "infection_control_policy",
            "incident_reporting_policy",
        ],
        TYPE_HOSPITAL: [
            "hospital_accreditation",
            "fire_safety_cert",
            "biohazard_disposal_agreement",
        ],
        TYPE_LAB: [
            "lab_director_license",
            "calibration_certification",
            "cold_chain_compliance",
        ],
        TYPE_PHARMACY: [
            "pharmacy_license",
            "controlled_drug_license",
        ],
        "telemedicine_provider": ["telemedicine_consent_policy"],
        "emergency_response_unit": ["emergency_escalation_protocol"],
        "ambulance_service": ["vehicle_registration", "paramedic_certifications"],
        "mental_health_center": ["suicide_escalation_protocol", "confidentiality_policy"],
        "insurance_provider": ["insurance_license", "regulatory_approval"],
        "regulatory_body": ["legal_mandate_document"],
        "accreditation_body": ["accreditation_standards"],
    }

    STATUS_ACTIVE = "active"
    STATUS_INACTIVE = "inactive"
    STATUS_SUSPENDED = "suspended"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_INACTIVE, "Inactive"),
        (STATUS_SUSPENDED, "Suspended"),
    ]

    @classmethod
    def required_documents_for_type(cls, org_type: str):
        base = list(cls.BASE_REQUIRED_DOCUMENTS)
        custom = list(cls.DOCUMENT_REQUIREMENTS.get(org_type, []))
        seen = set()
        ordered = []
        for doc in base + custom:
            if doc not in seen:
                seen.add(doc)
                ordered.append(doc)
        return ordered

    ONBOARDING_DRAFT = "draft"
    ONBOARDING_SUBMITTED = "submitted"
    ONBOARDING_UNDER_REVIEW = "under_review"
    ONBOARDING_APPROVED = "approved"
    ONBOARDING_REJECTED = "rejected"

    ONBOARDING_STATUS_CHOICES = [
        (ONBOARDING_DRAFT, "Draft"),
        (ONBOARDING_SUBMITTED, "Submitted"),
        (ONBOARDING_UNDER_REVIEW, "Under Review"),
        (ONBOARDING_APPROVED, "Approved"),
        (ONBOARDING_REJECTED, "Rejected"),
    ]

    tenant_id = models.UUIDField(null=True, blank=True)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=80, unique=True)
    org_type = models.CharField(max_length=24, choices=TYPE_CHOICES, default=TYPE_HOSPITAL)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    region = models.CharField(max_length=64, blank=True)
    metadata = JSONField(default=dict, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="medical_organizations")
    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)
    onboarding_status = models.CharField(max_length=32, choices=ONBOARDING_STATUS_CHOICES, default=ONBOARDING_DRAFT)
    onboarding_metadata = JSONField(default=dict, blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="verified_medical_organizations")
    last_status_updated_at = models.DateTimeField(null=True, blank=True)
    document_expiry = JSONField(default=dict, blank=True)
    compliance_officer = models.CharField(max_length=120, blank=True)
    risk_summary = models.TextField(blank=True)
    security_notes = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_healthcare_organization"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["tenant_id"]),
            models.Index(fields=["org_type", "status"]),
            models.Index(fields=["onboarding_status"], name="core_org_onboarding_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            base = self.name.lower().replace(" ", "-")
            self.slug = base[:80]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.get_org_type_display()})"


class MedicalProfile(BaseEntity):
    KIND_HEALTH = "health"
    KIND_MARKET = "market"
    KIND_EDUCATION = "education"

    KIND_CHOICES = [
        (KIND_HEALTH, "Health Profile"),
        (KIND_MARKET, "Market Profile"),
        (KIND_EDUCATION, "Education Profile"),
    ]

    STATUS_ACTIVE = "active"
    STATUS_PAUSED = "paused"
    STATUS_ARCHIVED = "archived"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_PAUSED, "Paused"),
        (STATUS_ARCHIVED, "Archived"),
    ]

    organization = models.ForeignKey(HealthcareOrganization, on_delete=models.CASCADE, related_name="profiles")
    profile_type = models.CharField(max_length=32, choices=KIND_CHOICES, default=KIND_HEALTH)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=80, unique=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    metadata = JSONField(default=dict, blank=True)
    location = JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_medical_profiles")
    onboarding_status = models.CharField(max_length=32, choices=HealthcareOrganization.ONBOARDING_STATUS_CHOICES, default=HealthcareOrganization.ONBOARDING_DRAFT)
    compliance_documents_metadata = JSONField(default=dict, blank=True)
    audit_entries = JSONField(default=list, blank=True)
    review_notes = models.TextField(blank=True)

    class Meta:
        db_table = "core_medical_profile"
        unique_together = (("organization", "slug"),)
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.slug:
            base = self.name.lower().replace(" ", "-")
            self.slug = f"{base[:60]}-{uuid.uuid4().hex[:6]}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.get_profile_type_display()})"


class Location(BaseEntity):
    organization = models.ForeignKey(
        HealthcareOrganization, on_delete=models.CASCADE, related_name="locations"
    )
    profile = models.ForeignKey(
        MedicalProfile, null=True, blank=True, on_delete=models.CASCADE, related_name="locations"
    )
    label = models.CharField(max_length=160)
    address = JSONField(default=dict, blank=True)
    is_primary = models.BooleanField(default=False)
    timezone = models.CharField(max_length=64, blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_location"
        ordering = ["label"]
        indexes = [
            models.Index(
                fields=["organization", "profile"],
                name="core_location_org_profile_idx",
            ),
        ]

    def __str__(self):
        return f"{self.label} ({self.organization.name})"


class Ward(BaseEntity):
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name="wards")
    name = models.CharField(max_length=120)
    capacity = models.PositiveIntegerField(default=0)
    is_isolation = models.BooleanField(default=False)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_ward"
        unique_together = (("location", "name"),)
        ordering = ["name"]

    def __str__(self):
        return f"{self.location.label} - {self.name}"
 
class Equipment(BaseEntity):
    STATUS_ACTIVE = "active"
    STATUS_MAINTENANCE = "maintenance"
    STATUS_OFFLINE = "offline"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_MAINTENANCE, "Maintenance"),
        (STATUS_OFFLINE, "Offline"),
    ]

    profile = models.ForeignKey(
        MedicalProfile, on_delete=models.CASCADE, related_name="equipment"
    )
    ward = models.ForeignKey(Ward, null=True, blank=True, on_delete=models.SET_NULL, related_name="equipment")
    name = models.CharField(max_length=140)
    equipment_type = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    last_service_at = models.DateTimeField(null=True, blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_equipment"
        unique_together = (("profile", "name"),)
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"


class Department(BaseEntity):
    profile = models.ForeignKey(MedicalProfile, on_delete=models.CASCADE, related_name="departments")
    name = models.CharField(max_length=120)
    is_ward = models.BooleanField(default=False)
    services = JSONField(default=list, blank=True)

    class Meta:
        db_table = "core_medical_department"
        unique_together = (("profile", "name"),)
        ordering = ["name"]

    def __str__(self):
        return f"{self.profile.name} - {self.name}"


class Service(BaseEntity):
    profile = models.ForeignKey(MedicalProfile, on_delete=models.CASCADE, related_name="services")
    department = models.ForeignKey(
        Department,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="department_services",
        related_query_name="department_service",
    )
    name = models.CharField(max_length=140)
    category = models.CharField(max_length=80, blank=True)
    description = models.TextField(blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_service"
        unique_together = (("profile", "name"),)
        ordering = ["name"]

    def __str__(self):
        return f"{self.profile.name} - {self.name}"


class StaffProfile(BaseEntity):
    profile = models.ForeignKey(MedicalProfile, on_delete=models.CASCADE, related_name="staff_profiles")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="medical_staff_profiles")
    role = models.CharField(max_length=64)
    scope = JSONField(default=dict, blank=True)
    permissions = JSONField(default=list, blank=True)
    licenses = JSONField(default=list, blank=True)
    shifts = JSONField(default=list, blank=True)
    is_on_call = models.BooleanField(default=False)

    class Meta:
        db_table = "core_medical_staff"
        indexes = [
            models.Index(fields=["profile", "role"]),
            models.Index(fields=["is_on_call"]),
        ]

    def __str__(self):
        if self.user:
            name = self.user.get_full_name()
        else:
            name = self.role
        return f"{self.profile.name} - {name}"


class StaffAuditLog(BaseEntity):
    staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="audits")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="staff_audits",
    )
    action = models.CharField(max_length=80)
    metadata = JSONField(default=dict, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        db_table = "core_staff_audit_log"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} on {self.staff}"


class ComplianceAuditLog(BaseEntity):
    SEVERITY_LOW = "low"
    SEVERITY_MEDIUM = "medium"
    SEVERITY_HIGH = "high"
    SEVERITY_CRITICAL = "critical"

    SEVERITY_CHOICES = [
        (SEVERITY_LOW, "Low"),
        (SEVERITY_MEDIUM, "Medium"),
        (SEVERITY_HIGH, "High"),
        (SEVERITY_CRITICAL, "Critical"),
    ]

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="compliance_audit_logs",
    )
    action = models.CharField(max_length=200)
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES, default=SEVERITY_MEDIUM)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_compliance_audit_log"
        indexes = [
            models.Index(fields=["actor", "action"]),
            models.Index(fields=["severity"]),
        ]

    @classmethod
    def log(cls, actor, action: str, target_type: str = "", target_id: str = "", severity: str = SEVERITY_MEDIUM, metadata: Optional[Dict[str, Any]] = None):
        return cls.objects.create(
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            action=action,
            target_type=target_type,
            target_id=target_id,
            severity=severity,
            metadata=metadata or {},
        )

    def __str__(self):
        return f"{self.action} ({self.severity})"


class CredentialVerification(BaseEntity):
    STATUS_PENDING = "pending"
    STATUS_VERIFIED = "verified"
    STATUS_EXPIRED = "expired"
    STATUS_REVOKED = "revoked"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_VERIFIED, "Verified"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_REVOKED, "Revoked"),
    ]

    staff_profile = models.ForeignKey(
        StaffProfile,
        on_delete=models.CASCADE,
        related_name="credentials",
    )
    credential_type = models.CharField(max_length=120)
    license_number = models.CharField(max_length=120)
    issuing_body = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    issued_at = models.DateField(null=True, blank=True)
    expires_at = models.DateField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="verified_credentials",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_credential_verification"
        ordering = ["-expires_at"]
        unique_together = (("staff_profile", "credential_type", "license_number"),)

    def mark_verified(self, actor=None):
        self.status = self.STATUS_VERIFIED
        self.verified_by = actor
        self.verified_at = timezone.now()
        self.save(update_fields=["status", "verified_by", "verified_at"])

    def __str__(self):
        return f"{self.staff_profile} · {self.credential_type} ({self.status})"


class RegulatoryReport(BaseEntity):
    STATUS_DRAFT = "draft"
    STATUS_SUBMITTED = "submitted"
    STATUS_REVIEWED = "reviewed"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_REVIEWED, "Reviewed"),
    ]

    report_type = models.CharField(max_length=120)
    profile = models.ForeignKey(MedicalProfile, on_delete=models.CASCADE, related_name="regulatory_reports")
    organization = models.ForeignKey(HealthcareOrganization, on_delete=models.CASCADE, related_name="regulatory_reports")
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    data_payload = JSONField(default=dict, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_regulatory_report"
        ordering = ["-created_at"]

    def submit(self):
        self.status = self.STATUS_SUBMITTED
        self.submitted_at = timezone.now()
        self.save(update_fields=["status", "submitted_at"])

    def __str__(self):
        return f"{self.report_type} ({self.status})"


class ComplianceDocument(BaseEntity):
    STATUS_DRAFT = "draft"
    STATUS_SIGNED = "signed"
    STATUS_ARCHIVED = "archived"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SIGNED, "Signed"),
        (STATUS_ARCHIVED, "Archived"),
    ]

    profile = models.ForeignKey(MedicalProfile, on_delete=models.CASCADE, related_name="compliance_document_records")
    organization = models.ForeignKey(HealthcareOrganization, on_delete=models.CASCADE, related_name="compliance_document_records")
    document_name = models.CharField(max_length=160)
    file_path = models.CharField(max_length=400, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    is_signed = models.BooleanField(default=False)
    signed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="signed_documents",
    )
    signed_at = models.DateTimeField(null=True, blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_compliance_document"
        ordering = ["-created_at"]

    def mark_signed(self, actor):
        self.is_signed = True
        self.status = self.STATUS_SIGNED
        self.signed_by = actor
        self.signed_at = timezone.now()
        self.save(update_fields=["is_signed", "status", "signed_by", "signed_at"])

    def __str__(self):
        return f"{self.document_name} ({self.status})"
    


class PatientMasterRecord(BaseEntity):
    """Master patient index entry with family/consent metadata."""

    STATUS_ACTIVE = "active"
    STATUS_INACTIVE = "inactive"
    STATUS_DECEASED = "deceased"

    GENDER_UNKNOWN = "unknown"
    GENDER_MALE = "male"
    GENDER_FEMALE = "female"
    GENDER_OTHER = "other"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_INACTIVE, "Inactive"),
        (STATUS_DECEASED, "Deceased"),
    ]

    GENDER_CHOICES = [
        (GENDER_UNKNOWN, "Unknown"),
        (GENDER_MALE, "Male"),
        (GENDER_FEMALE, "Female"),
        (GENDER_OTHER, "Other"),
    ]

    tenant_id = models.UUIDField(null=True, blank=True)
    mrn = models.CharField(max_length=32, unique=True)
    first_name = models.CharField(max_length=64)
    last_name = models.CharField(max_length=64)
    dob = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=16, choices=GENDER_CHOICES, default=GENDER_UNKNOWN)
    primary_contact = JSONField(default=dict, blank=True)
    emergency_contact = JSONField(default=dict, blank=True)
    family = JSONField(default=list, blank=True)
    metadata = JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    organization = models.ForeignKey(HealthcareOrganization, null=True, blank=True, on_delete=models.SET_NULL, related_name="patients")

    class Meta:
        db_table = "core_patient_master"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant_id"]),
            models.Index(fields=["mrn"]),
        ]

    def __str__(self):
        return f"{self.last_name}, {self.first_name} ({self.mrn})"


class DataAccessConsent(BaseEntity):
    STATUS_ACTIVE = "active"
    STATUS_REVOKED = "revoked"
    STATUS_EXPIRED = "expired"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_REVOKED, "Revoked"),
        (STATUS_EXPIRED, "Expired"),
    ]

    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="data_access_consents")
    granted_to = models.CharField(max_length=128)
    scope = models.CharField(max_length=200)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    expires_at = models.DateTimeField(null=True, blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_data_access_consent"
        ordering = ["-created_at"]

    def revoke(self):
        self.status = self.STATUS_REVOKED
        self.save(update_fields=["status"])

    def __str__(self):
        return f"{self.patient} · {self.scope} ({self.status})"


class HealthDataAccessGrant(BaseEntity):
    ROLE_CAREGIVER = "caregiver"
    ROLE_FAMILY = "family"
    ROLE_GUARDIAN = "guardian"
    ROLE_CLINICIAN = "clinician"

    STATUS_ACTIVE = "active"
    STATUS_REVOKED = "revoked"
    STATUS_EXPIRED = "expired"
    STATUS_PENDING = "pending"

    SCOPE_SUMMARY = "summary"
    SCOPE_EMERGENCY = "emergency"
    SCOPE_RECORDS = "records"
    SCOPE_FULL = "full"

    ROLE_CHOICES = [
        (ROLE_CAREGIVER, "Caregiver"),
        (ROLE_FAMILY, "Family"),
        (ROLE_GUARDIAN, "Guardian"),
        (ROLE_CLINICIAN, "Clinician"),
    ]

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_REVOKED, "Revoked"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_PENDING, "Pending"),
    ]

    SCOPE_CHOICES = [
        (SCOPE_SUMMARY, "Summary"),
        (SCOPE_EMERGENCY, "Emergency"),
        (SCOPE_RECORDS, "Records"),
        (SCOPE_FULL, "Full"),
    ]

    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="access_grants")
    granted_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="health_data_access_grants",
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="granted_health_data_access",
    )
    role = models.CharField(max_length=24, choices=ROLE_CHOICES, default=ROLE_CAREGIVER)
    scope = models.CharField(max_length=24, choices=SCOPE_CHOICES, default=SCOPE_SUMMARY)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    expires_at = models.DateTimeField(null=True, blank=True)
    allow_emergency_override = models.BooleanField(default=False)
    note = models.TextField(blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_health_data_access_grant"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["patient", "status"]),
            models.Index(fields=["granted_to", "status"]),
            models.Index(fields=["expires_at"]),
        ]
        constraints = [
            UniqueConstraint(
                fields=["patient", "granted_to", "role", "scope"],
                name="core_health_access_grant_unique_active_scope",
            ),
        ]

    def is_active(self) -> bool:
        if self.status != self.STATUS_ACTIVE:
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        return True

    def revoke(self):
        self.status = self.STATUS_REVOKED
        self.save(update_fields=["status", "updated_at"])

    def __str__(self):
        return f"{self.patient} -> {self.granted_to} ({self.scope})"


class MedicationOrder(BaseEntity):
    STATUS_REQUESTED = "requested"
    STATUS_ACTIVE = "active"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_REQUESTED, "Requested"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="medications")
    profile = models.ForeignKey(MedicalProfile, null=True, blank=True, on_delete=models.SET_NULL)
    clinician = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    encounter = models.ForeignKey("core.EncounterNote", null=True, blank=True, on_delete=models.SET_NULL, related_name="medications")
    drug_name = models.CharField(max_length=160)
    dosage = models.CharField(max_length=80, blank=True)
    route = models.CharField(max_length=64, blank=True)
    frequency = models.CharField(max_length=64, blank=True)
    duration = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_REQUESTED)
    fhir_resource = JSONField(default=dict, blank=True)
    ai_insights = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_medication_order"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["patient", "status"]),
            models.Index(fields=["drug_name"]),
        ]

    def __str__(self):
        return f"{self.patient} · {self.drug_name}"


class InventoryItem(BaseEntity):
    STATUS_ACTIVE = "active"
    STATUS_INACTIVE = "inactive"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_INACTIVE, "Inactive"),
    ]

    profile = models.ForeignKey(MedicalProfile, on_delete=models.CASCADE, related_name="inventory_items")
    organization = models.ForeignKey(HealthcareOrganization, on_delete=models.CASCADE, related_name="inventory_items")
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=120, blank=True)
    sku = models.CharField(max_length=64, blank=True)
    unit = models.CharField(max_length=32, default="unit")
    quantity_on_hand = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    reorder_level = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_inventory_item"
        unique_together = (("profile", "name"),)
        ordering = ["name"]

    def adjust_quantity(self, delta: Decimal):
        self.quantity_on_hand = max(Decimal("0.00"), self.quantity_on_hand + delta)
        self.save(update_fields=["quantity_on_hand"])


class DiagnosticOrder(BaseEntity):
    STATUS_ORDERED = "ordered"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_ORDERED, "Ordered"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="diagnostic_orders")
    profile = models.ForeignKey(MedicalProfile, null=True, blank=True, on_delete=models.SET_NULL)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    test_name = models.CharField(max_length=200)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_ORDERED)
    specimen_collected_at = models.DateTimeField(null=True, blank=True)
    results = JSONField(default=dict, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_diagnostic_order"
        ordering = ["-created_at"]


class ImagingStudy(BaseEntity):
    STATUS_SCHEDULED = "scheduled"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_SCHEDULED, "Scheduled"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="imaging_studies")
    profile = models.ForeignKey(MedicalProfile, null=True, blank=True, on_delete=models.SET_NULL)
    modality = models.CharField(max_length=64)
    body_region = models.CharField(max_length=120, blank=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_SCHEDULED)
    results_summary = models.TextField(blank=True)
    result_files = JSONField(default=list, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_imaging_study"
        ordering = ["-scheduled_at"]


class MedicationAdherenceReminder(BaseEntity):
    STATUS_PENDING = "pending"
    STATUS_SENT = "sent"
    STATUS_ACKNOWLEDGED = "acknowledged"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_SENT, "Sent"),
        (STATUS_ACKNOWLEDGED, "Acknowledged"),
    ]

    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="adherence_reminders")
    medication_order = models.ForeignKey(MedicationOrder, null=True, blank=True, on_delete=models.SET_NULL)
    scheduled_at = models.DateTimeField()
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    channel = models.CharField(max_length=64, default="sms")
    notes = models.TextField(blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_adherence_reminder"
        ordering = ["scheduled_at"]


class SupplyForecast(BaseEntity):
    profile = models.ForeignKey(MedicalProfile, on_delete=models.CASCADE, related_name="supply_forecasts")
    category = models.CharField(max_length=128)
    period_start = models.DateField()
    period_end = models.DateField()
    predicted_usage = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))])
    notes = models.TextField(blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_supply_forecast"
        ordering = ["-period_start"]



class ClinicalTask(BaseEntity):
    STATUS_PENDING = "pending"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"

    PRIORITY_LOW = "low"
    PRIORITY_MEDIUM = "medium"
    PRIORITY_HIGH = "high"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_COMPLETED, "Completed"),
    ]

    PRIORITY_CHOICES = [
        (PRIORITY_LOW, "Low"),
        (PRIORITY_MEDIUM, "Medium"),
        (PRIORITY_HIGH, "High"),
    ]

    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="clinical_tasks")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="clinical_tasks")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_clinical_tasks")
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    priority = models.CharField(max_length=16, choices=PRIORITY_CHOICES, default=PRIORITY_MEDIUM)
    due_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_clinical_task"
        ordering = ["-created_at"]

    def mark_completed(self):
        self.status = self.STATUS_COMPLETED
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at"])


class EmergencyEscalation(BaseEntity):
    SEVERITY_LOW = "low"
    SEVERITY_MEDIUM = "medium"
    SEVERITY_HIGH = "high"
    SEVERITY_CRITICAL = "critical"

    STATUS_PENDING = "pending"
    STATUS_ESCALATED = "escalated"
    STATUS_RESOLVED = "resolved"

    SEVERITY_CHOICES = [
        (SEVERITY_LOW, "Low"),
        (SEVERITY_MEDIUM, "Medium"),
        (SEVERITY_HIGH, "High"),
        (SEVERITY_CRITICAL, "Critical"),
    ]

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ESCALATED, "Escalated"),
        (STATUS_RESOLVED, "Resolved"),
    ]

    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="escalations")
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="reported_escalations")
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES, default=SEVERITY_MEDIUM)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    summary = models.TextField()
    metadata = JSONField(default=dict, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "core_emergency_escalation"
        ordering = ["-created_at"]


class TriageRecord(BaseEntity):
    ACUITY_ROUTINE = "routine"
    ACUITY_ELEVATED = "elevated"
    ACUITY_URGENT = "urgent"

    ACUITY_CHOICES = [
        (ACUITY_ROUTINE, "Routine"),
        (ACUITY_ELEVATED, "Elevated"),
        (ACUITY_URGENT, "Urgent"),
    ]

    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="triage_records")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="triage_requests")
    symptoms = JSONField(default=list, blank=True)
    acuity_level = models.CharField(max_length=32, choices=ACUITY_CHOICES, default=ACUITY_ROUTINE)
    recommended_unit = models.CharField(max_length=128, blank=True)
    ai_response = JSONField(default=dict, blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_triage_record"
        ordering = ["-created_at"]


class ReferralRoute(BaseEntity):
    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_DECLINED = "declined"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_DECLINED, "Declined"),
    ]

    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="referrals")
    from_organization = models.ForeignKey(HealthcareOrganization, null=True, blank=True, on_delete=models.SET_NULL, related_name="referrals_from")
    to_organization = models.ForeignKey(HealthcareOrganization, null=True, blank=True, on_delete=models.SET_NULL, related_name="referrals_to")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_referrals")
    reason = models.CharField(max_length=400)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_referral_route"
        ordering = ["-created_at"]


class ClinicalEventLog(BaseEntity):
    EVENT_TASK_CREATED = "task_created"
    EVENT_TASK_COMPLETED = "task_completed"
    EVENT_ESCALATION = "escalation"
    EVENT_TRIAGE = "triage"
    EVENT_REFERRAL = "referral"

    EVENT_CHOICES = [
        (EVENT_TASK_CREATED, "Task Created"),
        (EVENT_TASK_COMPLETED, "Task Completed"),
        (EVENT_ESCALATION, "Escalation"),
        (EVENT_TRIAGE, "Triage"),
        (EVENT_REFERRAL, "Referral"),
    ]

    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="clinical_event_logs")
    event_type = models.CharField(max_length=64, choices=EVENT_CHOICES)
    description = models.TextField()
    triggered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="triggered_clinical_events")
    task = models.ForeignKey(ClinicalTask, null=True, blank=True, on_delete=models.SET_NULL, related_name="events")
    escalation = models.ForeignKey(EmergencyEscalation, null=True, blank=True, on_delete=models.SET_NULL, related_name="events")
    triage = models.ForeignKey(TriageRecord, null=True, blank=True, on_delete=models.SET_NULL, related_name="events")
    referral = models.ForeignKey(ReferralRoute, null=True, blank=True, on_delete=models.SET_NULL, related_name="events")
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_clinical_event"
        ordering = ["-created_at"]


class AllergyRecord(BaseEntity):
    SEVERITY_MILD = "mild"
    SEVERITY_MODERATE = "moderate"
    SEVERITY_SEVERE = "severe"

    STATUS_ACTIVE = "active"
    STATUS_INACTIVE = "inactive"

    SEVERITY_CHOICES = [
        (SEVERITY_MILD, "Mild"),
        (SEVERITY_MODERATE, "Moderate"),
        (SEVERITY_SEVERE, "Severe"),
    ]
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_INACTIVE, "Inactive"),
    ]

    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="allergies")
    agent = models.CharField(max_length=140)
    category = models.CharField(max_length=64, blank=True)
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES, default=SEVERITY_MODERATE)
    reaction = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    recorded_at = models.DateTimeField(default=timezone.now)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_patient_allergy"
        ordering = ["-recorded_at"]
        indexes = [models.Index(fields=["patient", "status"])]

    def __str__(self):
        return f"{self.patient} - {self.agent} ({self.severity})"


class VitalSign(BaseEntity):
    TYPE_PULSE = "pulse"
    TYPE_BP = "blood_pressure"
    TYPE_RESPIRATION = "respiration"
    TYPE_TEMPERATURE = "temperature"
    TYPE_OXYGEN = "oxygen_saturation"

    TYPE_CHOICES = [
        (TYPE_PULSE, "Pulse"),
        (TYPE_BP, "Blood pressure"),
        (TYPE_RESPIRATION, "Respiration rate"),
        (TYPE_TEMPERATURE, "Temperature"),
        (TYPE_OXYGEN, "Oxygen saturation"),
    ]

    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="vitals")
    profile = models.ForeignKey(MedicalProfile, null=True, blank=True, on_delete=models.SET_NULL)
    clinician = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    vital_type = models.CharField(max_length=64, choices=TYPE_CHOICES)
    value = models.DecimalField(max_digits=6, decimal_places=2)
    units = models.CharField(max_length=24, blank=True)
    recorded_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_vital_sign"
        ordering = ["-recorded_at"]
        indexes = [
            models.Index(fields=["patient", "vital_type"]),
            models.Index(fields=["recorded_at"]),
        ]

    def __str__(self):
        return f"{self.patient} - {self.vital_type} {self.value}{self.units}"


class WellnessMetric(BaseEntity):
    METRIC_STEPS = "steps"
    METRIC_SLEEP = "sleep"
    METRIC_HEART_RATE = "heart_rate"
    METRIC_WEIGHT = "weight"
    METRIC_BLOOD_PRESSURE = "blood_pressure"
    METRIC_BLOOD_GLUCOSE = "blood_glucose"
    METRIC_WORKOUT = "workout"

    SOURCE_MANUAL = "manual"
    SOURCE_CLINICAL = "clinical"
    SOURCE_APPLE_HEALTH = "apple_health"
    SOURCE_HEALTH_CONNECT = "health_connect"
    SOURCE_GOOGLE_FIT = "google_fit"
    SOURCE_WEARABLE = "wearable"
    SOURCE_IMPORTED = "imported"

    WINDOW_INSTANT = "instant"
    WINDOW_DAILY = "daily"
    WINDOW_SLEEP = "sleep_session"
    WINDOW_WORKOUT = "workout_session"

    METRIC_CHOICES = [
        (METRIC_STEPS, "Steps"),
        (METRIC_SLEEP, "Sleep"),
        (METRIC_HEART_RATE, "Heart rate"),
        (METRIC_WEIGHT, "Weight"),
        (METRIC_BLOOD_PRESSURE, "Blood pressure"),
        (METRIC_BLOOD_GLUCOSE, "Blood glucose"),
        (METRIC_WORKOUT, "Workout"),
    ]

    SOURCE_CHOICES = [
        (SOURCE_MANUAL, "Manual"),
        (SOURCE_CLINICAL, "Clinical"),
        (SOURCE_APPLE_HEALTH, "Apple Health"),
        (SOURCE_HEALTH_CONNECT, "Health Connect"),
        (SOURCE_GOOGLE_FIT, "Google Fit"),
        (SOURCE_WEARABLE, "Wearable"),
        (SOURCE_IMPORTED, "Imported"),
    ]

    WINDOW_CHOICES = [
        (WINDOW_INSTANT, "Instant"),
        (WINDOW_DAILY, "Daily"),
        (WINDOW_SLEEP, "Sleep session"),
        (WINDOW_WORKOUT, "Workout session"),
    ]

    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="wellness_metrics")
    profile = models.ForeignKey(MedicalProfile, null=True, blank=True, on_delete=models.SET_NULL)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    metric_type = models.CharField(max_length=40, choices=METRIC_CHOICES)
    source = models.CharField(max_length=40, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    source_label = models.CharField(max_length=120, blank=True)
    measurement_window = models.CharField(max_length=40, choices=WINDOW_CHOICES, default=WINDOW_INSTANT)
    value = models.DecimalField(max_digits=10, decimal_places=2)
    units = models.CharField(max_length=24, blank=True)
    normalized_value = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    normalized_units = models.CharField(max_length=24, blank=True)
    secondary_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    secondary_units = models.CharField(max_length=24, blank=True)
    recorded_at = models.DateTimeField(default=timezone.now)
    period_start = models.DateTimeField(null=True, blank=True)
    period_end = models.DateTimeField(null=True, blank=True)
    is_clinically_verified = models.BooleanField(default=False)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_wellness_metric"
        ordering = ["-recorded_at", "-created_at"]
        indexes = [
            models.Index(fields=["patient", "metric_type"]),
            models.Index(fields=["patient", "metric_type", "recorded_at"]),
            models.Index(fields=["source", "metric_type"]),
        ]

    def __str__(self):
        return f"{self.patient} · {self.metric_type} {self.value}{self.units}"


class ProblemRecord(BaseEntity):
    STATUS_ACTIVE = "active"
    STATUS_RESOLVED = "resolved"
    STATUS_INACTIVE = "inactive"

    VERIFICATION_PROVISIONAL = "provisional"
    VERIFICATION_CONFIRMED = "confirmed"
    VERIFICATION_REFUTED = "refuted"

    SEVERITY_LOW = "low"
    SEVERITY_MEDIUM = "medium"
    SEVERITY_HIGH = "high"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_RESOLVED, "Resolved"),
        (STATUS_INACTIVE, "Inactive"),
    ]

    VERIFICATION_CHOICES = [
        (VERIFICATION_PROVISIONAL, "Provisional"),
        (VERIFICATION_CONFIRMED, "Confirmed"),
        (VERIFICATION_REFUTED, "Refuted"),
    ]

    SEVERITY_CHOICES = [
        (SEVERITY_LOW, "Low"),
        (SEVERITY_MEDIUM, "Medium"),
        (SEVERITY_HIGH, "High"),
    ]

    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="problems")
    profile = models.ForeignKey(MedicalProfile, null=True, blank=True, on_delete=models.SET_NULL)
    diagnosed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="diagnosed_problems")
    title = models.CharField(max_length=180)
    code = models.CharField(max_length=64, blank=True)
    code_system = models.CharField(max_length=64, blank=True)
    clinical_status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    verification_status = models.CharField(max_length=24, choices=VERIFICATION_CHOICES, default=VERIFICATION_PROVISIONAL)
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES, default=SEVERITY_MEDIUM)
    onset_date = models.DateField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_problem_record"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["patient", "clinical_status"]),
            models.Index(fields=["code"]),
        ]

    def __str__(self):
        return f"{self.patient} · {self.title}"


class ImmunizationRecord(BaseEntity):
    STATUS_COMPLETED = "completed"
    STATUS_DUE = "due"
    STATUS_DECLINED = "declined"

    STATUS_CHOICES = [
        (STATUS_COMPLETED, "Completed"),
        (STATUS_DUE, "Due"),
        (STATUS_DECLINED, "Declined"),
    ]

    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="immunizations")
    profile = models.ForeignKey(MedicalProfile, null=True, blank=True, on_delete=models.SET_NULL)
    administered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="administered_immunizations")
    vaccine_name = models.CharField(max_length=180)
    vaccine_code = models.CharField(max_length=64, blank=True)
    manufacturer = models.CharField(max_length=120, blank=True)
    lot_number = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_COMPLETED)
    administered_at = models.DateTimeField(null=True, blank=True)
    dose_number = models.PositiveIntegerField(null=True, blank=True)
    series_name = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_immunization_record"
        ordering = ["-administered_at", "-created_at"]
        indexes = [
            models.Index(fields=["patient", "status"]),
            models.Index(fields=["vaccine_code"]),
        ]

    def __str__(self):
        return f"{self.patient} · {self.vaccine_name}"


class ProcedureRecord(BaseEntity):
    STATUS_SCHEDULED = "scheduled"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_SCHEDULED, "Scheduled"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="procedures")
    profile = models.ForeignKey(MedicalProfile, null=True, blank=True, on_delete=models.SET_NULL)
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="performed_procedures")
    procedure_name = models.CharField(max_length=180)
    procedure_code = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_COMPLETED)
    performed_at = models.DateTimeField(null=True, blank=True)
    location = models.CharField(max_length=180, blank=True)
    notes = models.TextField(blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_procedure_record"
        ordering = ["-performed_at", "-created_at"]
        indexes = [
            models.Index(fields=["patient", "status"]),
            models.Index(fields=["procedure_code"]),
        ]

    def __str__(self):
        return f"{self.patient} · {self.procedure_name}"


class HealthDocument(BaseEntity):
    CATEGORY_LAB = "lab"
    CATEGORY_IMAGING = "imaging"
    CATEGORY_PRESCRIPTION = "prescription"
    CATEGORY_DISCHARGE = "discharge"
    CATEGORY_INSURANCE = "insurance"
    CATEGORY_REFERRAL = "referral"
    CATEGORY_GENERAL = "general"

    SOURCE_USER = "user"
    SOURCE_PROVIDER = "provider"
    SOURCE_IMPORT = "import"

    STATUS_ACTIVE = "active"
    STATUS_ARCHIVED = "archived"

    CATEGORY_CHOICES = [
        (CATEGORY_LAB, "Lab"),
        (CATEGORY_IMAGING, "Imaging"),
        (CATEGORY_PRESCRIPTION, "Prescription"),
        (CATEGORY_DISCHARGE, "Discharge"),
        (CATEGORY_INSURANCE, "Insurance"),
        (CATEGORY_REFERRAL, "Referral"),
        (CATEGORY_GENERAL, "General"),
    ]

    SOURCE_CHOICES = [
        (SOURCE_USER, "User"),
        (SOURCE_PROVIDER, "Provider"),
        (SOURCE_IMPORT, "Import"),
    ]

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_ARCHIVED, "Archived"),
    ]

    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="documents")
    profile = models.ForeignKey(MedicalProfile, null=True, blank=True, on_delete=models.SET_NULL)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="uploaded_health_documents")
    title = models.CharField(max_length=180)
    category = models.CharField(max_length=24, choices=CATEGORY_CHOICES, default=CATEGORY_GENERAL)
    source_type = models.CharField(max_length=24, choices=SOURCE_CHOICES, default=SOURCE_USER)
    source_label = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    file_url = models.CharField(max_length=500, blank=True)
    mime_type = models.CharField(max_length=120, blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_health_document"
        ordering = ["-issued_at", "-created_at"]
        indexes = [
            models.Index(fields=["patient", "category"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.patient} · {self.title}"


class HealthRecordExchangeLog(BaseEntity):
    DIRECTION_EXPORT = "export"
    DIRECTION_IMPORT = "import"

    STATUS_SUCCESS = "success"
    STATUS_PARTIAL = "partial"
    STATUS_FAILED = "failed"

    FORMAT_FHIR_BUNDLE = "fhir_bundle"
    FORMAT_KIS_BUNDLE = "kis_bundle"

    DIRECTION_CHOICES = [
        (DIRECTION_EXPORT, "Export"),
        (DIRECTION_IMPORT, "Import"),
    ]

    STATUS_CHOICES = [
        (STATUS_SUCCESS, "Success"),
        (STATUS_PARTIAL, "Partial"),
        (STATUS_FAILED, "Failed"),
    ]

    FORMAT_CHOICES = [
        (FORMAT_FHIR_BUNDLE, "FHIR Bundle"),
        (FORMAT_KIS_BUNDLE, "KIS Bundle"),
    ]

    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="exchange_logs")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="health_record_exchanges")
    direction = models.CharField(max_length=16, choices=DIRECTION_CHOICES)
    exchange_format = models.CharField(max_length=24, choices=FORMAT_CHOICES, default=FORMAT_FHIR_BUNDLE)
    source_label = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_SUCCESS)
    summary = models.JSONField(default=dict, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_health_record_exchange_log"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["patient", "direction"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.patient} · {self.direction} ({self.status})"


class PatientFamilyProfile(BaseEntity):
    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="family_profiles")
    relationship = models.CharField(max_length=64)
    members = JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "core_patient_family"
        ordering = ["relationship"]

    def __str__(self):
        return f"{self.patient} - {self.relationship}"


class ConsentRecord(BaseEntity):
    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="consents")
    purpose = models.CharField(max_length=128)
    consent_text = models.TextField()
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="granted_consents")
    granted_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_consent_record"
        ordering = ["-granted_at"]

    def __str__(self):
        return f"Consent {self.purpose} for {self.patient}"


class EncounterNote(BaseEntity):
    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="encounters")
    organization = models.ForeignKey(HealthcareOrganization, null=True, blank=True, on_delete=models.SET_NULL)
    profile = models.ForeignKey(MedicalProfile, null=True, blank=True, on_delete=models.SET_NULL)
    clinician = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="encounters")
    encounter_type = models.CharField(max_length=64, default="clinical")
    summary = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    ai_insights = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_encounter_note"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.patient} - {self.encounter_type}"


class Appointment(BaseEntity):
    STATUS_SCHEDULED = "scheduled"
    STATUS_CANCELLED = "cancelled"
    STATUS_COMPLETED = "completed"

    STATUS_CHOICES = [
        (STATUS_SCHEDULED, "Scheduled"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_COMPLETED, "Completed"),
    ]

    patient = models.ForeignKey(PatientMasterRecord, on_delete=models.CASCADE, related_name="appointments")
    profile = models.ForeignKey(MedicalProfile, null=True, blank=True, on_delete=models.SET_NULL)
    scheduled_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_SCHEDULED)
    queue_position = models.IntegerField(null=True, blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_appointment"
        ordering = ["-scheduled_at"]

    def __str__(self):
        return f"{self.patient} @ {self.scheduled_at}"


class TelemedicineSession(BaseEntity):
    STATUS_PENDING = "pending"
    STATUS_ACTIVE = "active"
    STATUS_ENDED = "ended"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_ENDED, "Ended"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    profile = models.ForeignKey(MedicalProfile, null=True, blank=True, on_delete=models.SET_NULL, related_name="tele_sessions")
    patient = models.ForeignKey(PatientMasterRecord, null=True, blank=True, on_delete=models.SET_NULL, related_name="tele_sessions")
    clinician = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="tele_clinician_sessions")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    appointment = models.ForeignKey(Appointment, null=True, blank=True, on_delete=models.SET_NULL, related_name="tele_sessions")
    reminder_sent = models.BooleanField(default=False)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    recording_url = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "core_telemedicine_session"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Telemedicine {self.patient or 'Unknown'} @ {self.start_str()}"

    def start_str(self):
        if self.started_at:
            return self.started_at.isoformat()
        return "Not started"


class TelemedicineDevice(BaseEntity):
    session = models.ForeignKey(TelemedicineSession, on_delete=models.CASCADE, related_name="devices")
    device_id = models.CharField(max_length=128)
    device_type = models.CharField(max_length=64, default="camera")
    metadata = JSONField(default=dict, blank=True)
    last_seen = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "core_telemedicine_device"
        unique_together = (("session", "device_id"),)

    def __str__(self):
        return f"{self.device_type} ({self.device_id})"


class VoiceDictation(BaseEntity):
    session = models.ForeignKey(TelemedicineSession, null=True, blank=True, on_delete=models.SET_NULL, related_name="dictations")
    clinician = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="dictations")
    audio_metadata = JSONField(default=dict, blank=True)
    transcript = models.TextField(blank=True)
    groq_job = models.ForeignKey("ai_integration.AIJob", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        db_table = "core_voice_dictation"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Dictation for {self.clinician or 'clinician'}"

# ---------------------------
# Usage examples (in comments)
# ---------------------------
"""
Examples:

# assign role to a user for a group:
from django.contrib.contenttypes.models import ContentType
user_ct = ContentType.objects.get_for_model(user.__class__)
group_ct = ContentType.objects.get_for_model(Group)

ra = RoleAssignment.objects.create(
    role=moderator_role,
    principal_content_type=user_ct,
    principal_object_id=str(user.pk),
    target_content_type=group_ct,
    target_object_id=str(group.pk)
)

# grant an allow ACE to a role on a channel:
role_ct = ContentType.objects.get_for_model(Role)
channel_ct = ContentType.objects.get_for_model(Channel)

ace = AccessControlEntry.objects.create(
    principal_content_type=role_ct,
    principal_object_id=str(moderator_role.id),
    target_content_type=channel_ct,
    target_object_id=str(channel.pk),
    permissions=["channel.post", "channel.delete_message"],
    effect=AccessControlEntry.EFFECT_ALLOW
)

# check permission:
if group.can_user(user, "group.post"):
    # allow posting

# invite flow:
invite = MembershipInvite.objects.create(token="abc123", group=group, created_by=inviter)
if invite.is_valid():
    invite.use()
    Membership.objects.create(group=group, user_content_type=user_ct, user_object_id=str(user.pk), status=Membership.STATUS_ACTIVE)

"""
