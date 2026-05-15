# media/views.py
import os
import uuid
from urllib.parse import quote

from django.conf import settings
from django.core import signing
from django.core.files.storage import default_storage
from django.http import FileResponse
from django.utils.text import get_valid_filename
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from apps.accounts.jwt_auth import DeviceBoundJWTAuthentication

from .models import MediaAsset, MediaVariant, ProcessingJob, MediaMetrics, MediaSafetyScan
from django.db import models
from .serializers import (
    MediaAssetSerializer, MediaVariantSerializer, ProcessingJobSerializer, MediaMetricsSerializer, MediaSafetyScanSerializer
)
from .permissions import IsOwnerOrReadOnly
from .safety import (
    explicit_scan_required,
    hash_upload,
    normalize_upload_context,
    scan_upload_for_explicit_content,
    user_safe_upload_response,
    validate_upload_file_safety,
)

MEDIA_SIGNED_URL_TTL_SECONDS = int(os.environ.get("MEDIA_SIGNED_URL_TTL_SECONDS", "300"))
PRIVATE_VISIBILITY_VALUES = {"private", "restricted", "owner", "authenticated", "tenant"}
PUBLIC_VISIBILITY_VALUES = {"public", "published", "open"}


def _payload_visibility(payload) -> str:
    if not isinstance(payload, dict):
        return ""
    value = payload.get("visibility") or payload.get("access") or payload.get("privacy")
    return str(value or "").strip().lower()


def _asset_is_private(asset: MediaAsset) -> bool:
    payloads = [asset.storage or {}, asset.metadata or {}, asset.security or {}]
    for payload in payloads:
        visibility = _payload_visibility(payload)
        if visibility in PRIVATE_VISIBILITY_VALUES:
            return True
        if payload.get("private") is True:
            return True
        if payload.get("public") is False:
            return True

    policy = getattr(asset, "access_policy", None)
    rules = getattr(policy, "rules", None) if policy else None
    if isinstance(rules, dict):
        visibility = _payload_visibility(rules)
        if visibility in PRIVATE_VISIBILITY_VALUES:
            return True
        if rules.get("private") is True or rules.get("public") is False:
            return True
    return False


def _public_media_queryset(qs):
    """Legacy ready media remains public unless an explicit private marker exists."""
    public_ids = [
        asset.id
        for asset in qs.filter(status="ready")
        if not _asset_is_private(asset)
    ]
    return qs.filter(id__in=public_ids)


def _user_can_access_asset(user, asset: MediaAsset) -> bool:
    if not _asset_is_private(asset):
        return asset.status == "ready" or bool(user and user.is_authenticated and user == asset.owner)
    if not user or not user.is_authenticated:
        return False
    return bool(user.is_staff or asset.owner_id == user.id)


def _sign_media_asset(asset: MediaAsset) -> str:
    signer = signing.TimestampSigner(salt="kis-media-download")
    return signer.sign(str(asset.id))


def _token_allows_asset(token: str | None, asset: MediaAsset) -> bool:
    if not token:
        return False
    signer = signing.TimestampSigner(salt="kis-media-download")
    try:
        value = signer.unsign(token, max_age=MEDIA_SIGNED_URL_TTL_SECONDS)
    except signing.BadSignature:
        return False
    except signing.SignatureExpired:
        return False
    return str(value) == str(asset.id)

# --- Swagger / OpenAPI compatibility shim (drf-yasg first, then drf-spectacular) ---
try:
    # drf-yasg
    from drf_yasg.utils import swagger_auto_schema
    from drf_yasg import openapi

    def schema_decorator(**kwargs):
        return swagger_auto_schema(**kwargs)

    def _PARAM(name, typ, required=False, desc=""):
        # drf-yasg Parameter helper for query params
        location = openapi.IN_QUERY if typ == "query" else openapi.IN_BODY
        return openapi.Parameter(name=name, in_=location, description=desc, type=openapi.TYPE_STRING, required=required)

    USING_SCHEMA_LIB = "drf_yasg"

except Exception:
    try:
        # drf-spectacular
        from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse

        def schema_decorator(**kwargs):
            """
            Map a limited subset of kwargs to drf-spectacular's extend_schema.
            Supported keys mapped: operation_description, request_body, responses, manual_parameters
            """
            operation_description = kwargs.get("operation_description")
            request_body = kwargs.get("request_body")
            responses = kwargs.get("responses")
            manual_parameters = kwargs.get("manual_parameters") or kwargs.get("parameters") or []

            spect_params = []
            for p in manual_parameters:
                try:
                    name = getattr(p, "name", None) or getattr(p, "param_name", None)
                    location = getattr(p, "in_", None)
                    required = getattr(p, "required", False)
                    desc = getattr(p, "description", "") or ""
                    # Map common locations; default to QUERY
                    loc = OpenApiParameter.QUERY
                    spect_params.append(OpenApiParameter(name=name, description=desc, required=required, type=str, location=loc))
                except Exception:
                    continue

            # Map responses: drf-spectacular expects dict mapping status->serializer/response
            return extend_schema(
                description=operation_description,
                request=request_body,
                responses=responses,
                parameters=spect_params if spect_params else None
            )

        def _PARAM(name, typ, required=False, desc=""):
            # simple factory for spectaculr mapping (location defaulted to query)
            loc = OpenApiParameter.QUERY
            return OpenApiParameter(name=name, description=desc, required=required, type=str, location=loc)

        USING_SCHEMA_LIB = "drf_spectacular"

    except Exception:
        # no schema lib installed, no-op decorator
        def schema_decorator(**kwargs):
            def _noop(func):
                return func
            return _noop

        def _PARAM(name, typ, required=False, desc=""):
            return None

        USING_SCHEMA_LIB = None
