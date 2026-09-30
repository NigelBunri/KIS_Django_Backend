# apps/broadcasts/education_communication_sync.py
#
# Partner Account -> Community/Group/Channel sync layer for Education.
#
# Architectural principle: Education is the source of truth for
# structure, enrollment, access, and permissions. Communication spaces
# are a connected layer that mirrors the relevant educational
# relationships - never the reverse. Education views must never read
# communication state to decide enrollment/access; this module only
# *reacts* to education state changing (see sync_enrollment_communication
# / sync_staff_assignment_communication, called from the enrollment and
# staff-assignment views after a status change is persisted).
#
# Gated entirely on EducationInstitution.partner being set. An
# institution with no Partner Account connected never has any Community/
# Group/Channel created for it - every ensure_* function below is a
# guarded no-op (returns None) in that case, and Education continues to
# work exactly as it does without this module existing. Disconnecting the
# partner (clearing institution.partner) must never delete education data
# or existing communication rooms/membership - see
# on_institution_partner_disconnected, the only function that runs on
# disconnect, and it only unlinks.
#
# Stable references, not slug lookups: Program/Class/Course each persist
# their own community/group/channel FK once created (see the migration in
# 0063_education_program_class_hierarchy.py), so calling ensure_* again
# is a cheap, idempotent no-op after the first call. This is deliberately
# a different, independent mechanism from the ad hoc "edu-course-{id}"
# slug-discovered Group used for the always-available live-class chat/
# call room (courseGroupChat.ts on the frontend, the chat-list exclusion
# in apps/chat/views.py) - that one needs no Partner Account and exists
# for every course; this module's Channel is the richer, Partner-Account-
# gated announcement/discussion space. See EducationInstitutionCourse
# .channel's docstring in models.py. The two are allowed to coexist.
from __future__ import annotations

from django.db import transaction
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
    CommunityJoinPolicy,
    CommunityMembership,
    CommunityMembershipStatus,
    CommunityRole,
    CommunityVisibility,
)
from apps.groups.models import Group, GroupMembership, GroupRole
from apps.channels.models import Channel

from .models import EducationEnrollmentStatus, EducationInstitutionStaffAssignmentStatus


def _unique_slug(model, base: str, **scope_filter) -> str:
    root = slugify(base) or model.__name__.lower()
    candidate = root
    suffix = 0
    while model.objects.filter(slug=candidate, **scope_filter).exists():
        suffix += 1
        candidate = f"{root}-{suffix}"
    return candidate


def _create_conversation(*, conv_type: str, title: str, owner) -> Conversation:
    conversation = Conversation.objects.create(
        type=conv_type, title=title, created_by=owner,
    )
    ConversationSettings.objects.create(conversation=conversation)
    ConversationMember.objects.create(
        conversation=conversation, user=owner, base_role=BaseConversationRole.OWNER,
    )
    return conversation


# ---------------------------------------------------------------------------
# ensure_* — idempotent get-or-create for the communication space behind
# one education entity. Each returns None (does nothing) when the owning
# institution has no Partner Account connected.
# ---------------------------------------------------------------------------

@transaction.atomic
def ensure_program_community(program) -> Community | None:
    institution = program.institution
    if not institution.partner_id:
        return None
    if program.community_id:
        return program.community
    owner = institution.owner
    community = Community.objects.create(
        partner=institution.partner,
        owner=owner,
        name=program.title,
        slug=_unique_slug(Community, program.title),
        description=program.summary or program.description or "",
        visibility=CommunityVisibility.PUBLIC,
        join_policy=CommunityJoinPolicy.REQUEST,
    )
    CommunityMembership.objects.create(
        community=community,
        user=owner,
        role=CommunityRole.OWNER,
        status=CommunityMembershipStatus.ACTIVE,
    )
    program.community = community
    program.save(update_fields=["community"])
    return community


