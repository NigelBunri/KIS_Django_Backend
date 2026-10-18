# apps/groups/services.py
from typing import Optional

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chat.models import (
    Conversation,
    ConversationType,
    ConversationSettings,
    ConversationMember,
    BaseConversationRole,
)
from apps.communities.models import CommunityBan, CommunityMembership, CommunityRole
from apps.groups.models import Group, GroupMembership, GroupRole


@transaction.atomic
def create_group_with_conversation(
    *,
    owner: User,
    name: str,
    slug: str,
    partner=None,
    community=None,
) -> Group:
    """
    Service for creating a group with a backing Conversation and owner membership.
    """
    conversation = Conversation.objects.create(
        type=ConversationType.GROUP,
        title=name,
        created_by=owner,
    )

    ConversationMember.objects.create(
        conversation=conversation,
        user=owner,
        base_role=BaseConversationRole.OWNER,
    )

    ConversationSettings.objects.create(conversation=conversation)

    group = Group.objects.create(
        owner=owner,
        conversation=conversation,
        name=name,
        slug=slug,
        partner=partner,
        community=community,
    )
    return group


@transaction.atomic
def reactivate_group_membership(group: Group, user: User, *, role=None):
    """
    Create-or-reactivate GroupMembership + ConversationMember for
    (group, user), keeping the two in sync. Shared by every path that can
    add/rejoin a group member (GroupViewSet's join/add-members/approve-
    request/unban, and ConversationViewSet.add_member when the conversation
    backs a group) - a conversation-only add previously left the group's own
    GroupMembership row missing entirely, so the group's member list (read
    from GroupMembership, not ConversationMember) never showed members added
    via the chat/conversation "Add member" UI even though they could already
    send and receive messages.

    joined_at is restamped to now() only when actually (re)activating from a
    nonexistent/left/banned state - never on a redundant call against an
    already-active membership - so a rejoin starts a new pre-join-history
    boundary (see the email/E2EE-history audit: new members can never
    decrypt messages sent before they joined, so an accurate joined_at is
    what lets the client draw that boundary correctly across leave/rejoin
    cycles) without disturbing an active member's real original join time.
    """
    role = role or GroupRole.MEMBER
    membership, created = GroupMembership.objects.get_or_create(
        group=group,
        user=user,
        defaults={"role": role},
    )
    if not created and (membership.left_at is not None or membership.is_banned):
        membership.left_at = None
        membership.is_banned = False
        membership.joined_at = timezone.now()
        membership.save(update_fields=["left_at", "is_banned", "joined_at"])

    cm, cm_created = ConversationMember.objects.get_or_create(
        conversation=group.conversation,
        user=user,
        defaults={"base_role": BaseConversationRole.MEMBER},
    )
    if not cm_created and cm.left_at is not None:
        cm.left_at = None
        cm.joined_at = timezone.now()
        cm.save(update_fields=["left_at", "joined_at"])

    return membership, cm


class CommunityBanned(Exception):
    """Raised by sync_community_membership_for_group when the target user
    is banned from the group's owning community - the caller must treat
    this as a hard denial, never silently skip the community sync (that
    would leave the user able to chat/post in the group's own conversation
    while still being nominally "banned" from the community one level up)."""


@transaction.atomic
def sync_community_membership_for_group(group: Group, user: User) -> None:
    """
    Mirror a group member into the group's owning community: a
    CommunityMembership row plus ConversationMember on both the
    community's main_conversation and posts_conversation. A no-op when
    the group doesn't belong to a community.

    Extracted from GroupViewSet.add_members (the only path that
    originally did this) so GroupViewSet.join_by_invite and
    ConversationViewSet.add_member's group branch can share it instead of
    only ever syncing GroupMembership/ConversationMember on the group's
    own conversation - which left a user who joined a community-scoped
    group via invite link, or got added to one via the generic
    "add from contacts/chats" action, fully able to use the group but
    invisible in the community's own member list (CommunityViewSet.members
    reads CommunityMembership only - see apps.communities.views) and able
    to bypass a CommunityBan that only this group's own owner/admin would
    ever see (add_members already guarded this; the other two paths did
    not).
    """
    if not group.community_id:
        return

    if CommunityBan.objects.filter(community_id=group.community_id, user=user).exists():
        raise CommunityBanned()

    CommunityMembership.objects.update_or_create(
        community_id=group.community_id,
        user=user,
        defaults={"role": CommunityRole.MEMBER, "left_at": None, "is_banned": False},
    )
    community = group.community
    if community and community.main_conversation_id:
        mcm, _ = ConversationMember.objects.get_or_create(
            conversation=community.main_conversation,
            user=user,
            defaults={"base_role": BaseConversationRole.MEMBER},
        )
        if mcm.left_at is not None:
            mcm.left_at = None
            mcm.save(update_fields=["left_at"])
    if community and community.posts_conversation_id:
        pcm, _ = ConversationMember.objects.get_or_create(
            conversation=community.posts_conversation,
            user=user,
            defaults={"base_role": BaseConversationRole.MEMBER},
        )
        if pcm.left_at is not None:
            pcm.left_at = None
            pcm.save(update_fields=["left_at"])
