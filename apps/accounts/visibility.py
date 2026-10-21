# apps/accounts/visibility.py
#
# Profile field-visibility enforcement, extracted out of apps/accounts/views.py
# so other apps (communities, groups, statuses, chat, calls - anywhere a
# user's profile fields get surfaced to someone else) can enforce the exact
# same rules without importing from a views module (which already imports
# from serializers.py, so the reverse import would be circular).
#
# This is the single existing per-field visibility mechanism in KIS
# (ProfileFieldVisibility: public / contacts / custom / private) - used here
# to gate Profile.work_status (the "Open to Work" / "Hiring" status) the same
# way avatar/headline/bio/etc are already gated in _build_profile_payload.
# Before this, work_status was returned completely ungated everywhere it was
# exposed (apps.accounts' own profile payload, apps.communities/apps.groups'
# member serializers, apps.statuses' author dicts) - a private/contacts-only
# status was visible to literally anyone who could see the user at all.
from typing import Iterable, Optional

from django.db.models import Q

from apps.core.phone_utils import to_e164

from .models import (
    ProfileArticleAllowTarget,
    ProfileFieldVisibility,
    ProfileFieldVisibilityAllowTarget,
    User,
    UserContact,
)

WORK_STATUS_FIELD_KEY = "work_status"


def _digits_only(value: str | None) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _phone_variants(value: str | None, country_hint: str = "CM") -> set[str]:
    raw = str(value or "").strip()
    if not raw:
        return set()
    variants = {raw}
    digits = _digits_only(raw)
    if digits:
        variants.add(digits)
        variants.add(f"+{digits}")
        if digits.startswith("00") and len(digits) > 2:
            variants.add(digits[2:])
            variants.add(f"+{digits[2:]}")
    try:
        e164 = to_e164(raw, country_hint or "CM")
        variants.add(e164)
        if e164.startswith("+"):
            variants.add(e164[1:])
    except Exception:
        pass
    return {item for item in variants if item}


def _user_phone_variants(user: Optional[User]) -> set[str]:
    if not user:
        return set()
    country_hint = str(getattr(user, "country", "") or "CM").upper() or "CM"
    variants = set()
    variants.update(_phone_variants(getattr(user, "phone", None), country_hint))
    local_number = str(getattr(user, "phone_number", "") or "").strip()
    if local_number:
        variants.update(_phone_variants(local_number, country_hint))
        code = str(getattr(user, "phone_country_code", "") or "").strip()
        if code:
            variants.update(_phone_variants(f"{code}{local_number}", country_hint))
    return variants


def _allow_targets_match_viewer(viewer: Optional[User], allow_targets: Iterable[ProfileFieldVisibilityAllowTarget | ProfileArticleAllowTarget]) -> bool:
    if not viewer:
        return False
    viewer_id = str(viewer.id)
    viewer_phone_variants = _user_phone_variants(viewer)
    viewer_phone_digits = {_digits_only(item) for item in viewer_phone_variants if _digits_only(item)}
    for target in allow_targets:
        target_user_id = str(getattr(target, "target_user_id", "") or "")
        if target_user_id and target_user_id == viewer_id:
            return True
        target_phone = str(getattr(target, "target_phone", "") or "").strip()
        if target_phone and target_phone in viewer_phone_variants:
            return True
        target_phone_digits = _digits_only(getattr(target, "target_phone_number", "") or target_phone)
        if target_phone_digits and target_phone_digits in viewer_phone_digits:
            return True
    return False


def _has_contact_link(owner: User, target: User) -> bool:
    owner_phone_variants = _user_phone_variants(target)
    owner_digits = {_digits_only(item) for item in owner_phone_variants if _digits_only(item)}
    qs = UserContact.objects.filter(user=owner).filter(
        Q(contact_user=target)
        | Q(contact_phone__in=owner_phone_variants)
        | Q(contact_phone_number__in=owner_digits)
    )
    return qs.exists()


def _is_mutual_contact(owner: User, viewer: Optional[User]) -> bool:
    if not viewer:
        return False
    if owner.id == viewer.id:
        return True
    return _has_contact_link(owner, viewer) and _has_contact_link(viewer, owner)


def _can_view_visibility(
    owner: User,
    viewer: Optional[User],
    visibility: str,
    allow_targets: Iterable[ProfileFieldVisibilityAllowTarget | ProfileArticleAllowTarget],
) -> bool:
    viewer_id = str(getattr(viewer, "id", ""))
    owner_id = str(owner.id)
    if viewer_id and viewer_id == owner_id:
        return True
    normalized = str(visibility or "public").strip().lower()
    if normalized == "public":
        return True
    if normalized == "private":
        return False
    if normalized == "contacts":
        return _is_mutual_contact(owner, viewer)
    if normalized == "custom":
        return _allow_targets_match_viewer(viewer, allow_targets)
    return False


def _can_view_field(owner: User, viewer: Optional[User], rule: Optional[ProfileFieldVisibility]) -> bool:
    if not rule:
        return True
    return _can_view_visibility(owner, viewer, rule.visibility, rule.allow_targets.all())


def get_visible_work_status(owner: User, viewer: Optional[User]) -> str | None:
    """The single entry point every serializer/view should call instead of
    reading `profile.work_status` directly. Returns None (render no ring/
    badge at all) when the status is 'none', when the owner has no profile
    row, or when the viewer isn't permitted to see it per the owner's own
    ProfileFieldVisibility rule for "work_status" - same per-field privacy
    model already used for avatar/headline/bio/etc, just applied to this
    one more field instead of inventing a separate visibility system."""
    profile = getattr(owner, "profile", None)
    status = getattr(profile, "work_status", None)
    if not status or status == "none":
        return None
    rule = (
        ProfileFieldVisibility.objects.filter(user=owner, field_key=WORK_STATUS_FIELD_KEY)
        .prefetch_related("allow_targets")
        .first()
    )
    if not _can_view_field(owner, viewer, rule):
        return None
    return status


def get_visible_hiring_job_listing_id(owner: User, viewer: Optional[User]) -> str | None:
    """The single entry point for "does this user's Hiring ring have a live
    listing to tap through to right now". Returns None (render the ring
    with no tap-through affordance, same as today) unless ALL of:
      1. get_visible_work_status(owner, viewer) == 'hiring' - i.e. the
         status itself is visible to this viewer at all (same
         ProfileFieldVisibility rule, not a separate check), and
      2. Profile.hiring_job_listing is set, and
      3. that JobListing is still is_active=True.
    See Profile.hiring_job_listing's own docstring in models.py for why a
    missing/inactive listing is treated as "no active opportunity" rather
    than hiding the ring itself - that's a deliberate product decision, not
    an oversight."""
    status = get_visible_work_status(owner, viewer)
    if status != "hiring":
        return None
    profile = getattr(owner, "profile", None)
    listing = getattr(profile, "hiring_job_listing", None)
    if not listing or not listing.is_active:
        return None
    return str(listing.id)
