from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.accounts.models import Profile
from apps.broadcasts.models import EducationInstitution, EducationInstitutionBroadcast
from apps.channels.models import Channel
from apps.chat.models import Conversation
from apps.commerce.models import ProductVariant, ShopLandingPage, ShopService
from apps.communities.models import Community
from apps.health_dashboard.models import (
    HealthDashboardHero,
    HealthDashboardInstitution,
    HealthDashboardInstitutionLandingPage,
    HealthDashboardLandingPageImage,
)
from apps.partners.models import Partner, PartnerOrganizationProfile
from common.media_urls import normalize_image_payload


IMAGE_FIELD_TARGETS = (
    (Profile, ("avatar_url", "cover_url")),
    (Partner, ("avatar_url",)),
    (PartnerOrganizationProfile, ("logo_url",)),
    (Community, ("avatar_url",)),
    (Channel, ("avatar_url",)),
    (Conversation, ("avatar_url",)),
    (EducationInstitutionBroadcast, ("cover_image_url",)),
    (ShopLandingPage, ("hero_image_url",)),
    (ShopService, ("image_url",)),
    (ProductVariant, ("image_url",)),
    (HealthDashboardInstitution, ("landing_background_image_url", "landing_logo_url")),
    (HealthDashboardHero, ("image_url",)),
    (HealthDashboardInstitutionLandingPage, ("logo_url", "background_image_url")),
    (HealthDashboardLandingPageImage, ("image_url",)),
)

BRANDING_IMAGE_KEYS = {
    "logo_url",
    "logoUrl",
    "image_url",
    "imageUrl",
    "banner_image_url",
    "bannerImageUrl",
    "cover_image_url",
    "coverImageUrl",
}


class Command(BaseCommand):
    help = "Normalize backend-local image URLs in stored image fields to path-only values."

    def handle(self, *args, **options):
        updated_count = 0

        for model, field_names in IMAGE_FIELD_TARGETS:
            for row in model.objects.all().iterator():
                changed_fields = []
                for field_name in field_names:
                    current = getattr(row, field_name, "")
                    normalized = normalize_image_payload(current)
                    if normalized != (current or ""):
                        setattr(row, field_name, normalized)
                        changed_fields.append(field_name)
                if changed_fields:
                    row.save(update_fields=[*changed_fields, "updated_at"] if hasattr(row, "updated_at") else changed_fields)
                    updated_count += 1

        for institution in EducationInstitution.objects.all().iterator():
            branding = institution.branding or {}
            if not isinstance(branding, dict):
                continue
            normalized_branding = {
                key: normalize_image_payload(value) if key in BRANDING_IMAGE_KEYS else value
                for key, value in branding.items()
            }
            if normalized_branding != branding:
                institution.branding = normalized_branding
                institution.save(update_fields=["branding", "updated_at"])
                updated_count += 1

        self.stdout.write(self.style.SUCCESS(f"Normalized image URLs on {updated_count} row(s)."))
