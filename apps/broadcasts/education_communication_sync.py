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
from apps.partners.models import PartnerMembership, PartnerMembershipStatus

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


def _ensure_partner_membership(institution, user) -> bool:
    """Adds the user to the institution's Partner Account itself, not just
    one of its Community/Group/Channel spaces — this is what lets them
    show up under the Partner's own member list and be reachable from the
    Partner sections in general, same idea as the existing lesson-
    enrollment -> PartnerMembership(lesson_access_only=True) pattern in
    LessonEnrollmentActionView.ensure_lesson_memberships, just for the
    Institution/Program/Class/Course enrollment system instead of the
    legacy standalone-lesson one. Idempotent (get_or_create on the
    partner+user unique_together) and never removes membership on its
    own - losing access to one course/class shouldn't silently evict
    someone who may still be active elsewhere at the same institution;
    removal is left to explicit partner-membership management. Returns
    whether a new row was actually created, so the caller can notify only
    once per real addition rather than on every idempotent re-sync."""
    if not institution.partner_id:
        return False
    _membership, created = PartnerMembership.objects.get_or_create(
        partner=institution.partner,
        user=user,
        defaults={"status": PartnerMembershipStatus.MEMBER},
    )
    return created


def _most_specific_conversation_id(*, course=None, institution_class=None):
    """Same course > class specificity order used throughout this module -
    the conversation a 'you've been added' notification should open
    straight into. Program-only (Community) has no Conversation-backed
    room of its own, so callers fall back to the Partner itself for that
    case (see _notify_added_to_partner)."""
    if course is not None and course.channel_id:
        return course.channel.conversation_id
    if institution_class is not None and institution_class.group_id:
        return institution_class.group.conversation_id
    return None


def _notify_added_to_partner(*, institution, user, label: str, course=None, institution_class=None) -> None:
    """Tells the learner/staffer they've been added to the institution's
    Partner Account, and gets them straight to where the actual
    conversation lives when there is one (the Channel/Group this specific
    enrollment/assignment unlocked) - falling back to the Partner itself
    (the one target every notification type below Education already knows
    how to route to - see ProfileNotificationDetailScreen.tsx's
    buildRouteNotificationData) when the entity is Program-only and so has
    only a Community, not a Conversation-backed room."""
    if not institution.partner_id:
        return
    from apps.notifications.services import create_notification

    conversation_id = _most_specific_conversation_id(course=course, institution_class=institution_class)
    title = f"You've been added to {label}"
    body = f"{institution.name} added you to its Partner Account — tap to start the conversation."
    if conversation_id:
        create_notification(
            user_id=user.id,
            type="EDUCATION_PARTNER_MEMBERSHIP_ADDED",
            title=title,
            body=body,
            target_type="conversation",
            target_id=str(conversation_id),
            priority="MEDIUM",
            dedup_key=f"education_partner_added:{institution.partner_id}:{user.id}:{conversation_id}",
            context={"institution_id": str(institution.id), "partner_id": str(institution.partner_id)},
        )
    else:
        create_notification(
            user_id=user.id,
            type="EDUCATION_PARTNER_MEMBERSHIP_ADDED",
            title=title,
            body=body,
            target_type="partner",
            target_id=str(institution.partner_id),
            priority="MEDIUM",
            dedup_key=f"education_partner_added:{institution.partner_id}:{user.id}",
            context={"institution_id": str(institution.id)},
        )


def sync_enrollment_communication(enrollment) -> None:
    active = enrollment.status in _ACTIVE_ENROLLMENT_STATUSES
    user = enrollment.user
    newly_added_to_partner = _ensure_partner_membership(enrollment.institution, user) if active else False

    course = enrollment.course if enrollment.course_id else None
    institution_class = enrollment.institution_class if enrollment.institution_class_id else None

    if enrollment.course_id:
        if active:
            channel = ensure_course_channel(course)
            if channel:
                _join_conversation(channel.conversation, user)
        elif course.channel_id:
            _leave_conversation(course.channel.conversation, user)

    if enrollment.institution_class_id:
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

    if newly_added_to_partner:
        label = course.title if course else institution_class.name if institution_class else (
            enrollment.program.title if enrollment.program_id else enrollment.institution.name
        )
        _notify_added_to_partner(
            institution=enrollment.institution, user=user, label=label,
            course=course, institution_class=institution_class,
        )


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
    newly_added_to_partner = _ensure_partner_membership(assignment.institution, user)

    course = assignment.course if assignment.course_id else None
    institution_class = assignment.institution_class if assignment.institution_class_id else None
    label = assignment.institution.name

    if assignment.course_id:
        channel = ensure_course_channel(course)
        if channel:
            _grant_conversation_admin(channel.conversation, user)
        label = course.title
    elif assignment.institution_class_id:
        group = ensure_class_group(institution_class)
        if group:
            _grant_group_admin(group, user)
        label = institution_class.name
    elif assignment.program_id:
        community = ensure_program_community(assignment.program)
        if community:
            _grant_community_admin(community, user)
        label = assignment.program.title

    if newly_added_to_partner:
        _notify_added_to_partner(
            institution=assignment.institution, user=user, label=label,
            course=course, institution_class=institution_class,
        )


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


def on_institution_partner_connected(institution) -> None:
    """The symmetric counterpart to on_institution_partner_disconnected —
    runs once, right after institution.partner is set. Found missing
    during the university-scale acceptance test: an institution that
    builds its full catalog and enrolls learners/staff *before* ever
    connecting a Partner Account (a completely normal real-world
    sequence) would otherwise have every one of those already-active
    enrollments/assignments silently stranded with no communication
    access — every ensure_*/sync_* function above only ever fires on a
    *new* enrollment/assignment event, never retroactively. This backfills
    exactly once at connect time; every call inside is already idempotent
    (ensure_* reuses an existing room, sync_* reuses existing membership),
    so re-running this (e.g. disconnect then reconnect) is always safe."""
    for enrollment in institution.enrollments.filter(
        status__in={EducationEnrollmentStatus.ENROLLED, EducationEnrollmentStatus.COMPLETED},
    ).select_related("program", "institution_class", "course"):
        sync_enrollment_communication(enrollment)
    for assignment in institution.staff_assignments.filter(
        status=EducationInstitutionStaffAssignmentStatus.ACTIVE,
    ).select_related("membership__user", "program", "institution_class", "course"):
        sync_staff_assignment_communication(assignment)


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
