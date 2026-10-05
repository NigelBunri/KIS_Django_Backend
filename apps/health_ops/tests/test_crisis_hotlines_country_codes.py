from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

User = get_user_model()


def _create_user(phone: str, username: str):
    return User.objects.create_user(
        phone=phone,
        country="CM",
        password="pass1234",
        username=username,
        display_name=username.title(),
        phone_country_code="+237",
        phone_number=phone.replace("+237", ""),
    )


# Regression guard: CrisisResourcesScreen.tsx's country picker sends ISO-3166
# alpha-2 codes (GB, not UK). The backend dict previously only had "UK", so
# a UK user selecting "GB" silently got the generic DEFAULT international
# directory link instead of the real Samaritans/PAPYRUS numbers — a wrong
# result in a safety-critical feature.
@override_settings(SECURE_SSL_REDIRECT=False)
class CrisisHotlineCountryCodeTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = _create_user("+237690900201", "crisis_hotline_user")
        self.client.force_authenticate(self.user)
        self.url = reverse("crisis-hotlines")

    def test_gb_country_code_returns_real_uk_hotlines_not_default(self):
        res = self.client.get(self.url, {"country": "GB"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        names = [h["name"] for h in res.data["hotlines"]]
        self.assertIn("Samaritans", names)
        self.assertNotIn("International Association for Suicide Prevention", names)

    def test_us_country_code_still_works(self):
        res = self.client.get(self.url, {"country": "US"})
        names = [h["name"] for h in res.data["hotlines"]]
        self.assertIn("National Suicide Prevention Lifeline", names)

    def test_unknown_country_falls_back_to_default_honestly(self):
        res = self.client.get(self.url, {"country": "ZZ"})
        self.assertEqual(res.data["country"], "ZZ")
        names = [h["name"] for h in res.data["hotlines"]]
        self.assertIn("International Association for Suicide Prevention", names)