@transaction.atomic
def ensure_class_group(institution_class) -> Group | None:
    institution = institution_class.institution
    if not institution.partner_id:
        return None
    if institution_class.group_id:
        return institution_class.group
    owner = institution.owner
    # A class inside a program rides on that program's community once it
    # exists; a standalone class (no program, or the program's own
    # community was never provisioned) gets a partner-scoped group with
    # no community parent — both are valid per the hierarchy rules.
    community = None
    if institution_class.program_id:
        community = ensure_program_community(institution_class.program)
    conversation = _create_conversation(
        conv_type=ConversationType.GROUP, title=institution_class.name, owner=owner,
    )
    group = Group.objects.create(
        partner=institution.partner,
        community=community,
        name=institution_class.name,
        slug=_unique_slug(Group, institution_class.name, community=community),
        owner=owner,
        conversation=conversation,
    )
    GroupMembership.objects.create(group=group, user=owner, role=GroupRole.OWNER)
    institution_class.group = group
    institution_class.save(update_fields=["group"])
    return group


@transaction.atomic
def ensure_course_channel(course) -> Channel | None:
    institution = course.institution
    if not institution.partner_id:
        return None
    if course.channel_id:
        return course.channel
    owner = institution.owner
    # A course's channel sits under whichever community its class's
    # program provides (ensure_class_group has already resolved/created
    # that program's community as a side effect by the time we read it
    # here), or under its own program's community if it has no class,
    # or under no community at all if it's fully standalone.
    community = None
    if course.institution_class_id:
        ensure_class_group(course.institution_class)
        if course.institution_class.program_id:
            community = course.institution_class.program.community
    elif course.program_id:
        community = ensure_program_community(course.program)
    conversation = _create_conversation(
        conv_type=ConversationType.CHANNEL, title=course.title, owner=owner,
    )
    channel = Channel.objects.create(
        partner=institution.partner,
        community=community,
        name=course.title,
        slug=_unique_slug(Channel, course.title, partner=institution.partner),
        owner=owner,
        conversation=conversation,
    )
    course.channel = channel
    course.save(update_fields=["channel"])
    return channel


# ---------------------------------------------------------------------------
# Membership + administrator sync — call these after an
# EducationInstitutionEnrollment or EducationInstitutionStaffAssignment
# is saved with a new status. Both are safe to call unconditionally; they
# no-op when the institution has no Partner Account.
# ---------------------------------------------------------------------------

_ACTIVE_ENROLLMENT_STATUSES = {
    EducationEnrollmentStatus.ENROLLED,
    EducationEnrollmentStatus.COMPLETED,
}


def sync_enrollment_communication(enrollment) -> None:
    active = enrollment.status in _ACTIVE_ENROLLMENT_STATUSES
    user = enrollment.user

    if enrollment.course_id:
        course = enrollment.course
        if active:
            channel = ensure_course_channel(course)
            if channel:
                _join_conversation(channel.conversation, user)
        elif course.channel_id:
            _leave_conversation(course.channel.conversation, user)

    if enrollment.institution_class_id:
        institution_class = enrollment.institution_class
        if active:
            group = ensure_class_group(institution_class)
            if group:
                _join_group(group, user)
        elif institution_class.group_id:
            _leave_group(institution_class.group, user)

    if enrollment.program_id:
        program = enrollment.program
        if active:
            community = ensure_program_community(program)
            if community:
                _join_community(community, user)
        elif program.community_id:
            _leave_community(program.community, user)


def sync_staff_assignment_communication(assignment) -> None:
    """Adds the assigned user as an *admin* of whichever communication
    space matches the assignment's most specific scope (course > class >
    program) — created on demand. Only fires for active assignments;
    an inactive/removed assignment is left for the caller to explicitly
    revoke via revoke_staff_assignment_communication if that's the
    transition that happened (kept separate so "assignment role changed"
    and "assignment removed" aren't conflated)."""
    if assignment.status != EducationInstitutionStaffAssignmentStatus.ACTIVE:
        return
    user = assignment.membership.user

    if assignment.course_id:
        channel = ensure_course_channel(assignment.course)
        if channel:
            _grant_conversation_admin(channel.conversation, user)
        return
    if assignment.institution_class_id:
        group = ensure_class_group(assignment.institution_class)
        if group:
            _grant_group_admin(group, user)
        return
    if assignment.program_id:
        community = ensure_program_community(assignment.program)
        if community:
            _grant_community_admin(community, user)


