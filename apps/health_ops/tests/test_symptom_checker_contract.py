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


# Regression guard for a real crash: the RN screen (SymptomCheckerScreen.tsx)
# reads `triage_level` (string) and `recommendations` (array) from this
# response. The view previously returned `level`/`recommendation` instead,
# so every successful call left `result.triage_level` undefined and the
# screen crashed on `result.triage_level.toUpperCase()`. These tests pin the
# exact field names and the allowed `triage_level` value set the frontend's
# CheckResult type expects (emergency/urgent/moderate/mild).
@override_settings(SECURE_SSL_REDIRECT=False)
class SymptomCheckerContractTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = _create_user("+237690900101", "symptom_checker_user")
        self.client.force_authenticate(self.user)
        self.url = reverse("symptoms-check")

    def test_response_uses_frontend_contract_field_names(self):
        res = self.client.post(self.url, {"symptoms": ["chest pain"]}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("triage_level", res.data)
        self.assertIn("recommendations", res.data)
        self.assertIsInstance(res.data["recommendations"], list)
        self.assertNotIn("level", res.data)
        self.assertNotIn("recommendation", res.data)

    def test_emergency_keywords_map_to_emergency_level(self):
        res = self.client.post(self.url, {"symptoms": ["chest pain"]}, format="json")
        self.assertEqual(res.data["triage_level"], "emergency")

    def test_unmatched_symptoms_still_return_a_valid_triage_level(self):
        res = self.client.post(self.url, {"symptoms": ["zzz nonexistent symptom"]}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn(res.data["triage_level"], {"emergency", "urgent", "moderate", "mild"})
        self.assertTrue(len(res.data["recommendations"]) > 0)

    def test_all_rule_levels_are_in_frontend_allowed_set(self):
        from apps.health_ops.extended_views import MOCK_TRIAGE_RULES

        allowed = {"emergency", "urgent", "moderate", "mild"}
        for rule in MOCK_TRIAGE_RULES:
            self.assertIn(rule["triage_level"], allowed)

    def test_response_includes_honest_disclaimer(self):
        res = self.client.post(self.url, {"symptoms": ["fever"]}, format="json")
        self.assertIn("disclaimer", res.data)
        self.assertIn("not a real ai", res.data["disclaimer"].lower())

    def test_requires_authentication(self):
        self.client.force_authenticate(None)
        res = self.client.post(self.url, {"symptoms": ["fever"]}, format="json")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
