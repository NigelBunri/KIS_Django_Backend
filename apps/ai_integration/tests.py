from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate

from .models import AIJob, AIPipeline, QnASession, TranslationRequest
from .views import AIJobViewSet, AIPipelineViewSet, QnASessionViewSet


class BasicModelTest(TestCase):
    def test_create_translation_job_and_request(self):
        job = AIJob.objects.create(job_type='TRANSLATION', input_ref_type='TEXT')
        tr = TranslationRequest.objects.create(job=job, source_lang='en', target_lang='fr', text_chars=10)
        self.assertEqual(tr.job, job)


class AIIntegrationAccessBoundaryTests(TestCase):
    def setUp(self):
        auth_user = get_user_model()
        self.owner = auth_user.objects.create_user(phone="+237670004201", password="TestPass123!", country="CM")
        self.other = auth_user.objects.create_user(phone="+237670004202", password="TestPass123!", country="CM")
        self.staff = auth_user.objects.create_user(
            phone="+237670004203",
            password="TestPass123!",
            country="CM",
            is_staff=True,
        )
        self.owner_job = AIJob.objects.create(job_type="CUSTOM", input_ref_type="TEXT", triggered_by=str(self.owner.id))
        self.other_job = AIJob.objects.create(job_type="CUSTOM", input_ref_type="TEXT", triggered_by=str(self.other.id))
        self.owner_session = QnASession.objects.create(user_id=self.owner.id)
        self.other_session = QnASession.objects.create(user_id=self.other.id)
        self.pipeline = AIPipeline.objects.create(name="Internal Pipeline", status="DRAFT")
        self.factory = APIRequestFactory()

    def _list_view(self, viewset, user):
        request = self.factory.get("/")
        force_authenticate(request, user=user)
        response = viewset.as_view({"get": "list"})(request)
        response.render()
        return response

    def _rows(self, response):
        payload = response.data
        return payload.get("results", payload)

    def test_ai_jobs_are_limited_to_triggering_user(self):
        response = self._list_view(AIJobViewSet, self.owner)
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in self._rows(response)}
        self.assertIn(str(self.owner_job.id), ids)
        self.assertNotIn(str(self.other_job.id), ids)

    def test_qna_sessions_are_limited_to_owner(self):
        response = self._list_view(QnASessionViewSet, self.owner)
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in self._rows(response)}
        self.assertIn(str(self.owner_session.id), ids)
        self.assertNotIn(str(self.other_session.id), ids)

    def test_ai_pipelines_are_staff_only(self):
        denied = self._list_view(AIPipelineViewSet, self.owner)
        self.assertEqual(denied.status_code, 403)

        allowed = self._list_view(AIPipelineViewSet, self.staff)
        self.assertEqual(allowed.status_code, 200)
        ids = {row["id"] for row in self._rows(allowed)}
        self.assertIn(str(self.pipeline.id), ids)
