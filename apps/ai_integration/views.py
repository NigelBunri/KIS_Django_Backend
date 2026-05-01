# ai_integration/views.py
import json

from apps.accounts import models as accounts_models
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.http import StreamingHttpResponse

from .services import GroqAIService

from .models import AIJob, TranslationRequest, QnASession, AIModel, AIJobFeedback, AIPipeline, AISchedule
from .serializers import (AIJobSerializer, TranslationRequestSerializer, QnASessionSerializer,
                          AIModelSerializer, AIJobFeedbackSerializer, AIPipelineSerializer, AIScheduleSerializer)
from .tasks import enqueue_ai_job
from apps.accounts.tiers import consume_quota


class StaffOrAuthenticatedReadOnly(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True
        return request.user.is_staff or request.user.is_superuser


class TriggeredByUserQuerysetMixin:
    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return qs
        identifiers = {str(user.id)}
        for value in (getattr(user, "username", None), getattr(user, "phone", None), getattr(user, "email", None)):
            if value:
                identifiers.add(str(value))
        return qs.filter(triggered_by__in=identifiers)

    def perform_create(self, serializer):
        serializer.save(triggered_by=str(self.request.user.id))

# --- OpenAPI / Swagger compatibility layer (supports drf-spectacular and drf-yasg) ---
try:
    # drf-spectacular
    from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse, OpenApiParameter
    SPECTACULAR = True
except Exception:
    SPECTACULAR = False

try:
    # drf-yasg
    from drf_yasg.utils import swagger_auto_schema
    from drf_yasg import openapi
    YASG = True
except Exception:
    YASG = False


def doc_decorator(summary=None, description=None, request=None, responses=None, parameters=None):
    """
    Returns a decorator compatible with drf-spectacular or drf-yasg (or a no-op if neither is available).
    - summary: short operation summary
    - description: longer description
    - request: serializer class for request body (or None)
    - responses: dict or serializer for responses (or None)
    - parameters: list of OpenApiParameter / openapi.Parameter if needed
    """
    if SPECTACULAR:
        # drf-spectacular expects `request=` and `responses=`
        return extend_schema(summary=summary, description=description, request=request, responses=responses, parameters=parameters)
    if YASG:
        # map to drf-yasg naming
        def _map_params(params):
            if not params:
                return None
            # if user passed drf-spectacular OpenApiParameter objects, we can't convert automatically;
            # drf-yasg expects openapi.Parameter objects. Accept that `parameters` may be None for yasg fallback.
            return params

        return swagger_auto_schema(operation_summary=summary, operation_description=description,
                                  request_body=request, responses=responses, manual_parameters=_map_params(parameters))
    # no-op decorator
    def _noop(func):
        return func
    return _noop


def class_doc_decorator(tag_name: str):
    """
    Return a class decorator to attach tags or class-level metadata if spectacular is available.
    For drf-spectacular we use extend_schema_view to tag common methods.
    For drf-yasg we don't add a class-level decorator (manual tagging is often done in schema view).
    """
    if SPECTACULAR:
        # tag common viewset actions
        return extend_schema_view(
            list=extend_schema(tags=[tag_name]),
            retrieve=extend_schema(tags=[tag_name]),
            create=extend_schema(tags=[tag_name]),
            update=extend_schema(tags=[tag_name]),
            partial_update=extend_schema(tags=[tag_name]),
            destroy=extend_schema(tags=[tag_name])
        )
    # no-op for drf-yasg and plain
    def _noop(cls):
        return cls
    return _noop


# --- ViewSets with OpenAPI annotations ---
@class_doc_decorator('AI Models')
class AIModelViewSet(viewsets.ModelViewSet):
    queryset = AIModel.objects.all()
    serializer_class = AIModelSerializer
    permission_classes = [StaffOrAuthenticatedReadOnly]


@class_doc_decorator('AI Jobs')
class AIJobViewSet(TriggeredByUserQuerysetMixin, viewsets.ModelViewSet):
    queryset = AIJob.objects.all().order_by('-created_at')
    serializer_class = AIJobSerializer
    permission_classes = [IsAuthenticated]

    @doc_decorator(
        summary="Run AI job",
        description="Enqueue and run the AI job (allowed only when job status is PENDING or RETRY).",
        request=None,
        responses={200: OpenApiResponse(description="Job enqueued") if SPECTACULAR else "Job enqueued",
                   400: OpenApiResponse(description="Job not runnable") if SPECTACULAR else "Job not runnable"},
    )
    @action(detail=True, methods=['post'])
    def run(self, request, pk=None):
        job = self.get_object()
        if job.status not in ('PENDING', 'RETRY'):
            return Response({'detail': 'Job not runnable'}, status=status.HTTP_400_BAD_REQUEST)
        enqueue_ai_job.delay(str(job.id))
        job.status = 'RETRY'
        job.save()
        return Response({'status': 'enqueued'})


@class_doc_decorator('Translations')
class TranslationRequestViewSet(viewsets.ModelViewSet):
    queryset = TranslationRequest.objects.all().order_by('-created_at')
    serializer_class = TranslationRequestSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset().select_related("job")
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return qs
        identifiers = {str(user.id)}
        for value in (getattr(user, "username", None), getattr(user, "phone", None), getattr(user, "email", None)):
            if value:
                identifiers.add(str(value))
        return qs.filter(job__triggered_by__in=identifiers)

    @doc_decorator(
        summary="Create translation request",
        description="Creates an AIJob of type TRANSLATION and enqueues it. Free-tier stub processor will run.",
        request=TranslationRequestSerializer,
        responses={201: TranslationRequestSerializer, 400: OpenApiResponse(description="Bad request") if SPECTACULAR else "Bad request"},
    )
    def create(self, request, *args, **kwargs):
        if request.user and request.user.is_authenticated:
            if not consume_quota(request.user, "ai_queries_per_day", 1):
                return Response({"detail": "AI daily quota exceeded"}, status=429)
        # create an AIJob and TranslationRequest atomically
        data = request.data
        job_data = {
            'job_type': 'TRANSLATION',
            'input_ref_type': data.get('input_ref_type', 'TEXT'),
            'input_ref_id': data.get('input_ref_id'),
            'metadata': data.get('metadata', {}),
            'triggered_by': str(request.user.id) if request.user.is_authenticated else 'ANONYMOUS'
        }
        job = AIJob.objects.create(**job_data)
        tr = TranslationRequest.objects.create(
            job=job,
            source_lang=data['source_lang'],
            target_lang=data['target_lang'],
            text_chars=len(data.get('text', '')),
            result_text=''
        )
        # enqueue
        enqueue_ai_job.delay(str(job.id))
        serializer = self.get_serializer(tr)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@class_doc_decorator('QnA Sessions')
class QnASessionViewSet(viewsets.ModelViewSet):
    queryset = QnASession.objects.all().order_by('-last_interaction_at')
    serializer_class = QnASessionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return qs
        return qs.filter(user_id=user.id)

    def perform_create(self, serializer):
        serializer.save(user_id=self.request.user.id)

    @doc_decorator(
        summary="Send a message to QnA session",
        description="Post a message to an active QnA session. The message will be processed asynchronously by creating an AIJob of type CUSTOM (QNA).",
        request=None,
        responses={200: OpenApiResponse(description="Queued") if SPECTACULAR else "Queued"}
    )
    @action(detail=True, methods=['post'])
    def message(self, request, pk=None):
        if request.user and request.user.is_authenticated:
            if not consume_quota(request.user, "ai_queries_per_day", 1):
                return Response({"detail": "AI daily quota exceeded"}, status=429)
        session = self.get_object()
        user_message = request.data.get('message')
        # For free tier: forward to a local or community model microservice stub
        # Here we create an AIJob of type CUSTOM to process the message asynchronously
        job = AIJob.objects.create(job_type='CUSTOM', input_ref_type='QNA', input_ref_id=session.id,
                                   metadata={'message': user_message}, triggered_by=str(request.user.id) if request.user.is_authenticated else 'ANONYMOUS')
        enqueue_ai_job.delay(str(job.id))
        # use timezone.now() for a proper timestamp
        session.last_interaction_at = timezone.now()
        session.save()
        return Response({'status': 'queued', 'job_id': job.id})


@class_doc_decorator('AI Feedback')
class AIJobFeedbackViewSet(viewsets.ModelViewSet):
    queryset = AIJobFeedback.objects.all().order_by('-created_at')
    serializer_class = AIJobFeedbackSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset().select_related("job")
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return qs
        return qs.filter(Q(user_id=user.id) | Q(job__triggered_by=str(user.id)))

    def perform_create(self, serializer):
        serializer.save(user_id=self.request.user.id)


@class_doc_decorator('Pipelines')
class AIPipelineViewSet(viewsets.ModelViewSet):
    queryset = AIPipeline.objects.all().order_by('-created_at')
    serializer_class = AIPipelineSerializer
    permission_classes = [IsAdminUser]

    @doc_decorator(
        summary="Trigger pipeline",
        description="Enqueue execution of a pipeline. Works asynchronously.",
        request=None,
        responses={200: OpenApiResponse(description="Pipeline enqueued") if SPECTACULAR else "Pipeline enqueued"}
    )
    @action(detail=True, methods=['post'])
    def trigger(self, request, pk=None):
        pipeline = self.get_object()
        # pipeline execution scheduled
        from .services import execute_pipeline
        execute_pipeline.delay(str(pipeline.id), triggered_by=str(request.user.id) if request.user.is_authenticated else 'ANONYMOUS')
        return Response({'status': 'enqueued'})


@class_doc_decorator('Schedules')
class AIScheduleViewSet(viewsets.ModelViewSet):
    queryset = AISchedule.objects.all().order_by('-created_at')
    serializer_class = AIScheduleSerializer
    permission_classes = [IsAdminUser]
    


class GroqAIStreamView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        prompt = request.data.get("prompt", "")
        mode = request.data.get("mode", "triage")
        model = request.data.get("model")
        tenant_id = request.data.get("tenant_id")

        job = AIJob.objects.create(
            job_type="CUSTOM",
            input_ref_type="GROQ_STREAM",
            metadata={"prompt": prompt, "mode": mode, "tenant_id": tenant_id},
            triggered_by=request.user.username if request.user and request.user.is_authenticated else "ANONYMOUS",
        )
        job.status = "RUNNING"
        job.save(update_fields=["status"])

        service = GroqAIService()

        def generator():
            try:
                for chunk in service.stream_prompt(prompt=prompt, mode=mode, model=model, tenant_id=tenant_id, job_id=str(job.id)):
                    payload = {"type": chunk.get("type", "chunk"), "data": chunk}
                    yield f"data: {json.dumps(payload)}\\n\\n"
            except Exception as exc:
                job.status = "FAILED"
                job.error = str(exc)
                job.save(update_fields=["status", "error"])
                yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\\n\\n"
            else:
                job.status = "COMPLETED"
                job.completed_at = timezone.now()
                job.save(update_fields=["status", "completed_at"])
                yield f"data: {json.dumps({'type': 'done'})}\\n\\n"

        response = StreamingHttpResponse(generator(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Job-Id"] = str(job.id)
        return response


class GroqAITaskView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        prompt = request.data.get("prompt", "")
        if not prompt:
            raise ValidationError({"prompt": "Required."})
        mode = request.data.get("mode", "clinical")
        function_call = request.data.get("function_call")
        tenant_id = request.data.get("tenant_id")

        job = AIJob.objects.create(
            job_type="CUSTOM",
            input_ref_type="GROQ_TASK",
            metadata={"prompt": prompt, "mode": mode, "function_call": function_call},
            triggered_by=request.user.username if request.user and request.user.is_authenticated else "ANONYMOUS",
        )
        job.status = "RUNNING"
        job.save(update_fields=["status"])

        service = GroqAIService()
        try:
            result = service.route_task(prompt=prompt, mode=mode, tenant_id=tenant_id, function_call=function_call)
        except ValueError as exc:
            job.status = "FAILED"
            job.error = str(exc)
            job.save(update_fields=["status", "error"])
            raise ValidationError({"detail": str(exc)})

        service.log_decision(str(job.id), result)
        job.metadata["result"] = result
        job.status = "COMPLETED"
        job.result_ref = result.get("response") or json.dumps(result.get("function_call", {}))
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "result_ref", "completed_at", "metadata"])

        return Response({"job_id": str(job.id), "result": result}, status=status.HTTP_200_OK)
