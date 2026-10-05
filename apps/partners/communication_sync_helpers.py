# apps/partners/communication_sync_helpers.py
#
# Generic, domain-agnostic join/leave/admin-grant operations on the
# Conversation/Community/Group/Channel primitives. Extracted from
# apps.broadcasts.education_communication_sync (the first domain to need
# this pattern) so a second domain (apps.health_ops.communication_sync)
# doesn't have to duplicate ~120 lines of identical logic — these
# functions never reference Education or Health models, only the generic
# chat/communities/groups/channels ones, so any Partner-Account-gated
# ensure_*/sync_* module can share them.
from __future__ import annotations

from django.utils import timezone
from django.utils.text import slugify

from apps.chat.models import (
    BaseConversationRole,
    Conversation,
    ConversationMember,
    ConversationSettings,
    ConversationType,
)
from apps.communities.models import (
    Community,
    CommunityMembership,
    CommunityMembershipStatus,
    CommunityRole,
)
from apps.groups.models import Group, GroupMembership, GroupRole


def unique_slug(model, base: str, **scope_filter) -> str:
    root = slugify(base) or model.__name__.lower()
    candidate = root
    suffix = 0
    while model.objects.filter(slug=candidate, **scope_filter).exists():
        suffix += 1
        candidate = f"{root}-{suffix}"
    return candidate


def create_conversation(*, conv_type: str, title: str, owner) -> Conversation:
    conversation = Conversation.objects.create(
        type=conv_type, title=title, created_by=owner,
    )
    ConversationSettings.objects.create(conversation=conversation)
    ConversationMember.objects.create(
        conversation=conversation, user=owner, base_role=BaseConversationRole.OWNER,
    )
    return conversation


def join_conversation(conversation: Conversation, user) -> None:
    member, created = ConversationMember.objects.get_or_create(
        conversation=conversation, user=user,
        defaults={"base_role": BaseConversationRole.MEMBER},
    )
    if not created and member.left_at is not None:
        member.left_at = None
        member.save(update_fields=["left_at"])


def leave_conversation(conversation: Conversation, user) -> None:
    ConversationMember.objects.filter(
        conversation=conversation, user=user, left_at__isnull=True,
    ).update(left_at=timezone.now())


def grant_conversation_admin(conversation: Conversation, user) -> None:
    member, created = ConversationMember.objects.get_or_create(
        conversation=conversation, user=user,
        defaults={"base_role": BaseConversationRole.ADMIN},
    )
    if created:
        return
    updates = []
    if member.left_at is not None:
        member.left_at = None
        updates.append("left_at")
    if member.base_role not in (BaseConversationRole.OWNER, BaseConversationRole.ADMIN):
        member.base_role = BaseConversationRole.ADMIN
        updates.append("base_role")
    if updates:
        member.save(update_fields=updates)


def revoke_conversation_admin(conversation: Conversation, user) -> None:
    ConversationMember.objects.filter(
        conversation=conversation, user=user, base_role=BaseConversationRole.ADMIN,
    ).update(base_role=BaseConversationRole.MEMBER)


def join_group(group: Group, user) -> None:
    membership, created = GroupMembership.objects.get_or_create(
        group=group, user=user, defaults={"role": GroupRole.MEMBER},
    )
    if not created and membership.left_at is not None:
        membership.left_at = None
        membership.save(update_fields=["left_at"])
    join_conversation(group.conversation, user)


def leave_group(group: Group, user) -> None:
    GroupMembership.objects.filter(
        group=group, user=user, left_at__isnull=True,
    ).update(left_at=timezone.now())
    leave_conversation(group.conversation, user)


def grant_group_admin(group: Group, user) -> None:
    membership, created = GroupMembership.objects.get_or_create(
        group=group, user=user, defaults={"role": GroupRole.ADMIN},
    )
    if not created:
        updates = []
        if membership.left_at is not None:
            membership.left_at = None
            updates.append("left_at")
        if membership.role not in (GroupRole.OWNER, GroupRole.ADMIN):
            membership.role = GroupRole.ADMIN
            updates.append("role")
        if updates:
            membership.save(update_fields=updates)
    grant_conversation_admin(group.conversation, user)


def revoke_group_admin(group: Group, user) -> None:
    GroupMembership.objects.filter(
        group=group, user=user, role=GroupRole.ADMIN,
    ).update(role=GroupRole.MEMBER)
    revoke_conversation_admin(group.conversation, user)


def join_community(community: Community, user) -> None:
    membership, created = CommunityMembership.objects.get_or_create(
        community=community, user=user,
        defaults={"role": CommunityRole.MEMBER, "status": CommunityMembershipStatus.ACTIVE},
    )
    if not created and membership.status != CommunityMembershipStatus.ACTIVE:
        membership.status = CommunityMembershipStatus.ACTIVE
        membership.left_at = None
        membership.save(update_fields=["status", "left_at"])


def leave_community(community: Community, user) -> None:
    CommunityMembership.objects.filter(
        community=community, user=user, status=CommunityMembershipStatus.ACTIVE,
    ).update(status=CommunityMembershipStatus.LEFT, left_at=timezone.now())


def grant_community_admin(community: Community, user) -> None:
    membership, created = CommunityMembership.objects.get_or_create(
        community=community, user=user,
        defaults={"role": CommunityRole.ADMIN, "status": CommunityMembershipStatus.ACTIVE},
    )
    if not created:
        updates = []
        if membership.status != CommunityMembershipStatus.ACTIVE:
            membership.status = CommunityMembershipStatus.ACTIVE
            membership.left_at = None
            updates += ["status", "left_at"]
        if membership.role not in (CommunityRole.OWNER, CommunityRole.ADMIN):
            membership.role = CommunityRole.ADMIN
            updates.append("role")
        if updates:
            membership.save(update_fields=updates)


def revoke_community_admin(community: Community, user) -> None:
    CommunityMembership.objects.filter(
        community=community, user=user, role=CommunityRole.ADMIN,
    ).update(role=CommunityRole.MEMBER)
