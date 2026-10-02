# apps/health_ops/partner_sync.py
#
# Partner Account auto-add + notification for Health institutions —
# scoped-down sibling of apps.broadcasts.education_communication_sync for
# this domain. Education's version also auto-creates/joins a per-entity
# Community/Group/Channel (Program->Community, Class->Group, Course->
# Channel); Health has no equivalent per-service communication-room
# infrastructure today (HealthInstitution has no community/group/channel
# FK, and nothing elsewhere in this app creates one), so this only ever
# does the two things that don't depend on that: add the user to the
# institution's Partner Account, and notify them. Building the deeper
# per-service room system is a separate, larger feature.
from __future__ import annotations

from apps.partners.models import PartnerMembership, PartnerMembershipStatus


def ensure_partner_membership(institution, user) -> bool:
    """Idempotent get-or-create on the partner+user unique_together.
    Never removes membership on its own — matches
    education_communication_sync._ensure_partner_membership's reasoning
    exactly (losing access to one service session shouldn't silently
    evict someone who may still be active elsewhere at the same
    institution). Returns whether a new row was created, so the caller
    can notify only once per real addition."""
    if not institution.partner_id:
        return False
    _membership, created = PartnerMembership.objects.get_or_create(
        partner=institution.partner,
        user=user,
        defaults={"status": PartnerMembershipStatus.MEMBER},
    )
    return created


def notify_added_to_partner(*, institution, user, label: str) -> None:
    if not institution.partner_id:
        return
    from apps.notifications.services import create_notification

    create_notification(
        user_id=user.id,
        type="HEALTH_PARTNER_MEMBERSHIP_ADDED",
        title=f"You've been added to {label}",
        body=f"{institution.name} added you to its Partner Account — tap to see your Partner sections.",
        target_type="partner",
        target_id=str(institution.partner_id),
        priority="MEDIUM",
        dedup_key=f"health_partner_added:{institution.partner_id}:{user.id}",
        context={"institution_id": str(institution.id)},
    )


def sync_patient_partner_membership(*, institution, user, label: str) -> None:
    """Call after a patient-facing action that genuinely associates a
    user with the institution (today: starting a ServiceWorkflowSession —
    see apps.health_ops.views._start_workflow_session). A no-op when the
    institution has no Partner Account connected."""
    if ensure_partner_membership(institution, user):
        notify_added_to_partner(institution=institution, user=user, label=label)
