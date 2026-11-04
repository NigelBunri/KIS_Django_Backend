# apps/communities/services.py
from django.db import transaction

from apps.accounts.models import User
from apps.partners.models import Partner
from apps.communities.models import Community, CommunityMembership, CommunityRole, CommunityMembershipStatus
from apps.chat.models import (
    Conversation,
    ConversationType,
    ConversationSettings,
    ConversationMember,
    BaseConversationRole,
)


@transaction.atomic
def create_community_with_main_conversation(
    *,
    owner: User,
    partner: Partner,
    name: str,
    slug: str,
    description: str = "",
    avatar_url: str = "",
    create_main_conversation: bool = True,
) -> Community:
    """
    Service for creating a Community under a Partner, and optionally its main conversation.
    """
    main_conversation = None

    if create_main_conversation:
        main_conversation = Conversation.objects.create(
            type=ConversationType.GROUP,
            title=name,
            description=f"Main conversation for community {name}",
            created_by=owner,
        )

        ConversationMember.objects.create(
            conversation=main_conversation,
            user=owner,
            base_role=BaseConversationRole.OWNER,
        )

        ConversationSettings.objects.create(conversation=main_conversation)

    community = Community.objects.create(
        partner=partner,
        name=name,
        slug=slug,
        description=description,
        avatar_url=avatar_url,
        owner=owner,
        main_conversation=main_conversation,
    )

    # CommunityCreateSerializer.create() (the actually-used create path)
    # does this too - without it the owner has no CommunityMembership row
    # at all, so GET .../members/ (which filters on
    # CommunityMembership.objects.active()) would never list them.
    CommunityMembership.objects.create(
        community=community,
        user=owner,
        role=CommunityRole.OWNER,
        status=CommunityMembershipStatus.ACTIVE,
    )

    return community


# ---------------------------------------------------------------------
# KIS Bot Platform (Phase 2 gap-closing) — event publishers. Plain
# Django calls, never touching Nest, mirroring apps.groups.services'
# member.joined/member.left pattern exactly but for Community
# membership/posts/comments. Never raise: a publisher failure must
# never break the real join/post/comment action that triggered it.
# ---------------------------------------------------------------------

def dispatch_community_member_event(community: Community, user_id, event: str) -> None:
    try:
        from apps.bots.models import BotPermissionScope, BotResourceType
        from apps.bots.services import dispatch_resource_event
        from django.utils import timezone as _tz

        dispatch_resource_event(
            BotResourceType.COMMUNITY, community.id, event, {"user_id": str(user_id)},
            event_id=f"{event}:{community.id}:{user_id}:{_tz.now().timestamp()}",
            required_scope=BotPermissionScope.READ_MEMBERS,
        )
    except Exception:  # noqa: BLE001
        pass


def dispatch_community_post_created(post) -> None:
    try:
        from apps.bots.models import BotPermissionScope, BotResourceType
        from apps.bots.services import dispatch_resource_event
        from django.utils import timezone as _tz

        dispatch_resource_event(
            BotResourceType.COMMUNITY, post.community_id, "post.created",
            {
                "post_id": str(post.id), "author_id": str(post.author_id),
                "text": getattr(post, "text_plain", "") or getattr(post, "text_preview", "") or "",
            },
            event_id=f"post.created:{post.id}:{_tz.now().timestamp()}",
            required_scope=BotPermissionScope.READ_MESSAGES,
        )
    except Exception:  # noqa: BLE001
        pass


def dispatch_community_comment_created(comment) -> None:
    try:
        from apps.bots.models import BotPermissionScope, BotResourceType
        from apps.bots.services import dispatch_resource_event
        from django.utils import timezone as _tz

        dispatch_resource_event(
            BotResourceType.COMMUNITY, comment.post.community_id, "comment.created",
            {
                "comment_id": str(comment.id), "post_id": str(comment.post_id),
                "author_id": str(comment.author_id), "text": getattr(comment, "text", "") or "",
            },
            event_id=f"comment.created:{comment.id}:{_tz.now().timestamp()}",
            required_scope=BotPermissionScope.READ_MESSAGES,
        )
    except Exception:  # noqa: BLE001
        pass
