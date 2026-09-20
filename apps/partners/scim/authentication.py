"""Bearer-token authentication for the SCIM 2.0 Users endpoints.

SCIM has no auth scheme of its own - RFC 7644 defers to whatever the
provisioning client and the service agree on out of band. Okta/Azure AD
both do a static bearer token by default, so that's what's implemented
here: the token an admin generates in PartnerIntegrationsPanel.tsx (kind
"scim") and pastes into their IdP's SCIM app config.

This is intentionally NOT tied to a Django User - the IdP's provisioning
job is the caller, not a person with a KIS session. `request.auth` is set
to the resolved `Partner` so views know which tenant's roster they're
allowed to touch; `request.user` stays anonymous.
"""
from __future__ import annotations

import hmac

from django.contrib.auth.models import AnonymousUser
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from apps.partners.models import Partner, PartnerIntegration


class ScimBearerTokenAuthentication(BaseAuthentication):
    def authenticate(self, request):
        partner_slug = request.parser_context["kwargs"].get("partner_slug") if request.parser_context else None
        if not partner_slug:
            return None

        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.startswith("Bearer "):
            raise AuthenticationFailed("Missing bearer token.")
        provided_token = header[len("Bearer "):].strip()
        if not provided_token:
            raise AuthenticationFailed("Missing bearer token.")

        partner = Partner.objects.filter(slug=partner_slug).first()
        if not partner:
            raise AuthenticationFailed("Unknown tenant.")

        integration = PartnerIntegration.objects.filter(
            partner=partner, kind=PartnerIntegration.KIND_SCIM, is_enabled=True
        ).first()
        if not integration:
            raise AuthenticationFailed("SCIM is not enabled for this tenant.")

        configured_token = str((integration.config or {}).get("token") or "")
        if not configured_token or not hmac.compare_digest(configured_token, provided_token):
            raise AuthenticationFailed("Invalid bearer token.")

        return (AnonymousUser(), partner)

    def authenticate_header(self, request):
        return "Bearer"
