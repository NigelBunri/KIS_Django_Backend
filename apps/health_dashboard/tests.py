from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.broadcasts.models import (
    BroadcastHealthInstitution,
    BroadcastHealthInstitutionMember,
    BroadcastHealthInstitutionService,
    BroadcastHealthProfile,
)
from apps.health_dashboard.models import HealthDashboardInstitutionLandingPage


User = get_user_model()


def _create_user(phone: str, username: str) -> User:
    return User.objects.create_user(
        phone=phone,
        country="CM",
        password="pass1234",
        username=username,
        display_name=username.title(),
        phone_country_code="+237",
        phone_number=phone.replace("+237", ""),
    )


@override_settings(SECURE_SSL_REDIRECT=False)
class HealthDashboardLandingPageApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.owner = _create_user("+237690100001", "hd_owner")
        self.manager = _create_user("+237690100002", "hd_manager")
        self.viewer = _create_user("+237690100003", "hd_viewer")

        self.health_profile = BroadcastHealthProfile.objects.create(profile=self.owner.profile, payload={})
        self.institution_uid = "inst-landing-001"
        self.institution = BroadcastHealthInstitution.objects.create(
            health_profile=self.health_profile,
            institution_uid=self.institution_uid,
            institution_type="clinic",
            name="City Health Center",
            owner_user=self.owner,
        )
        BroadcastHealthInstitutionMember.objects.create(
            institution=self.institution,
            member_uid="member-manager-1",
            name="Manager User",
            role="manager",
            user=self.manager,
            phone=self.manager.phone,
            email="manager@example.com",
        )
        BroadcastHealthInstitutionService.objects.create(
            institution=self.institution,
            service_uid="svc-general",
            name="General Consultation",
            description="Walk-in and remote consultations.",
            active=True,
            base_price_cents=15000,
            medium_ids=["in_person", "virtual"],
            medium_names=["In Person", "Virtual"],
        )

        self.list_url = reverse("health-dashboard-institutions")
        self.landing_url = reverse("health-institution-landing-page", kwargs={"institution_id": self.institution_uid})

    def _landing_payload(self):
        return {
            "title": "City Health Center",
            "description": "Primary and preventive healthcare.",
            "heroHeadline": "Compassionate care, every day.",
            "heroCtaLabel": "Book Appointment",
            "heroCtaUrl": "https://example.com/book",
            "backgroundImageUrl": "https://example.com/image.jpg",
            "logoUrl": "https://example.com/logo.jpg",
            "contact": {
                "primaryPhone": "+237690100001",
                "email": "hello@cityhealth.test",
            },
            "address": {
                "lineOne": "12 Health Street",
                "city": "Douala",
                "country": "CM",
            },
            "services": [
                {
                    "institutionServiceUid": "svc-general",
                    "name": "General Consultation",
                    "description": "Comprehensive consult package.",
                    "priceCents": 15000,
                    "isActive": True,
                }
            ],
            "images": [{"imageUrl": "https://example.com/gallery.jpg", "caption": "Reception"}],
            "socialLinks": [{"platform": "instagram", "url": "https://instagram.com/cityhealth"}],
            "operatingHours": [{"dayKey": "mon", "opensAt": "08:00", "closesAt": "17:00", "isClosed": False}],
        }

    def test_non_manager_cannot_create_landing_page(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.post(self.landing_url, self._landing_payload(), format="json", secure=True)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_manager_can_create_and_update_landing_page(self):
        self.client.force_authenticate(self.manager)

        create_response = self.client.post(self.landing_url, self._landing_payload(), format="json", secure=True)
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(HealthDashboardInstitutionLandingPage.objects.count(), 1)
        # Not published yet (the payload doesn't set isPublished) — a draft
        # landing page isn't actually clickable/visible until published.
        self.assertFalse(create_response.data.get("institutionNameClickable"))

        patch_response = self.client.patch(
            self.landing_url,
            {"title": "City Health Center Updated", "isPublished": True},
            format="json",
            secure=True,
        )
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_response.data.get("title"), "City Health Center Updated")
        self.assertTrue(patch_response.data.get("institutionNameClickable"))

    def test_institution_list_clickable_flag_depends_on_landing_page(self):
        self.client.force_authenticate(self.owner)
        before_response = self.client.get(self.list_url, secure=True)
        self.assertEqual(before_response.status_code, status.HTTP_200_OK)

        before_row = next(
            row for row in before_response.data.get("results", []) if row.get("institutionId") == self.institution_uid
        )
        self.assertFalse(before_row.get("institutionNameClickable"))
        self.assertFalse(before_row.get("hasLandingPage"))

        create_response = self.client.post(self.landing_url, self._landing_payload(), format="json", secure=True)
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)

        # hasLandingPage becomes true as soon as the row exists, but
        # institutionNameClickable correctly requires it to be published
        # too (an unpublished draft isn't visible to anyone but managers).
        publish_response = self.client.patch(
            self.landing_url, {"isPublished": True}, format="json", secure=True,
        )
        self.assertEqual(publish_response.status_code, status.HTTP_200_OK)

        after_response = self.client.get(self.list_url, secure=True)
        after_row = next(
            row for row in after_response.data.get("results", []) if row.get("institutionId") == self.institution_uid
        )
        self.assertTrue(after_row.get("institutionNameClickable"))
        self.assertTrue(after_row.get("hasLandingPage"))
        self.assertIn(f"/health/institutions/{self.institution_uid}/landing-page/", after_row.get("landingPageUrl", ""))

    def test_profile_owner_can_manage_even_when_owner_user_is_missing(self):
        self.institution.owner_user = None
        self.institution.save(update_fields=["owner_user", "updated_at"])

        self.client.force_authenticate(self.owner)
        create_response = self.client.post(self.landing_url, self._landing_payload(), format="json", secure=True)
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)

        list_response = self.client.get(self.list_url, secure=True)
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        row = next(
            item for item in list_response.data.get("results", []) if item.get("institutionId") == self.institution_uid
        )
        self.assertEqual(row.get("role"), "owner")
        self.assertTrue(row.get("can_manage"))

    def test_public_user_can_view_existing_landing_page(self):
        self.client.force_authenticate(self.owner)
        create_response = self.client.post(self.landing_url, self._landing_payload(), format="json", secure=True)
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        # An unpublished landing page correctly 404s for anonymous callers
        # (see HealthDashboardLandingPageView.get) — publish it first so
        # this test actually exercises "public user views an existing,
        # live landing page" as intended.
        publish_response = self.client.patch(
            self.landing_url, {"isPublished": True}, format="json", secure=True,
        )
        self.assertEqual(publish_response.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(user=None)
        get_response = self.client.get(self.landing_url, secure=True)
        self.assertEqual(get_response.status_code, status.HTTP_200_OK)
        self.assertEqual(get_response.data.get("institutionId"), self.institution_uid)
        self.assertTrue(get_response.data.get("institutionNameClickable"))
