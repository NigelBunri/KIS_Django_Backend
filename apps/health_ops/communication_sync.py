# apps/health_ops/communication_sync.py
#
# Partner Account -> Community/Channel sync layer for Health, mirroring
# apps.broadcasts.education_communication_sync exactly (same architectural
# principle: Health is the source of truth for institution/service/
# membership state; communication spaces are a connected layer that
# reacts to it, never the reverse). Shares its low-level join/leave/
# admin-grant primitives via apps.partners.communication_sync_helpers
# rather than duplicating them.
#
# Mapping (flatter than Education's Program/Class/Course, since Health
# has no middle tier): HealthInstitution -> Community (one per
# institution, the institution-wide space every staff member and active
# patient ends up a member of), HealthService -> Channel (one per
# service, nested under the institution's Community, for patients
# actively receiving that service plus staff).
#
# Gated entirely on HealthInstitution.partner being set — every ensure_*
# below is a guarded no-op (returns None) otherwise, and Health continues
# to work exactly as it does without this module existing. Disconnecting
# the partner (HealthInstitutionPartnerConnectView.delete) must never
# delete health data or existing communication rooms/membership — see
# on_institution_partner_disconnected, which only unlinks.
from __future__ import annotations

from django.db import transaction

from apps.channels.models import Channel
from apps.communities.models import Community, CommunityJoinPolicy, CommunityVisibility
from apps.partners.communication_sync_helpers import (
    create_conversation,
    grant_community_admin,
    grant_conversation_admin,
    join_community,
    join_conversation,
    unique_slug,
)
from apps.chat.models import ConversationType


@transaction.atomic
def ensure_institution_community(institution) -> Community | None:
    if not institution.partner_id:
        return None
    if institution.community_id:
        return institution.community
    owner = institution.owner
    community = Community.objects.create(
        partner=institution.partner,
        owner=owner,
        name=institution.name,
        slug=unique_slug(Community, institution.name),
        description=f"{institution.name} on KIS Health",
        visibility=CommunityVisibility.PUBLIC,
        join_policy=CommunityJoinPolicy.REQUEST,
    )
    join_community(community, owner)
    grant_community_admin(community, owner)
    institution.community = community
    institution.save(update_fields=["community"])
    return community


@transaction.atomic
def ensure_service_channel(service) -> Channel | None:
    institution = service.institution
    if not institution.partner_id:
        return None
    if service.channel_id:
        return service.channel
    owner = institution.owner
    community = ensure_institution_community(institution)
    conversation = create_conversation(
        conv_type=ConversationType.CHANNEL, title=service.name, owner=owner,
    )
    channel = Channel.objects.create(
        partner=institution.partner,
        community=community,
        name=service.name,
        slug=unique_slug(Channel, service.name, partner=institution.partner),
        owner=owner,
        conversation=conversation,
    )
    service.channel = channel
    service.save(update_fields=["channel"])
    return channel


def sync_membership_communication(membership) -> None:
    """Call after a HealthInstitutionMembership is saved active/inactive.
    OWNER/ADMIN/MANAGER roles get admin on the institution Community;
    STAFF/MEMBER get plain membership. An inactive membership is left
    alone here (matching Education's revoke_staff_assignment_communication
    split) — explicit removal is a separate, deliberate action, not an
    automatic side effect of this sync."""
    if not membership.is_active:
        return
    institution = membership.institution
    community = ensure_institution_community(institution)
    if not community:
        return
    user = membership.user
    from apps.health_ops.models import MembershipRole

    if membership.role in (MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.MANAGER):
        grant_community_admin(community, user)
    else:
        join_community(community, user)


def sync_patient_service_communication(workflow_session) -> None:
    """Call after a ServiceWorkflowSession is created (a patient actively
    using a service) — see apps.health_ops.views._start_workflow_session.
    A no-op when the institution has no Partner Account connected."""
    service = workflow_session.service
    channel = ensure_service_channel(service)
    if not channel:
        return
    join_conversation(channel.conversation, workflow_session.user)


def on_institution_partner_connected(institution) -> None:
    """Symmetric counterpart to on_institution_partner_disconnected, and
    the fix for the same class of bug Education's acceptance test found:
    an institution that already has members and patients with open
    workflow sessions *before* ever connecting a Partner Account would
    otherwise have all of them silently stranded with no communication
    access, since every ensure_*/sync_* above only fires on a *new*
    event. This backfills exactly once at connect time; every call inside
    is already idempotent, so re-running this (disconnect then reconnect)
    is always safe."""
    from apps.health_ops.models import ServiceWorkflowSession, WorkflowStatus

    ensure_institution_community(institution)
    for membership in institution.memberships.filter(is_active=True).select_related("user"):
        sync_membership_communication(membership)
    open_sessions = (
        ServiceWorkflowSession.objects.filter(institution=institution)
        .exclude(status__in=[WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED])
        .select_related("service", "user")
    )
    for workflow_session in open_sessions:
        sync_patient_service_communication(workflow_session)


def on_institution_partner_disconnected(institution) -> None:
    """The one function that runs when institution.partner is cleared.
    Deliberately does nothing to health data or existing communication
    rooms/membership — an unlink, not a delete. Any Community/Channel FK
    that was already set stays exactly as-is; new ones simply won't be
    created until a Partner Account is connected again."""
    return
