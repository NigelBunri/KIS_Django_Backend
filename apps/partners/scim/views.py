"""SCIM 2.0 Users resource (RFC 7643/7644), MVP scope.

Only what an IdP's automatic provisioning actually needs day one: create
an account on hire, deactivate it on termination, and let periodic syncs
reconcile changes. Groups -> PartnerRole sync is a deliberate fast-follow,
not built here.

"Deactivate" never hard-deletes a User - it removes them from THIS
partner's roster (PartnerMembership -> REMOVED) since a person can belong
to more than one partner in this data model. The underlying account, and
any other partner's membership, is untouched.
"""
from __future__ import annotations

import logging
import secrets

from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.serializers import UserCreateSerializer

from ..models import PartnerMembership, PartnerMembershipStatus
from .authentication import ScimBearerTokenAuthentication

logger = logging.getLogger("security.partners.scim")

SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCIM_ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"


def _scim_error(detail: str, http_status: int) -> Response:
    return Response(
        {"schemas": [SCIM_ERROR_SCHEMA], "detail": detail, "status": str(http_status)},
        status=http_status,
        content_type="application/scim+json",
    )


def _serialize_user(user: User, membership: PartnerMembership) -> dict:
    return {
        "schemas": [SCIM_USER_SCHEMA],
        "id": str(user.id),
        "userName": user.username or user.email or str(user.id),
        "name": {"formatted": user.display_name or ""},
        "emails": [{"value": user.email, "primary": True}] if user.email else [],
        "active": membership.status != PartnerMembershipStatus.REMOVED,
        "meta": {"resourceType": "User"},
    }


def _extract_email(body: dict) -> str:
    emails = body.get("emails") or []
    if emails and isinstance(emails, list) and emails[0].get("value"):
        return str(emails[0]["value"]).strip().lower()
    # Some IdPs (Okta) send userName as the email when no explicit emails[] is configured.
    user_name = str(body.get("userName") or "").strip().lower()
    if "@" in user_name:
        return user_name
    return ""


class ScimUsersView(APIView):
    """GET/POST /scim/v2/<partner_slug>/Users"""

    authentication_classes = [ScimBearerTokenAuthentication]
    permission_classes = [AllowAny]  # authentication IS the authorization here - see ScimBearerTokenAuthentication

    def get(self, request, partner_slug=None):
        partner = request.auth
        filter_param = str(request.query_params.get("filter") or "")
        qs = PartnerMembership.objects.filter(partner=partner).select_related("user").order_by("id")

        if filter_param:
            # Minimal support for the one filter IdPs actually send on
            # sync: userName eq "<value>". Anything else is ignored
            # rather than erroring, matching how many SCIM servers behave
            # for an MVP filter grammar.
            if " eq " in filter_param and filter_param.lower().startswith("username"):
                value = filter_param.split(" eq ", 1)[1].strip().strip('"')
                qs = qs.filter(user__email__iexact=value) | qs.filter(user__username__iexact=value)

        try:
            start_index = max(1, int(request.query_params.get("startIndex", 1)))
        except (TypeError, ValueError):
            start_index = 1
        try:
            count = min(200, max(1, int(request.query_params.get("count", 50))))
        except (TypeError, ValueError):
            count = 50

        total = qs.count()
        page = list(qs[start_index - 1 : start_index - 1 + count])

        return Response(
            {
                "schemas": [SCIM_LIST_SCHEMA],
                "totalResults": total,
                "startIndex": start_index,
                "itemsPerPage": len(page),
                "Resources": [_serialize_user(m.user, m) for m in page],
            },
            content_type="application/scim+json",
        )

    def post(self, request, partner_slug=None):
        partner = request.auth
        body = request.data or {}
        email = _extract_email(body)
        if not email:
            return _scim_error("userName or emails[0].value is required.", status.HTTP_400_BAD_REQUEST)

        display_name = (body.get("name") or {}).get("formatted") or body.get("displayName") or ""
        active = body.get("active", True)

        user = User.objects.filter(email__iexact=email).first()
        created = False
        if not user:
            # No account exists for this email yet - JIT-provision one the
            # same way an enterprise SSO first-login would (no phone
            # number available, see phone_is_placeholder on User).
            placeholder_number = f"{secrets.randbelow(10**9):09d}"
            random_password = secrets.token_urlsafe(32)
            serializer = UserCreateSerializer(
                data={
                    "password": random_password,
                    "password2": random_password,
                    "display_name": display_name,
                    "phone": "",
                    "phone_country_code": "999",
                    "phone_number": placeholder_number,
                    "country": "",
                }
            )
            if not serializer.is_valid():
                return _scim_error(f"Could not provision user: {serializer.errors}", status.HTTP_400_BAD_REQUEST)
            user = serializer.save()
            user.email = email
            user.email_verified = False
            user.phone_is_placeholder = True
            user.set_unusable_password()
            user.save(update_fields=["email", "email_verified", "phone_is_placeholder", "password"])
            created = True

        membership, _ = PartnerMembership.objects.get_or_create(
            partner=partner,
            user=user,
            defaults={"status": PartnerMembershipStatus.MEMBER},
        )
        if membership.status == PartnerMembershipStatus.REMOVED and active:
            membership.status = PartnerMembershipStatus.MEMBER
            membership.removed_at = None
            membership.save(update_fields=["status", "removed_at"])
        elif not active and membership.status != PartnerMembershipStatus.REMOVED:
            membership.status = PartnerMembershipStatus.REMOVED
            membership.removed_at = timezone.now()
            membership.save(update_fields=["status", "removed_at"])

        logger.info(
            "scim.user.%s", "provisioned" if created else "membership_synced",
            extra={"partner_id": str(partner.id), "user_id": str(user.id)},
        )
        return Response(
            _serialize_user(user, membership),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            content_type="application/scim+json",
        )


