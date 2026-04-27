from apps.chat.models import (
    BaseConversationRole,
    Conversation,
    ConversationJoinPolicy,
    ConversationMember,
    ConversationSendPolicy,
    ConversationSettings,
    ConversationType,
)


def ensure_post_comment_conversation(
    owner,
    *,
    actor,
    title: str,
    description: str,
    created_by=None,
):
    """
    Canonical helper for discussion surfaces backed by `comment_conversation`.

    This gives partner posts, community posts, and broadcast items one room
    creation contract while Phase 2 migration is in progress.
    """
    conversation = getattr(owner, "comment_conversation", None)
    if conversation:
        return conversation

    conversation = Conversation.objects.create(
        type=ConversationType.POST,
        title=title,
        description=description,
        created_by=created_by or actor,
    )
    ConversationSettings.objects.get_or_create(
        conversation=conversation,
        defaults={
            "send_policy": ConversationSendPolicy.ALL_MEMBERS,
            "join_policy": ConversationJoinPolicy.OPEN,
        },
    )

    owner.comment_conversation = conversation
    owner.save(update_fields=["comment_conversation"])
    return conversation


def ensure_conversation_member(conversation: Conversation, user):
    return ConversationMember.objects.get_or_create(
        conversation=conversation,
        user=user,
        defaults={"base_role": BaseConversationRole.MEMBER},
    )


def get_discussion_count(owner, *, legacy_comment_queryset=None) -> int:
    """
    Phase 2 count contract:
    - prefer the conversation sequence when a canonical discussion room exists
    - otherwise fall back to legacy SQL comments during migration
    """
    conversation = getattr(owner, "comment_conversation", None)
    if conversation and conversation.last_message_seq is not None:
        return max(int(conversation.last_message_seq or 0), 0)
    if legacy_comment_queryset is not None:
        return int(legacy_comment_queryset.count())
    return 0
