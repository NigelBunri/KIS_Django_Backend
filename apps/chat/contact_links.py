"""
Private 1:1 contact/share links (PART 7 of the deep-links spec).

Two endpoints, deliberately split by authentication requirement:

  - ContactShareLinkMeView (authenticated, owner-only): get-or-create,
    regenerate (= revoke the old link, since a fresh token immediately
    invalidates whoever still has the old one), and deactivate.
  - RedeemContactLinkView (authenticated, any user): the ONLY place a
    resolved contact-link token ever turns into a real action. Reuses
    get_or_create_direct_conversation(..., use_request_flow=True) - the
    exact same pending-DM-request path and UserBlock check every other
    "message this person" entry point in this app already goes through
    (see ConversationViewSet.direct() in views.py). This file adds no new
    messaging mechanism - only a second way to arrive at that one.

The UNAUTHENTICATED preview (name/avatar only, resolved before the
visitor has logged in) is handled separately by apps.core.link_resolver -
deliberately does NOT return owner.id, so an anonymous caller can never
learn a real user's database identifier from a contact link alone. Only
after the visitor is authenticated does RedeemContactLinkView resolve the
token server-side and act on it - the client never needs to know the
owner's id at any point in this flow.
"""
from __future__ import annotations

import secrets

from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chat.models import ContactShareLink
from apps.chat.services import get_or_create_direct_conversation
from apps.moderation.models import UserBlock
from django.db.models import Q


def _invite_link(token: str) -> str | None:
    from django.conf import settings

    base = str(getattr(settings, "KIS_WEBSITE_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/join/contact/{token}"


class ContactShareLinkMeView(APIView):
    """
    GET    - return the current link (creates one if absent).
    POST   - regenerate the token (invalidates the old link immediately).
    DELETE - deactivate (is_active=False) without generating a new token.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        link, _ = ContactShareLink.objects.get_or_create(
            owner=request.user,
            defaults={"token": secrets.token_urlsafe(24)},
        )
        return self._respond(link)

    def post(self, request):
        link, created = ContactShareLink.objects.get_or_create(
            owner=request.user,
            defaults={"token": secrets.token_urlsafe(24)},
        )
        if not created:
            link.token = secrets.token_urlsafe(24)
            link.is_active = True
            link.save(update_fields=["token", "is_active", "updated_at"])
        return self._respond(link)

    def delete(self, request):
        link = ContactShareLink.objects.filter(owner=request.user).first()
        if link:
            link.is_active = False
            link.save(update_fields=["is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _respond(self, link: ContactShareLink):
        return Response({
            "token": link.token,
            "is_active": link.is_active,
            "invite_link": _invite_link(link.token),
        })


class RedeemContactLinkView(APIView):
    """
    POST /api/v1/contact-links/redeem/  { "token": "..." }

    The only place a contact-link token is ever converted into a real
    conversation. Requires authentication - the caller must already be
    signed in to KIS (see PART 5 of the spec: an unauthenticated click
    only ever sees the safe preview via apps.core.link_resolver; this is
    what runs once the visitor has logged in and tapped "Message").
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = str(request.data.get("token") or "").strip()
        if not token:
            raise ValidationError({"token": "This field is required."})

        link = ContactShareLink.objects.select_related("owner").filter(token=token).first()
        if not link:
            return Response({"detail": "This link is invalid."}, status=status.HTTP_404_NOT_FOUND)
        if not link.is_active:
            return Response({"detail": "This link is no longer active."}, status=status.HTTP_410_GONE)
        if link.is_expired():
            return Response({"detail": "This link has expired."}, status=status.HTTP_410_GONE)

        owner = link.owner
        if owner.id == request.user.id:
            raise ValidationError({"detail": "This is your own contact link."})

        # Same check ConversationViewSet.direct() already applies for every
        # other "message this person" entry point - a contact link must not
        # be a way to bypass an existing block in either direction.
        blocked = UserBlock.objects.filter(
            Q(blocker=request.user, blocked=owner) | Q(blocker=owner, blocked=request.user)
        ).exists()
        if blocked:
            raise PermissionDenied("You can't start a conversation with this user.")

        conversation, created = get_or_create_direct_conversation(
            user_a=request.user,
            user_b=owner,
            initiator=request.user,
            use_request_flow=True,
        )

        ContactShareLink.objects.filter(id=link.id).update(use_count=link.use_count + 1)

        # Deliberately NOT ConversationDetailSerializer here (unlike
        # ConversationViewSet.direct()) - it serializes every member's
        # full user object, phone included, which is exactly what a
        # contact link exists to prevent the recipient from ever seeing.
        # A normal DM already implies both sides know each other's
        # number; a contact link's entire premise is that they don't, and
        # redeeming one must not be the moment that number leaks. The
        # client already has its own normal (separately-scoped)
        # conversation list/detail fetch for everything else it needs.
        return Response(
            {"conversation_id": str(conversation.id), "request_state": conversation.request_state},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