def revoke_staff_assignment_communication(assignment) -> None:
    """Removes the admin grant this assignment previously produced —
    demotes back to plain member rather than removing membership
    outright, matching the spec's 'administrator removed' edge case
    (they may still be a legitimate member/student, just not an admin
    anymore)."""
    user = assignment.membership.user
    if assignment.course_id and assignment.course.channel_id:
        _revoke_conversation_admin(assignment.course.channel.conversation, user)
    if assignment.institution_class_id and assignment.institution_class.group_id:
        _revoke_group_admin(assignment.institution_class.group, user)
    if assignment.program_id and assignment.program.community_id:
        _revoke_community_admin(assignment.program.community, user)


def on_institution_partner_disconnected(institution) -> None:
    """The one function that runs when institution.partner is cleared.
    Deliberately does nothing to education data or existing communication
    rooms/membership — per the spec's disconnect-behavior rule, this is
    an *unlink*, not a delete. Any Program/Class/Course community/group/
    channel FK that was already set stays exactly as-is; new ones simply
    won't be created until a Partner Account is connected again (every
    ensure_* above re-checks institution.partner_id on every call, so
    nothing further needs to happen here beyond documenting the
    intentional no-op)."""
    return


# ---------------------------------------------------------------------------
# Low-level join/leave/admin helpers
# ---------------------------------------------------------------------------

def _join_conversation(conversation: Conversation, user) -> None:
    member, created = ConversationMember.objects.get_or_create(
        conversation=conversation, user=user,
        defaults={"base_role": BaseConversationRole.MEMBER},
    )
    if not created and member.left_at is not None:
        member.left_at = None
        member.save(update_fields=["left_at"])


def _leave_conversation(conversation: Conversation, user) -> None:
    ConversationMember.objects.filter(
        conversation=conversation, user=user, left_at__isnull=True,
    ).update(left_at=timezone.now())


def _grant_conversation_admin(conversation: Conversation, user) -> None:
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


def _revoke_conversation_admin(conversation: Conversation, user) -> None:
    ConversationMember.objects.filter(
        conversation=conversation, user=user, base_role=BaseConversationRole.ADMIN,
    ).update(base_role=BaseConversationRole.MEMBER)


def _join_group(group: Group, user) -> None:
    membership, created = GroupMembership.objects.get_or_create(
        group=group, user=user, defaults={"role": GroupRole.MEMBER},
    )
    if not created and membership.left_at is not None:
        membership.left_at = None
        membership.save(update_fields=["left_at"])
    _join_conversation(group.conversation, user)


def _leave_group(group: Group, user) -> None:
    GroupMembership.objects.filter(
        group=group, user=user, left_at__isnull=True,
    ).update(left_at=timezone.now())
    _leave_conversation(group.conversation, user)


def _grant_group_admin(group: Group, user) -> None:
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
    _grant_conversation_admin(group.conversation, user)


def _revoke_group_admin(group: Group, user) -> None:
    GroupMembership.objects.filter(
        group=group, user=user, role=GroupRole.ADMIN,
    ).update(role=GroupRole.MEMBER)
    _revoke_conversation_admin(group.conversation, user)


def _join_community(community: Community, user) -> None:
    membership, created = CommunityMembership.objects.get_or_create(
        community=community, user=user,
        defaults={"role": CommunityRole.MEMBER, "status": CommunityMembershipStatus.ACTIVE},
    )
    if not created and membership.status != CommunityMembershipStatus.ACTIVE:
        membership.status = CommunityMembershipStatus.ACTIVE
        membership.left_at = None
        membership.save(update_fields=["status", "left_at"])


def _leave_community(community: Community, user) -> None:
    CommunityMembership.objects.filter(
        community=community, user=user, status=CommunityMembershipStatus.ACTIVE,
    ).update(status=CommunityMembershipStatus.LEFT, left_at=timezone.now())


def _grant_community_admin(community: Community, user) -> None:
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


def _revoke_community_admin(community: Community, user) -> None:
    CommunityMembership.objects.filter(
        community=community, user=user, role=CommunityRole.ADMIN,
    ).update(role=CommunityRole.MEMBER)