class ScimUserDetailView(APIView):
    """GET/PUT/PATCH/DELETE /scim/v2/<partner_slug>/Users/<user_id>"""

    authentication_classes = [ScimBearerTokenAuthentication]
    permission_classes = [AllowAny]

    def _get_membership(self, partner, user_id) -> PartnerMembership:
        membership = (
            PartnerMembership.objects.filter(partner=partner, user_id=user_id)
            .select_related("user")
            .first()
        )
        if not membership:
            raise NotFound("No such user in this tenant.")
        return membership

    def get(self, request, partner_slug=None, user_id=None):
        membership = self._get_membership(request.auth, user_id)
        return Response(_serialize_user(membership.user, membership), content_type="application/scim+json")

    def put(self, request, partner_slug=None, user_id=None):
        membership = self._get_membership(request.auth, user_id)
        body = request.data or {}
        user = membership.user

        display_name = (body.get("name") or {}).get("formatted") or body.get("displayName")
        if display_name is not None:
            user.display_name = display_name
            user.save(update_fields=["display_name"])

        active = body.get("active", True)
        self._set_active(membership, bool(active))
        return Response(_serialize_user(user, membership), content_type="application/scim+json")

    def patch(self, request, partner_slug=None, user_id=None):
        membership = self._get_membership(request.auth, user_id)
        operations = (request.data or {}).get("Operations") or []
        for op in operations:
            path = str(op.get("path") or "").strip().lower()
            value = op.get("value")
            if path == "active":
                self._set_active(membership, bool(value))
            elif path in ("displayname", "name.formatted") and value:
                membership.user.display_name = str(value)
                membership.user.save(update_fields=["display_name"])
        return Response(_serialize_user(membership.user, membership), content_type="application/scim+json")

    def delete(self, request, partner_slug=None, user_id=None):
        # SCIM DELETE = deprovision, not a destructive account wipe -
        # matches the platform's general soft-delete-first posture.
        membership = self._get_membership(request.auth, user_id)
        self._set_active(membership, False)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @staticmethod
    def _set_active(membership: PartnerMembership, active: bool) -> None:
        if active and membership.status == PartnerMembershipStatus.REMOVED:
            membership.status = PartnerMembershipStatus.MEMBER
            membership.removed_at = None
            membership.save(update_fields=["status", "removed_at"])
        elif not active and membership.status != PartnerMembershipStatus.REMOVED:
            membership.status = PartnerMembershipStatus.REMOVED
            membership.removed_at = timezone.now()
            membership.save(update_fields=["status", "removed_at"])
