"""
Custom domain support via Cloudflare for SaaS (Custom Hostnames API) on
the existing kingdomimpactventures.org zone, with the already-deployed
`kiv-website` Worker as the origin — the "Worker as origin" pattern,
free-tier-eligible (100 free custom hostnames before $0.10/mo each).

This module is the APPLICATION code for that integration. It is
deliberately built ahead of, and gated on, a SEPARATE one-time
infrastructure step that has NOT been done yet as of writing this:
- a Cloudflare for SaaS zone config on the production account
- an originless fallback-origin DNS record
- a Worker Route (*/*) so custom-hostname traffic reaches kiv-website

That step touches real, shared, already-live production infrastructure
(the same Cloudflare account/zone serving kingdomimpactventures.org
right now) and was explicitly deferred pending a separate confirmation
— see the plan this was built from. Until CLOUDFLARE_API_TOKEN and
CLOUDFLARE_ZONE_ID are configured (they aren't, by default), every
function here raises CustomDomainsNotConfigured rather than silently
no-op'ing or crashing on a missing setting — callers turn that into a
clear "custom domains aren't available yet" response, not a 500.
"""
from __future__ import annotations

import re

import requests
from django.conf import settings
from rest_framework.exceptions import ValidationError

CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"
REQUEST_TIMEOUT_SECONDS = 10

_DOMAIN_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.[A-Za-z0-9-]{1,63})+$")


class CustomDomainsNotConfigured(Exception):
    pass


def custom_domains_enabled() -> bool:
    return bool(getattr(settings, "CLOUDFLARE_API_TOKEN", "") and getattr(settings, "CLOUDFLARE_ZONE_ID", ""))


def _require_configured():
    if not custom_domains_enabled():
        raise CustomDomainsNotConfigured(
            "Custom domains aren't available on this deployment yet — the Cloudflare zone hasn't been configured."
        )


def validate_domain_format(domain: str) -> str:
    domain = domain.strip().lower().rstrip(".")
    if not _DOMAIN_RE.match(domain):
        raise ValidationError({"custom_domain": "That doesn't look like a valid domain (e.g. www.example.com)."})
    if domain.endswith("kingdomimpactventures.org"):
        raise ValidationError({"custom_domain": "Custom domains must be on your own domain, not kingdomimpactventures.org."})
    return domain


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.CLOUDFLARE_API_TOKEN}", "Content-Type": "application/json"}


def register_custom_hostname(domain: str) -> dict:
    """Registers the hostname with Cloudflare for SaaS. Returns
    {cloudflare_id, cname_target, txt_record: {name, value}} — the
    records the domain owner must add at THEIR OWN DNS provider (this
    account's zone is kingdomimpactventures.org, not the customer's
    domain, so nothing here touches the customer's DNS directly)."""
    _require_configured()
    response = requests.post(
        f"{CLOUDFLARE_API_BASE}/zones/{settings.CLOUDFLARE_ZONE_ID}/custom_hostnames",
        headers=_headers(),
        json={"hostname": domain, "ssl": {"method": "http", "type": "dv"}},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    payload = response.json()
    if not payload.get("success"):
        errors = payload.get("errors") or [{"message": "Unknown error"}]
        raise ValidationError({"custom_domain": errors[0].get("message", "Unable to register this domain with Cloudflare.")})

    result = payload["result"]
    ownership = result.get("ownership_verification") or {}
    return {
        "cloudflare_id": result["id"],
        "cname_target": getattr(settings, "CLOUDFLARE_FALLBACK_ORIGIN_HOSTNAME", "kingdomimpactventures.org"),
        "txt_record": {"name": ownership.get("name", ""), "value": ownership.get("value", "")},
    }


def check_hostname_status(cloudflare_id: str) -> str:
    """Returns one of WebsiteCustomDomainStatus's values based on
    Cloudflare's own verification/SSL-issuance state."""
    _require_configured()
    response = requests.get(
        f"{CLOUDFLARE_API_BASE}/zones/{settings.CLOUDFLARE_ZONE_ID}/custom_hostnames/{cloudflare_id}",
        headers=_headers(),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    payload = response.json()
    if not payload.get("success"):
        return "failed"

    result = payload["result"]
    ssl_status = (result.get("ssl") or {}).get("status", "")
    if result.get("status") == "active" and ssl_status == "active":
        return "active"
    if result.get("status") in ("pending_deletion", "moved") or ssl_status in ("expired",):
        return "failed"
    return "pending"


def deregister_custom_hostname(cloudflare_id: str) -> None:
    _require_configured()
    requests.delete(
        f"{CLOUDFLARE_API_BASE}/zones/{settings.CLOUDFLARE_ZONE_ID}/custom_hostnames/{cloudflare_id}",
        headers=_headers(),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