# ------------------------------------------------------------------------------

class MediaAssetViewSet(viewsets.ModelViewSet):
    queryset = MediaAsset.objects.select_related("owner").all()
    serializer_class = MediaAssetSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOwnerOrReadOnly]

    def get_queryset(self):
        qs = MediaAsset.objects.select_related("owner").filter(is_deleted=False)
        user = self.request.user
        if user.is_authenticated:
            if user.is_staff:
                return qs
            public_qs = _public_media_queryset(qs)
            return (qs.filter(owner=user) | public_qs).distinct()
        return _public_media_queryset(qs)

    def perform_create(self, serializer):
        from apps.accounts.tiers import get_user_tier_features

        user = self.request.user
        features = get_user_tier_features(user)
        limit_mb = features.get("media_storage_mb")
        if isinstance(limit_mb, int):
            used_bytes = (
                MediaAsset.objects.filter(owner=user, is_deleted=False)
                .aggregate(total=models.Sum("bytes"))
                .get("total")
                or 0
            )
            incoming_bytes = int(self.request.data.get("bytes") or 0)
            limit_bytes = int(limit_mb) * 1024 * 1024
            if used_bytes + incoming_bytes > limit_bytes:
                raise ValidationError({"detail": "Storage limit reached for your plan."})
        serializer.save(owner=user)

    @schema_decorator(
        operation_description=(
            "Mark an upload as complete. Client should supply 'canonical_url' (optional). "
            "This endpoint marks the asset ready and schedules common processing jobs (analyze, phash)."
        ),
        manual_parameters=[
            _PARAM("canonical_url", "query", required=False, desc="Canonical public URL for the uploaded asset")
        ],
        responses={
            200: (openapi.Schema(type=openapi.TYPE_OBJECT, properties={
                "ok": openapi.Schema(type=openapi.TYPE_BOOLEAN, description="True when scheduling succeeded")
            }) if USING_SCHEMA_LIB == "drf_yasg" else {"ok": "boolean"})
        }
    )
    @action(detail=True, methods=["post"], permission_classes=[permissions.IsAuthenticated])
    def upload_complete(self, request, pk=None):
        """
        Called when client finishes upload; can mark asset ready and schedule processing jobs.
        """
        asset = self.get_object()
        url = request.data.get("canonical_url")
        asset.mark_ready(url=url)
        # schedule processing jobs - simple stub: create jobs for analyze & phash
        ProcessingJob.objects.create(asset=asset, pipeline="analyze", priority=50)
        ProcessingJob.objects.create(asset=asset, pipeline="phash", priority=40)
        return Response({"ok": True})

    @schema_decorator(
        operation_description="Record a media view. Optionally pass 'minutes' viewed (int).",
        manual_parameters=[
            _PARAM("minutes", "query", required=False, desc="Minutes streamed/viewed (integer)")
        ],
        responses={
            200: (openapi.Schema(type=openapi.TYPE_OBJECT, properties={
                "views": openapi.Schema(type=openapi.TYPE_INTEGER),
                "stream_minutes": openapi.Schema(type=openapi.TYPE_INTEGER),
                "downloads": openapi.Schema(type=openapi.TYPE_INTEGER),
                "carbon_grams": openapi.Schema(type=openapi.TYPE_NUMBER),
                "cost_cents": openapi.Schema(type=openapi.TYPE_INTEGER),
            }) if USING_SCHEMA_LIB == "drf_yasg" else MediaMetricsSerializer)
        }
    )
    @action(detail=True, methods=["post"], permission_classes=[permissions.AllowAny])
    def record_view(self, request, pk=None):
        """
        Record a view for the media asset (increments counters). Returns current metrics.
        """
        asset = self.get_object()
        minutes = request.data.get("minutes", 0)
        metrics, _ = MediaMetrics.objects.get_or_create(asset=asset)
        metrics.add_view(minutes=minutes)
        return Response(MediaMetricsSerializer(metrics).data)

    @action(detail=True, methods=["post"], permission_classes=[permissions.IsAuthenticated])
    def sign(self, request, pk=None):
        asset = get_object_or_404(MediaAsset.objects.filter(is_deleted=False), pk=pk)
        if not _user_can_access_asset(request.user, asset):
            raise PermissionDenied("You do not have access to this media asset.")

        token = _sign_media_asset(asset)
        path = f"/api/v1/assets/{asset.id}/download/?token={quote(token)}"
        return Response(
            {
                "signed_url": request.build_absolute_uri(path),
                "expires_in": MEDIA_SIGNED_URL_TTL_SECONDS,
                "private": _asset_is_private(asset),
            }
        )

    @action(detail=True, methods=["get"], permission_classes=[permissions.AllowAny])
    def download(self, request, pk=None):
        asset = get_object_or_404(MediaAsset.objects.filter(is_deleted=False), pk=pk)
        if not _user_can_access_asset(request.user, asset):
            token = request.query_params.get("token")
            if not _token_allows_asset(token, asset):
                raise PermissionDenied("A valid signed media URL is required.")

        if not asset.bucket_key:
            raise NotFound("Media file is not available.")
        try:
            handle = default_storage.open(asset.bucket_key, "rb")
        except Exception:
            raise NotFound("Media file is not available.")

        response = FileResponse(handle, content_type=asset.mime_type or "application/octet-stream")
        response["Cache-Control"] = "private, max-age=0, no-store"
        return response

class ProcessingJobViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ProcessingJob.objects.select_related("asset", "asset__owner").all()
    serializer_class = ProcessingJobSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = ProcessingJob.objects.select_related("asset", "asset__owner").filter(is_deleted=False)
        user = self.request.user
        if user.is_staff:
            return qs
        return qs.filter(asset__owner=user)


class MediaSafetyScanViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MediaSafetyScanSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = MediaSafetyScan.objects.select_related("owner", "asset").filter(is_deleted=False).order_by("-created_at")
        user = self.request.user
        if user.is_staff:
            return qs
        return qs.filter(owner=user)


def _guess_attachment_kind(mime_type: str | None) -> str:
    if not mime_type:
        return "other"
    normalized = mime_type.lower()
    if normalized.startswith("image/"):
        return "image"
    if normalized.startswith("video/"):
        return "video"
    if normalized.startswith("audio/"):
        return "audio"
    if normalized.startswith("text/") or normalized.startswith("application/"):
        return "document"
    return "other"


class UploadFileView(APIView):
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [DeviceBoundJWTAuthentication]
    throttle_scope = "upload"

    def initial(self, request, *args, **kwargs):
        device_id = request.GET.get("device_id")
        if device_id and not request.headers.get("X-Device-Id"):
            request.META["HTTP_X_DEVICE_ID"] = device_id
        return super().initial(request, *args, **kwargs)

    def post(self, request):
        upload = request.FILES.get("file")
        if not upload:
            return Response({"detail": "A file is required."}, status=status.HTTP_400_BAD_REQUEST)
        context = normalize_upload_context(
            request.data.get("context") or request.data.get("surface") or request.data.get("upload_context")
        )
        validate_upload_file_safety(upload, context=context)

        identifier = uuid.uuid4().hex
        filename = get_valid_filename(upload.name or "upload") or "upload"
        checksum = hash_upload(upload)
        mime_type = upload.content_type or ""
        decision = scan_upload_for_explicit_content(filename=filename, mime_type=mime_type, context=context)
        relative_path = f"uploads/{identifier}/{filename}"
        saved_path = default_storage.save(relative_path, upload)
        sanitized_path = saved_path.replace("\\", "/")
        media_root_url = settings.MEDIA_URL.rstrip("/")
        public_url = request.build_absolute_uri(f"{media_root_url}/{sanitized_path.lstrip('/')}")
        visibility = str(request.data.get("visibility") or "private").strip().lower()
        is_public_upload = visibility in PUBLIC_VISIBILITY_VALUES
        scan_status = decision.status

        safety_scan = MediaSafetyScan.objects.create(
            owner=request.user if request.user.is_authenticated else None,
            upload_id=identifier,
            context=context,
            original_name=filename,
            mime_type=mime_type,
            bytes=upload.size or 0,
            checksum=checksum,
            provider=decision.provider,
            status=decision.status,
            quarantine=decision.quarantine,
            requires_review=decision.requires_review,
            policy_version=decision.policy_version,
            reason=decision.reason,
            result={
                **decision.as_metadata(),
                "surface": "upload_file",
                "conversation_id": str(request.GET.get("conversationId") or ""),
                "client_id": str(request.GET.get("clientId") or ""),
                "device_id_present": bool(request.headers.get("X-Device-Id") or request.GET.get("device_id")),
                "visibility": visibility,
            },
        )
        try:
            from apps.moderation.services import create_media_safety_alert_for_scan

            create_media_safety_alert_for_scan(safety_scan, actor=request.user, request=request)
        except Exception:
            pass

        attachment = {
            "id": identifier,
            "url": public_url if (settings.DEBUG and not decision.quarantine) or (is_public_upload and not decision.quarantine) else "",
            "localUrl": public_url if settings.DEBUG else "",
            "originalName": filename,
            "mimeType": mime_type,
            "size": upload.size,
            "kind": _guess_attachment_kind(mime_type),
            "visibility": "public" if is_public_upload else "private",
            "private": not is_public_upload,
            "scanStatus": scan_status,
            "quarantined": decision.quarantine,
            "requiresReview": decision.requires_review,
            "safetyScanId": str(safety_scan.id),
            "safety": user_safe_upload_response(decision),
        }

        return Response({"attachment": attachment}, status=status.HTTP_201_CREATED)
