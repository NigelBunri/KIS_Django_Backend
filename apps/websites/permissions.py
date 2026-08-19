"""
Tier-gate enforcement for the website builder — backend-enforced, never
UI-only. Follows the exact two-exception-type convention already
established by apps.accounts.tiers.resolve_profile_limit call sites
elsewhere (e.g. education/health profile creation, broadcast channel
creation): PermissionDenied when the tier's limit is 0 (feature not
available at all), a separate ValidationError when a numeric quota is
exceeded.
"""
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.tiers import get_user_tier_features, normalize_limit_value, resolve_profile_limit
from apps.websites.models import Website, WebsitePage


def require_website_publish_allowed(user):
    features = get_user_tier_features(user)
    if "website_publish" in features and not features.get("website_publish"):
        raise PermissionDenied("Publishing a website requires a higher plan. Upgrade to publish.")


def require_custom_branding_allowed(user, branding_payload: dict):
    """Only enforced when the payload actually changes branding away from
    an empty/default value — reading or leaving branding untouched never
    trips this gate."""
    if not branding_payload:
        return
    features = get_user_tier_features(user)
    if "website_custom_branding" in features and not features.get("website_custom_branding"):
        raise PermissionDenied("Custom branding requires a higher plan. Upgrade to customize your website.")


def check_websites_quota(user):
    """Call BEFORE creating a new Website. Raises PermissionDenied if the
    plan doesn't include the website builder at all, ValidationError if
    the user is already at their plan's website count."""
    limit = resolve_profile_limit(
        user, "websites_limit",
        legacy_required_tier="business",
        permission_message="The website builder requires Business tier or higher.",
    )
    if limit is None:
        return
    existing = Website.objects.filter(created_by=user).count()
    if existing >= limit:
        raise ValidationError({"detail": f"Your current plan allows up to {limit} website{'s' if limit != 1 else ''}. Upgrade to create more."})


def check_pages_quota(user, website: Website):
    limit = resolve_profile_limit(
        user, "website_pages_limit",
        legacy_required_tier="business",
        permission_message="Adding pages requires Business tier or higher.",
    )
    if limit is None:
        return
    existing = website.pages.count()
    if existing >= limit:
        raise ValidationError({"detail": f"Your current plan allows up to {limit} page{'s' if limit != 1 else ''} per website. Upgrade to add more."})


def check_kis_content_sections_quota(user, page: WebsitePage, *, adding: int = 1):
    limit = resolve_profile_limit(
        user, "website_kis_content_sections_limit",
        legacy_required_tier="business",
        permission_message="Linking live KIS content requires Business tier or higher.",
    )
    if limit is None:
        return
    existing = sum(1 for s in (page.sections or []) if isinstance(s, dict) and s.get("type") == "kis_content")
    if existing + adding > limit:
        raise ValidationError({"detail": f"Your current plan allows up to {limit} live-content section{'s' if limit != 1 else ''} per page. Upgrade to add more."})
