"""
Public, unauthenticated resolver for KIS's shareable "join" deep links
(https://kingdomimpactventures.org/join/<type>/<token>). Used by:

  - The website's own /join/[type]/[token] landing page, to show a safe
    preview ("Join Kingdom Youth community") and an "Open in KIS" button
    before the visitor has logged in (see PART 5 of the deep-links spec:
    unauthenticated visitors must see the intended action, not a wall).
  - The mobile app, for the same purpose when it's not yet certain the
    click will actually open the app (e.g. a share-sheet preview).

Deliberately returns ONLY safe-to-show fields per type - never a phone
number, email, or any other private identifier. See each resolver
function's own docstring for exactly what is/isn't included.

Calls are NOT resolved here: call data lives in Nest/MongoDB, and Nest's
own GET /calls/join/:token requires authentication (calls.controller.ts),
matching the existing architecture where a call's real details are only
shown once the user is signed in. This resolver returns a generic
"open the app" acknowledgement for call/broadcast-call tokens rather than
inventing a second, unauthenticated call-preview path.
"""
from __future__ import annotations

from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

SUPPORTED_TYPES = {"call", "broadcast-call", "group", "community", "partner", "contact", "referral"}


def _not_found():
    return Response({"status": "invalid", "detail": "This link is invalid."}, status=404)


def _resolve_group(token: str):
    from apps.groups.models import Group

    group = Group.objects.filter(invite_token=token).first()
    if not group:
        return _not_found()
    if group.is_archived:
        return Response({"status": "revoked", "detail": "This group invite is no longer active."}, status=410)
    return Response({
        "status": "ok",
        "type": "group",
        "name": group.name,
    })


def _resolve_community(token: str):
    from apps.communities.models import Community

    community = Community.objects.filter(invite_token=token).first()
    if not community:
        return _not_found()
    if not community.is_active:
        return Response({"status": "revoked", "detail": "This community invite is no longer active."}, status=410)
    if not community.allow_join_link:
        # The token still technically matches a row, but the community's
        # own admin-controlled kill-switch means this specific link was
        # intentionally turned off - same user-facing outcome as revoked,
        # distinct reason kept server-side only (not exposed to the client).
        return Response({"status": "revoked", "detail": "This community invite is no longer active."}, status=410)
    return Response({
        "status": "ok",
        "type": "community",
        "name": community.name,
        "description": community.description[:200] if community.description else "",
        "avatar_url": community.avatar_url or None,
    })


def _resolve_partner(token: str):
    from apps.partners.models import PartnerInvite

    invite = PartnerInvite.objects.select_related("partner").filter(code=token).first()
    if not invite:
        return _not_found()
    if not invite.is_active:
        return Response({"status": "revoked", "detail": "This invite is no longer active."}, status=410)
    if invite.is_expired:
        return Response({"status": "expired", "detail": "This invite has expired."}, status=410)
    if not invite.has_uses_remaining:
        return Response({"status": "expired", "detail": "This invite has already been used."}, status=410)
    return Response({
        "status": "ok",
        "type": "partner",
        "name": invite.partner.name,
        "avatar_url": invite.partner.avatar_url or None,
    })


def _resolve_contact(token: str):
    from apps.chat.models import ContactShareLink

    link = ContactShareLink.objects.select_related("owner").filter(token=token).first()
    if not link:
        return _not_found()
    if not link.is_active:
        return Response({"status": "revoked", "detail": "This link is no longer active."}, status=410)
    if link.is_expired():
        return Response({"status": "expired", "detail": "This link has expired."}, status=410)
    owner = link.owner
    # Deliberately excludes owner.id (and obviously phone/email) - an
    # unauthenticated caller gets only what's needed to decide whether to
    # tap "Message", never a usable identifier. See
    # apps.chat.contact_links.RedeemContactLinkView, the only place this
    # token is ever resolved into a real action, which re-looks-up the
    # owner from the token itself once the caller is authenticated -
    # nothing here needs to round-trip an id through the client.
    display_name = getattr(owner, "display_name", None) or getattr(owner, "username", None) or "This person"
    return Response({
        "status": "ok",
        "type": "contact",
        "name": display_name,
        "avatar_url": getattr(getattr(owner, "profile", None), "avatar_url", None) or None,
    })


def _resolve_referral(token: str):
    """A ReferralCode is permanent and never revoked/expired (one per user,
    created lazily, unique(). "This link is invalid" is the only failure
    mode here — never expired/revoked, unlike the other resolvers.

    Logs a real, minimal "link clicked" AuditLog event against the
    referrer (reusing the existing audit mechanism rather than inventing a
    new attribution-events model) — this is Part 8's "link clicked" state.
    Deliberately does NOT create or touch any Referral row: clicking a
    link is not attribution, and must never itself grant or imply a
    reward. Attribution only happens for real at registration
    (apps.referrals.services.register_referral, called with the referrer's
    own code, independent of whether this endpoint was ever hit)."""
    from apps.accounts.models import AuditLog
    from apps.referrals.models import ReferralCode

    code = (token or "").strip().upper()
    code_record = ReferralCode.objects.select_related("user").filter(code=code).first()
    if not code_record:
        return _not_found()

    referrer = code_record.user
    AuditLog.log(referrer, "referral.link_clicked", {"referral_code": code})

    display_name = getattr(referrer, "display_name", None) or getattr(referrer, "username", None) or "A KIS member"
    return Response({
        "status": "ok",
        "type": "referral",
        "name": display_name,
        "avatar_url": getattr(getattr(referrer, "profile", None), "avatar_url", None) or None,
        "referral_code": code,
    })


def _resolve_call(token: str, link_type: str):
    # No server-side validity check here on purpose (see module docstring)
    # - Django doesn't own call data and Nest's own join-by-token endpoint
    # already requires auth, so a fake "valid" response here would be
    # meaningless. The web landing page shows a generic "open the app"
    # CTA; the app itself does the real token validation once the user is
    # signed in, exactly as it already does for a link opened while the
    # app was already installed.
    return Response({
        "status": "ok",
        "type": link_type,
        "name": None,
    })


_RESOLVERS = {
    "group": _resolve_group,
    "community": _resolve_community,
    "partner": _resolve_partner,
    "contact": _resolve_contact,
    "referral": _resolve_referral,
}


class PublicLinkResolveView(APIView):
    """GET /api/v1/links/resolve/<type>/<token>/"""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "link_resolve"

    def get(self, request, link_type: str, token: str):
        link_type = (link_type or "").strip().lower()
        token = (token or "").strip()
        if link_type not in SUPPORTED_TYPES or not token:
            return Response({"status": "invalid", "detail": "This link is invalid."}, status=404)
        if link_type in ("call", "broadcast-call"):
            return _resolve_call(token, link_type)
        return _RESOLVERS[link_type](token)
