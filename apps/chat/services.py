# chat/services.py
from typing import Tuple, Optional

from django.db import IntegrityError, transaction
from django.db.models import F, Q


from apps.accounts.models import User
from .models import (
    Conversation,
    ConversationMember,
    ConversationSettings,
    BaseConversationRole,
    ConversationType,
    ConversationRequestState,
)

# Which conversations are reachable from a Partner surface (Partner page,
# community, a partner-owned group/channel) rather than the main Messages
# tab. Shared by ConversationViewSet.get_queryset() (so the Messages list
# never shows one of these) and apps.notifications.badge_counts (so the
# Messages tab-bar badge never counts an unread message from one of these
# either) - previously each kept its own separate, independently-maintained
# copy of this logic, and badge_counts.py's copy only covered Partner.
# main_conversation and partner-owned Channel.conversation, not partner-
# owned Group/Community conversations. That meant the Messages badge could
# count an unread message the Messages tab itself would never display, so
# it looked permanently "stuck" even after everything actually visible in
# Messages had been read - there was nothing to open to clear it. A single
# shared definition makes that drift structurally impossible from here on.
#
# Built as functions rather than bare Q objects because the two call sites
# query from different roots: ConversationViewSet filters Conversation
# directly (prefix ""), badge_counts.py filters ConversationMember (prefix
# "conversation__"). The relation paths themselves - the actual thing that
# must never drift between the two callers - are defined exactly once
# either way.
def partner_conversation_q(prefix: str = "") -> Q:
    p = f"{prefix}__" if prefix else ""
    return (
        Q(**{f"{p}group__partner__isnull": False})
        | Q(**{f"{p}group__community__partner__isnull": False})
        | Q(**{f"{p}channel__partner__isnull": False})
        | Q(**{f"{p}channel__community__partner__isnull": False})
        | Q(**{f"{p}community_main__partner__isnull": False})
        | Q(**{f"{p}community_posts__partner__isnull": False})
        | Q(**{f"{p}partner_main__isnull": False})
    )


# Conversations hidden from the Messages tab for a reason that has nothing
# to do with Partners - each is reachable only from its own dedicated
# surface (a course's classroom tab, the Bible section's Prayer Calendar),
# so an unread message in one of these must not be counted toward ANY main
# tab badge, not just excluded from the Messages list.
def non_partner_hidden_conversation_q(prefix: str = "") -> Q:
    p = f"{prefix}__" if prefix else ""
    return (
        # A course's own "classroom" group chat (see KIS's
        # courseGroupChat.ts — one persistent group per course, found by
        # the deterministic slug "edu-course-{courseId}").
        Q(**{f"{p}group__slug__startswith": "edu-course-"})
        # The 24 permanent Prayer Watch rooms (apps.prayer.provisioning —
        # slug "prayer-watch-{hour:02d}").
        | Q(**{f"{p}group__slug__startswith": "prayer-watch-"})
    )


# Every conversation ConversationViewSet's "list" action excludes from the
# Messages tab - the union callers should use when they need the exact same
# visibility rule the Messages tab itself enforces.
def hidden_from_messages_tab_q(prefix: str = "") -> Q:
    return partner_conversation_q(prefix) | non_partner_hidden_conversation_q(prefix)


# Conversation-rooted (prefix "") convenience constants for the common case.
PARTNER_CONVERSATION_Q = partner_conversation_q()
NON_PARTNER_HIDDEN_CONVERSATION_Q = non_partner_hidden_conversation_q()
HIDDEN_FROM_MESSAGES_TAB_Q = PARTNER_CONVERSATION_Q | NON_PARTNER_HIDDEN_CONVERSATION_Q


def _normalize_pair(user_a: User, user_b: User) -> tuple[User, User]:
    # deterministic ordering
    if user_a.id > user_b.id:
        return user_b, user_a
    return user_a, user_b


def direct_conversation_key(user_a: User, user_b: User) -> str:
    """
    Stable identity for a direct room between exactly two users.
    UUIDs are stringified and sorted so both caller directions resolve to
    the same key.
    """
    a = str(user_a.id)
    b = str(user_b.id)
    first, second = sorted([a, b])
    return f"direct:{first}:{second}"


