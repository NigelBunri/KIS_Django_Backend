import hashlib
import hmac
import json
import uuid
from io import StringIO

from django.core.management import call_command
from django.urls import reverse
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User

from django.utils import timezone

from .constants import VerificationBadgeCode, VerificationBadgeStatus, VerificationCaseStatus, VerificationSubjectType
from .models import VerificationAuditEvent, VerificationBadge, VerificationCase
from .providers import get_provider_adapter, provider_public_status
from .services import (
    get_or_create_subject,
    public_trust_summary,
    schedule_verification_expiry_notifications,
    start_education_institution_verification_case,
    start_health_institution_verification_case,
    start_partner_verification_case,
    start_user_verification_case,
    unified_identity_trust_overview,
    user_subject_for,
    verification_summary,
)


def _grant_verification_badge_feature(user) -> None:
    # UserVerificationStartView correctly enforces require_feature(user,
    # "verification_badge", ...) — a real, intentional Business-Pro-and-up
    # paywall (see apps/accounts/tier_presets.py). These tests predate that
    # gate and need a qualifying tier to exercise the verification flow
    # itself rather than being blocked by the (correct) paywall.
    from apps.accounts.tiers import ensure_default_account_tiers

    ensure_default_account_tiers()
    user.tier = "Business Pro"
    user.save(update_fields=["tier"])


class UserVerificationFlowTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(phone="+237670020001", password="TestPass123!", country="CM")
        _grant_verification_badge_feature(self.user)
        self.staff = User.objects.create_user(phone="+237670020002", password="TestPass123!", country="CM")
        self.staff.is_staff = True
        self.staff.save(update_fields=["is_staff"])

    def test_user_can_read_empty_verification_status(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(reverse("verification:user-status"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["verified"])
        self.assertEqual(response.data["badges"], [])

    def test_user_can_start_case_with_private_evidence_metadata(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            reverse("verification:user-start"),
            {
                "provider": "dojah",
                "evidence_metadata": {
                    "documents": [
                        {
                            "type": "passport",
                            "private_media_id": "private-media-001",
                            "filename": "passport.pdf",
                        }
                    ]
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["case"]["provider"], "dojah")
        self.assertFalse(response.data["provider"]["configured"])
        self.assertEqual(VerificationCase.objects.filter(requested_by=self.user).count(), 1)

    def test_user_start_case_rejects_raw_document_payloads(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            reverse("verification:user-start"),
            {
                "evidence_metadata": {
                    "document_base64": "data:image/png;base64,abc123",
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(VerificationCase.objects.filter(requested_by=self.user).count(), 0)

    def test_staff_can_approve_user_case_and_issue_public_badges(self):
        self.client.force_authenticate(user=self.user)
        created = self.client.post(reverse("verification:user-start"), {"evidence_metadata": {}}, format="json")
        case_id = created.data["case"]["id"]

        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            reverse("verification:staff-user-review", kwargs={"case_id": case_id}),
            {"action": "approve", "notes": "Manual ID matched."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual({badge["code"] for badge in response.data["badges"]}, {VerificationBadgeCode.VERIFIED_USER, VerificationBadgeCode.ID_VERIFIED})
        self.assertTrue(
            VerificationBadge.objects.filter(
                subject__subject_type=VerificationSubjectType.USER,
                subject__subject_id=self.user.id,
                code=VerificationBadgeCode.VERIFIED_USER,
            ).exists()
        )
        self.assertTrue(verification_summary(VerificationSubjectType.USER, self.user.id)["verified"])

    def test_public_trust_summary_excludes_private_verification_payloads(self):
        subject = user_subject_for(self.user)
        case = VerificationCase.objects.create(
            subject=subject,
            requested_by=self.user,
            level="identity_verified",
            status=VerificationCaseStatus.APPROVED,
            provider="manual",
            evidence_metadata={"documents": [{"private_media_id": "private-secret-doc"}]},
            provider_payload={"secret": "provider-secret"},
            submitted_at=timezone.now(),
            reviewed_at=timezone.now(),
        )
        VerificationBadge.objects.create(
            subject=subject,
            case=case,
            code=VerificationBadgeCode.VERIFIED_USER,
            status=VerificationBadgeStatus.ACTIVE,
            public=True,
            expires_at=timezone.now() + timezone.timedelta(days=20),
        )

        summary = public_trust_summary(VerificationSubjectType.USER, self.user.id)
        payload = json.dumps(summary)

        self.assertTrue(summary["verified"])
        self.assertEqual(summary["trust_tier"], "verified")
        self.assertTrue(summary["expiry"]["expires_soon"])
        self.assertNotIn("private-secret-doc", payload)
        self.assertNotIn("provider-secret", payload)
        self.assertFalse(summary["privacy"]["raw_documents_exposed"])

    def test_trust_overview_endpoint_unifies_owned_subjects_and_staff_evidence(self):
        subject = user_subject_for(self.user)
        VerificationBadge.objects.create(
            subject=subject,
            code=VerificationBadgeCode.ID_VERIFIED,
            status=VerificationBadgeStatus.ACTIVE,
            public=True,
        )

        self.client.force_authenticate(user=self.user)
        response = self.client.get(reverse("verification:trust-overview"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["counts"]["subjects"], 1)
        self.assertEqual(response.data["counts"]["verified_subjects"], 1)
        self.assertFalse(response.data["privacy"]["raw_documents_exposed"])
        self.assertNotIn("staff_evidence", response.data)

        self.staff.is_staff = True
        self.staff.save(update_fields=["is_staff"])
        staff_subject = user_subject_for(self.staff)
        VerificationCase.objects.create(
            subject=staff_subject,
            requested_by=self.staff,
            level="identity_verified",
            status=VerificationCaseStatus.SUBMITTED,
            submitted_at=timezone.now(),
        )
        self.client.force_authenticate(user=self.staff)
        staff_response = self.client.get(reverse("verification:trust-overview"))

        self.assertEqual(staff_response.status_code, status.HTTP_200_OK)
        self.assertIn("staff_evidence", staff_response.data)
        self.assertIn("open_case_count", staff_response.data["staff_evidence"])

    def test_webhook_without_signature_is_rejected(self):
        response = self.client.post(reverse("verification:webhook", kwargs={"provider": "dojah"}), {"event": "test"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class StaffVerificationOperationsTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(phone="+237670020011", password="TestPass123!", country="CM")
        _grant_verification_badge_feature(self.user)
        self.staff = User.objects.create_user(phone="+237670020012", password="TestPass123!", country="CM")
        self.staff.is_staff = True
        self.staff.save(update_fields=["is_staff"])
        self.subject = user_subject_for(self.user)
        self.case = VerificationCase.objects.create(
            subject=self.subject,
            requested_by=self.user,
            level="identity_verified",
            status=VerificationCaseStatus.SUBMITTED,
            provider="manual",
            submitted_at=timezone.now(),
            evidence_metadata={"documents": [{"private_media_id": "private-doc-1"}], "private_references_only": True},
        )

    def test_staff_queue_is_staff_only(self):
        self.client.force_authenticate(self.user)
        denied = self.client.get(reverse("verification:staff-cases"))
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.staff)
        response = self.client.get(reverse("verification:staff-cases"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["evidence_summary"]["private_references_only"], True)

    def test_staff_can_issue_and_revoke_badge(self):
        self.client.force_authenticate(self.staff)
        issued = self.client.post(
            reverse("verification:staff-badge-issue"),
            {
                "case_id": str(self.case.id),
                "subject_type": VerificationSubjectType.USER,
                "code": VerificationBadgeCode.VERIFIED_USER,
                "reason": "Manual reviewer approved.",
            },
            format="json",
        )
        self.assertEqual(issued.status_code, status.HTTP_201_CREATED)
        self.assertEqual(issued.data["status"], VerificationBadgeStatus.ACTIVE)

        revoked = self.client.post(
            reverse("verification:staff-badge-revoke", kwargs={"badge_id": issued.data["id"]}),
            {"reason": "Evidence expired."},
            format="json",
        )
        self.assertEqual(revoked.status_code, status.HTTP_200_OK)
        self.assertEqual(revoked.data["status"], VerificationBadgeStatus.REVOKED)
        self.assertTrue(
            VerificationAuditEvent.objects.filter(action="staff.badge_revoked", subject=self.subject).exists()
        )

    def test_staff_can_read_audits_and_provider_callbacks(self):
        VerificationAuditEvent.objects.create(
            subject=self.subject,
            case=self.case,
            action="webhook.rejected",
            provider="dojah",
            ip_address="127.0.0.1",
            metadata={"reason": "test"},
        )
        self.client.force_authenticate(self.staff)

        audits = self.client.get(reverse("verification:staff-audit-events"))
        callbacks = self.client.get(reverse("verification:staff-provider-callbacks"), {"provider": "dojah"})
        signals = self.client.get(reverse("verification:staff-suspicious-signals"))

        self.assertEqual(audits.status_code, status.HTTP_200_OK)
        self.assertEqual(callbacks.status_code, status.HTTP_200_OK)
        self.assertEqual(signals.status_code, status.HTTP_200_OK)
        self.assertIn("many_cases_per_subject", signals.data)

    def test_staff_audit_serializer_redacts_provider_secrets_and_raw_documents(self):
        VerificationAuditEvent.objects.create(
            subject=self.subject,
            case=self.case,
            action="webhook.mapped",
            provider="dojah",
            metadata={
                "secret": "provider-secret",
                "token": "provider-token",
                "document_base64": "data:image/png;base64,secret",
                "nested": {"passport": "raw-passport", "safe": "ok"},
            },
        )
        self.client.force_authenticate(self.staff)

        response = self.client.get(reverse("verification:staff-audit-events"), {"action": "webhook.mapped"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = json.dumps(response.data, default=str)
        self.assertIn("[redacted]", payload)
        self.assertNotIn("provider-secret", payload)
        self.assertNotIn("provider-token", payload)
        self.assertNotIn("raw-passport", payload)
        self.assertEqual(response.data["results"][0]["metadata"]["nested"]["safe"], "ok")

    def test_verify_verification_launch_command_passes_local_guardrails(self):
        out = StringIO()

        call_command("verify_verification_launch", stdout=out)

        output = out.getvalue()
        self.assertIn("Verification launch guardrails ready: True", output)
        self.assertIn("VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED", output)
        self.assertIn("provider_payload_redaction", output)

    def test_expiry_reminders_and_expire_dry_run(self):
        badge = VerificationBadge.objects.create(
            subject=self.subject,
            case=self.case,
            code=VerificationBadgeCode.ID_VERIFIED,
            status=VerificationBadgeStatus.ACTIVE,
            public=True,
            expires_at=timezone.now() - timezone.timedelta(days=1),
        )
        self.client.force_authenticate(self.staff)

        dry_run = self.client.post(reverse("verification:staff-expiry-reminders"), {"dry_run": True}, format="json")
        expire = self.client.post(reverse("verification:staff-expiry-reminders"), {"dry_run": False}, format="json")

        self.assertEqual(dry_run.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(dry_run.data["matched"], 1)
        self.assertEqual(expire.status_code, status.HTTP_200_OK)
        badge.refresh_from_db()
        self.assertEqual(badge.status, VerificationBadgeStatus.EXPIRED)

    @override_settings(VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED=False)
    def test_provider_status_keeps_live_calls_disabled_by_default(self):
        provider = provider_public_status("dojah")
        self.assertFalse(provider["live_calls_enabled"])
        self.assertFalse(provider["live_call_made"])

    @override_settings(
        ENV="staging",
        VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED=True,
        VERIFICATION_PROVIDER_SANDBOX_ENABLED=True,
        VERIFICATION_PROVIDER_LIVE_ALLOWED_ENVS=["staging"],
        VERIFICATION_LIVE_PROVIDER_SUBJECTS=["user"],
        DOJAH_APP_ID="configured",
        DOJAH_SECRET_KEY="configured",
    )
    def test_staging_sandbox_user_start_records_redacted_provider_handoff(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            reverse("verification:user-start"),
            {
                "provider": "dojah",
                "evidence_metadata": {
                    "private_media_refs": ["private-media-1"],
                    "document_base64": "should-not-be-accepted",
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        response = self.client.post(
            reverse("verification:user-start"),
            {
                "provider": "dojah",
                "evidence_metadata": {
                    "private_media_refs": ["private-media-1"],
                    "private_references_only": True,
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        case = VerificationCase.objects.get(id=response.data["case"]["id"])
        self.assertEqual(case.status, VerificationCaseStatus.PROVIDER_PENDING)
        self.assertEqual(case.provider_status, "sandbox_pending")
        self.assertTrue(case.provider_payload["sandbox_handoff_ready"])
        self.assertFalse(case.provider_payload["live_call_made"])
        self.assertNotIn("private-media-1", json.dumps(case.provider_payload))

    @override_settings(VERIFICATION_WEBHOOK_SECRET="webhook-secret")
    def test_signed_provider_webhook_maps_approved_user_case_to_badges(self):
        self.case.provider = "dojah"
        self.case.provider_case_id = f"sandbox:dojah:{self.case.id}"
        self.case.status = VerificationCaseStatus.PROVIDER_PENDING
        self.case.save(update_fields=["provider", "provider_case_id", "status", "updated_at"])
        payload = {
            "provider_case_id": self.case.provider_case_id,
            "status": "approved",
            "document": "redact-this",
            "token": "redact-this-too",
        }
        body = json.dumps(payload).encode("utf-8")
        signature = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()

        response = self.client.post(
            reverse("verification:webhook", kwargs={"provider": "dojah"}),
            data=body,
            content_type="application/json",
            HTTP_X_VERIFICATION_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertTrue(response.data["matched"])
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, VerificationCaseStatus.APPROVED)
        self.assertTrue(
            VerificationBadge.objects.filter(subject=self.subject, code=VerificationBadgeCode.VERIFIED_USER).exists()
        )
        audit = VerificationAuditEvent.objects.filter(action="webhook.mapped", case=self.case).latest("created_at")
        serialized = json.dumps(audit.metadata)
        self.assertIn("[redacted]", serialized)
        self.assertNotIn("redact-this", serialized)

    @override_settings(VERIFICATION_WEBHOOK_SECRET="webhook-secret")
    def test_signed_provider_webhook_replay_status_fixtures(self):
        statuses = (
            ("rejected", VerificationCaseStatus.REJECTED),
            ("needs_more_info", VerificationCaseStatus.NEEDS_MORE_INFO),
            ("provider_pending", VerificationCaseStatus.PROVIDER_PENDING),
        )
        for provider_status, expected_case_status in statuses:
            case = VerificationCase.objects.create(
                subject=self.subject,
                requested_by=self.user,
                level="identity_verified",
                status=VerificationCaseStatus.PROVIDER_PENDING,
                provider="dojah",
                provider_case_id=f"sandbox:dojah:{provider_status}:{self.user.id}",
                evidence_metadata={"private_references_only": True},
            )
            payload = {"provider_case_id": str(case.provider_case_id), "status": provider_status}
            body = json.dumps(payload).encode("utf-8")
            signature = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
            response = self.client.post(
                reverse("verification:webhook", kwargs={"provider": "dojah"}),
                data=body,
                content_type="application/json",
                HTTP_X_VERIFICATION_SIGNATURE=signature,
            )
            self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
            self.assertTrue(response.data["matched"])
            case.refresh_from_db()
            self.assertEqual(case.status, expected_case_status)

        payload = {"provider_case_id": "sandbox:dojah:00000000-0000-4000-8000-000000000999", "status": "approved"}
        body = json.dumps(payload).encode("utf-8")
        signature = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
        response = self.client.post(
            reverse("verification:webhook", kwargs={"provider": "dojah"}),
            data=body,
            content_type="application/json",
            HTTP_X_VERIFICATION_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertFalse(response.data["matched"])
        self.assertTrue(VerificationAuditEvent.objects.filter(action="webhook.unmatched").exists())

    @override_settings(
        ENV="staging",
        VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED=True,
        VERIFICATION_PROVIDER_SANDBOX_ENABLED=True,
        VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED=False,
        VERIFICATION_PROVIDER_LIVE_ALLOWED_ENVS=["staging"],
        VERIFICATION_LIVE_PROVIDER_SUBJECTS=["user"],
        DOJAH_APP_ID="provider-secret",
        DOJAH_SECRET_KEY="provider-secret",
        SUMSUB_APP_TOKEN="provider-secret",
        SUMSUB_SECRET_KEY="provider-secret",
        SMILE_ID_PARTNER_ID="provider-secret",
        SMILE_ID_API_KEY="provider-secret",
    )
    def test_provider_specific_sandbox_requests_are_redacted(self):
        for provider in ("dojah", "sumsub", "smile_id"):
            case = start_user_verification_case(
                user=self.user,
                level="identity_verified",
                provider=provider,
                evidence_metadata={"private_media_refs": ["private-media-1"], "private_references_only": True},
            )
            adapter = get_provider_adapter(provider)
            self.assertTrue(adapter.live_calls_enabled(VerificationSubjectType.USER))
            serialized = json.dumps(case.provider_payload)
            self.assertIn("sandbox_network_disabled", serialized)
            self.assertNotIn("provider-secret", serialized)
            self.assertNotIn("private-media-1", serialized)
            self.assertNotIn("secret", serialized.lower())

    @override_settings(
        ENV="staging",
        VERIFICATION_LIVE_PROVIDER_CALLS_ENABLED=True,
        VERIFICATION_PROVIDER_SANDBOX_ENABLED=True,
        VERIFICATION_PROVIDER_SANDBOX_NETWORK_ENABLED=False,
        VERIFICATION_PROVIDER_LIVE_ALLOWED_ENVS=["staging"],
        VERIFICATION_LIVE_PROVIDER_SUBJECTS=["partner", "health_institution", "education_institution"],
        DOJAH_APP_ID="provider-secret",
        DOJAH_SECRET_KEY="provider-secret",
    )
    def test_institution_sandbox_handoff_is_redacted_and_provider_pending(self):
        class Target:
            def __init__(self, name):
                self.id = uuid.uuid4()
                self.name = name
                self.owner = self.user
                self.owner_user = self.user
                self.institution_type = "test"

        Target.user = self.user
        partner = Target("Partner Org")
        health = Target("Health Org")
        education = Target("Education Org")
        cases = [
            start_partner_verification_case(partner=partner, actor=self.user, provider="dojah", evidence_metadata={"company_registration": [{"private_media_id": "private-partner"}]}),
            start_health_institution_verification_case(institution=health, actor=self.user, provider="dojah", evidence_metadata={"medical_license": [{"private_media_id": "private-health"}]}),
            start_education_institution_verification_case(institution=education, actor=self.user, provider="dojah", evidence_metadata={"accreditation": [{"private_media_id": "private-education"}]}),
        ]
        for case in cases:
            self.assertIsNotNone(case)
            case.refresh_from_db()
            self.assertEqual(case.status, VerificationCaseStatus.PROVIDER_PENDING)
            self.assertEqual(case.provider_status, "sandbox_pending")
            serialized = json.dumps(case.provider_payload)
            self.assertIn("sandbox_network_disabled", serialized)
            self.assertNotIn("provider-secret", serialized)
            self.assertNotIn("private-", serialized)

    @override_settings(VERIFICATION_WEBHOOK_SECRET="webhook-secret")
    def test_provider_webhook_approval_maps_subject_specific_badges(self):
        expected = (
            (VerificationSubjectType.PARTNER, "partner_verified", VerificationBadgeCode.VERIFIED_PARTNER),
            (VerificationSubjectType.HEALTH_INSTITUTION, "licensed_health", VerificationBadgeCode.VERIFIED_HEALTH_INSTITUTION),
            (VerificationSubjectType.EDUCATION_INSTITUTION, "accredited_education", VerificationBadgeCode.VERIFIED_EDUCATION_INSTITUTION),
            (VerificationSubjectType.SHOP, "shop_kyb_verified", VerificationBadgeCode.VERIFIED_SHOP),
        )
        for subject_type, level, badge_code in expected:
            subject = get_or_create_subject(
                subject_type=subject_type,
                subject_id=uuid.uuid4(),
                owner=self.user,
                display_name=f"{subject_type} target",
            )
            case = VerificationCase.objects.create(
                subject=subject,
                requested_by=self.user,
                level=level,
                status=VerificationCaseStatus.PROVIDER_PENDING,
                provider="dojah",
                provider_case_id=f"sandbox:dojah:{subject_type}:{subject.subject_id}",
                evidence_metadata={"private_references_only": True},
            )
            payload = {"provider_case_id": case.provider_case_id, "status": "approved"}
            body = json.dumps(payload).encode("utf-8")
            signature = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
            response = self.client.post(
                reverse("verification:webhook", kwargs={"provider": "dojah"}),
                data=body,
                content_type="application/json",
                HTTP_X_VERIFICATION_SIGNATURE=signature,
            )
            self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
            self.assertTrue(response.data["matched"])
            case.refresh_from_db()
            self.assertEqual(case.status, VerificationCaseStatus.APPROVED)
            self.assertTrue(VerificationBadge.objects.filter(subject=subject, code=badge_code).exists())

    def test_expiry_notification_scheduler_dry_run_uses_private_metadata_only(self):
        badge = VerificationBadge.objects.create(
            subject=self.subject,
            case=self.case,
            code=VerificationBadgeCode.ID_VERIFIED,
            status=VerificationBadgeStatus.ACTIVE,
            public=True,
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )

        result = schedule_verification_expiry_notifications(days_list=[30, 14, 7, 1], dry_run=True)

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["matched"], 1)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["id"], str(badge.id))
        self.assertNotIn("evidence_metadata", candidate)
