"""Shared authorization helpers for the clinical domain (Encounter,
Referral, Laboratory, clinician-authored patient records).

Kept as a separate module (rather than adding more private helpers to the
already-7000-line views.py) because these checks are reused across several
view files (views.py, extended_views.py) and are security-sensitive enough
to want one tested, single definition rather than copies drifting apart.
"""
from __future__ import annotations

from apps.health_ops.extended_models import HealthPractitioner
from apps.health_ops.models import ServiceWorkflowSession
from apps.verification.constants import VerificationBadgeCode
from apps.verification.services import current_practitioner_verification_status


def verified_practitioner_for_user(user) -> HealthPractitioner | None:
    """Returns the user's HealthPractitioner profile only if it currently
    holds the LICENSED_PROVIDER badge — never a mere profile row. A
    practitioner who hasn't been through apps.verification's admin-reviewed
    case workflow must never be treated as authorized to act clinically."""
    practitioner = HealthPractitioner.objects.filter(user=user, is_active=True).first()
    if not practitioner:
        return None
    status = current_practitioner_verification_status(practitioner)
    badge_codes = {str((badge or {}).get("code") or badge) for badge in (status.get("badges") or [])}
    if VerificationBadgeCode.LICENSED_PROVIDER not in badge_codes:
        return None
    return practitioner


def patient_has_institution_contact(patient, institution) -> bool:
    """True if the patient has genuinely interacted with this institution
    (booked/started at least one service) — the data-backed fact an
    Encounter's existence is anchored to, rather than trusting a
    practitioner's say-so that a relationship exists."""
    if institution is None:
        return False
    return ServiceWorkflowSession.objects.filter(user=patient, institution=institution).exists()


def can_create_encounter(user, *, patient, institution) -> tuple[HealthPractitioner | None, str | None]:
    from apps.health_ops.views import _is_institution_member

    practitioner = verified_practitioner_for_user(user)
    if not practitioner:
        return None, "Only a verified practitioner may create a clinical encounter."
    if not _is_institution_member(user, institution):
        return None, "You must be a member of this institution to create an encounter there."
    if not patient_has_institution_contact(patient, institution):
        return None, "This patient has no existing relationship with this institution."
    return practitioner, None


def can_write_clinical_content(user, encounter) -> bool:
    """Only the encounter's own practitioner may write clinical content
    (notes/assessment/treatment plan) into it, and only while it's still
    open — a closed encounter is an immutable record."""
    from apps.health_ops.extended_models import EncounterStatus

    if encounter.status == EncounterStatus.CLOSED:
        return False
    return encounter.practitioner.user_id == user.id