def _find_existing_direct(user_a: User, user_b: User) -> Optional[Conversation]:
    """
    Find existing DIRECT conversation where BOTH users are active members.
    """
    key = direct_conversation_key(user_a, user_b)
    keyed = Conversation.objects.filter(
        type=ConversationType.DIRECT,
        direct_key=key,
    ).first()
    if keyed:
        return keyed

    qs = (
        Conversation.objects
        .filter(type=ConversationType.DIRECT)
        .filter(memberships__user=user_a, memberships__left_at__isnull=True)
        .filter(memberships__user=user_b, memberships__left_at__isnull=True)
        .distinct()
    )
    existing = qs.order_by("created_at").first()
    if existing and not existing.direct_key:
        try:
            existing.direct_key = key
            existing.save(update_fields=["direct_key"])
        except IntegrityError:
            return Conversation.objects.filter(
                type=ConversationType.DIRECT,
                direct_key=key,
            ).first()
    return existing


def _restore_direct_membership_visibility(conversation: Conversation) -> None:
    ConversationMember.objects.filter(
        conversation=conversation,
        left_at__isnull=True,
        is_hidden=True,
    ).update(is_hidden=False)


def get_or_create_direct_conversation(
    user_a: User,
    user_b: User,
    initiator: Optional[User] = None,
    use_request_flow: bool = False,
) -> Tuple[Conversation, bool]:
    """
    Backward-compatible API.

    - Old callers: get_or_create_direct_conversation(user_a, user_b)
        -> creates a normal direct conversation (no request workflow)

    - New callers (your DM request system):
        get_or_create_direct_conversation(user_a, user_b, initiator=request.user, use_request_flow=True)
        -> creates a pending DM request conversation:
           request_state=PENDING, initiator/recipient set, locked by default.
    """
    user_a, user_b = _normalize_pair(user_a, user_b)
    direct_key = direct_conversation_key(user_a, user_b)

    existing = _find_existing_direct(user_a, user_b)
    if existing:
        _restore_direct_membership_visibility(existing)
        return existing, False

    # If request flow is asked, initiator is required.
    if use_request_flow and initiator is None:
        initiator = user_a  # safe fallback, but you should pass initiator explicitly

    with transaction.atomic():
        existing = (
            Conversation.objects
            .select_for_update()
            .filter(type=ConversationType.DIRECT, direct_key=direct_key)
            .first()
        )
        if existing:
            _restore_direct_membership_visibility(existing)
            return existing, False

        if use_request_flow:
            if initiator.id not in (user_a.id, user_b.id):
                # initiator must be one of the participants
                initiator = user_a

            recipient = user_b if initiator.id == user_a.id else user_a

            conversation, created = Conversation.objects.get_or_create(
                type=ConversationType.DIRECT,
                direct_key=direct_key,
                defaults={
                    "created_by": initiator,
                    "is_locked": False,
                    "request_state": ConversationRequestState.PENDING,
                    "request_initiator": initiator,
                    "request_recipient": recipient,
                },
            )
            if not created:
                _restore_direct_membership_visibility(conversation)
                return conversation, False

            # Roles: initiator is OWNER, recipient MEMBER (fast WhatsApp-like)
            ConversationMember.objects.bulk_create([
                ConversationMember(
                    conversation=conversation,
                    user=initiator,
                    base_role=BaseConversationRole.OWNER,
                ),
                ConversationMember(
                    conversation=conversation,
                    user=recipient,
                    base_role=BaseConversationRole.MEMBER,
                ),
            ])

        else:
            # classic direct conversation (no request workflow)
            conversation, created = Conversation.objects.get_or_create(
                type=ConversationType.DIRECT,
                direct_key=direct_key,
                defaults={
                    "created_by": user_a,
                },
            )
            if not created:
                _restore_direct_membership_visibility(conversation)
                return conversation, False

            ConversationMember.objects.bulk_create([
                ConversationMember(
                    conversation=conversation,
                    user=user_a,
                    base_role=BaseConversationRole.OWNER,
                ),
                ConversationMember(
                    conversation=conversation,
                    user=user_b,
                    base_role=BaseConversationRole.MEMBER,
                ),
            ])

        ConversationSettings.objects.create(conversation=conversation)

    return conversation, True


def user_is_active_member(user: User, conversation: Conversation) -> bool:
    return ConversationMember.objects.filter(
        conversation=conversation,
        user=user,
        left_at__isnull=True,
        is_blocked=False,
    ).exists()



def allocate_conversation_seq(conversation: Conversation) -> int:
    """
    Atomically allocates the next conversation sequence number.
    GUARANTEES:
      - strictly increasing
      - unique per conversation
      - safe under concurrency
    """
    with transaction.atomic():
        Conversation.objects.filter(
            id=conversation.id
        ).update(
            last_message_seq=F('last_message_seq') + 1
        )

        conversation.refresh_from_db(fields=['last_message_seq'])
        return conversation.last_message_seq
