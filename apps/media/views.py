# media/views.py
import uuid
from urllib.parse import quote

from django.conf import settings
from django.core.files.storage import default_storage
from django.http import FileResponse
from django.utils.text import get_valid_filename
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from apps.accounts.jwt_auth import DeviceBoundJWTAuthentication

from . import upload_intent
from .models import MediaAsset, MediaVariant, ProcessingJob, MediaMetrics, MediaSafetyScan, MediaUploadIntent
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
from .signing import (
    MEDIA_SIGNED_URL_TTL_SECONDS,
    sign_media_asset_token as _sign_media_asset,
    token_allows_asset as _token_allows_asset,
)

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

    def get_object(self):
        # Phase 2: destroy() must remain idempotent — a repeated DELETE for
        # an already-soft-deleted asset should 204 again, not 404, but
        # get_queryset() (used by every other action) correctly still
        # excludes is_deleted=True rows from list/retrieve/update. Bypass
        # that exclusion for destroy only, so lifecycle.delete_asset's own
        # idempotent "already deleted" branch is reachable a second time.
        if self.action == "destroy":
            from django.shortcuts import get_object_or_404

            obj = get_object_or_404(MediaAsset.objects.select_related("owner"), pk=self.kwargs["pk"])
            self.check_object_permissions(self.request, obj)
            return obj
        return super().get_object()

    def perform_destroy(self, instance):
        # Phase 2: was a raw instance.delete() (Django ModelViewSet's
        # default) — a hard row delete, which for a canonical asset
        # (Phase 1+) would silently discard confirmed_at/attached_at/
        # target_type/target_id and orphan its MediaSafetyScan history.
        # Routes through the same soft-delete service the new generic
        # DELETE /api/v1/media/assets/:assetId/ endpoint uses (this method
        # backs that exact URL — MediaAssetViewSet's router registration
        # already owns it, so there's no second view/URL for it). Response
        # shape is unchanged: DRF's destroy() always returns 204 No
        # Content regardless of what perform_destroy does internally.
        from .services import lifecycle as media_lifecycle

        purpose = _resolve_purpose_for_asset(instance)
        media_lifecycle.delete_asset(user=self.request.user, asset=instance, purpose=purpose)

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


def _safe_storage_url(name: str) -> str:
    try:
        return default_storage.url(name)
    except Exception:
        return ""


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
        checksum = hash_upload(upload) if getattr(settings, "MEDIA_UPLOAD_CHECKSUM_ENABLED", False) else ""
        mime_type = upload.content_type or ""
        decision = scan_upload_for_explicit_content(filename=filename, mime_type=mime_type, context=context)
        relative_path = f"uploads/{identifier}/{filename}"
        try:
            saved_path = default_storage.save(relative_path, upload)
        except Exception:
            raise APIException("Unable to store this upload right now. Please retry.")
        sanitized_path = saved_path.replace("\\", "/")
        visibility = str(request.data.get("visibility") or "private").strip().lower()
        is_public_upload = visibility in PUBLIC_VISIBILITY_VALUES
        public_url = _safe_storage_url(sanitized_path) if is_public_upload and not decision.quarantine else ""
        scan_status = decision.status
        asset_status = "pending" if decision.quarantine or decision.requires_review else "ready"
        storage_provider = getattr(settings, "OBJECT_STORAGE_PROVIDER", "") or "default"
        media_asset = MediaAsset.objects.create(
            owner=request.user if request.user.is_authenticated else None,
            type=_guess_attachment_kind(mime_type),
            bucket_key=sanitized_path,
            canonical_url=public_url or None,
            mime_type=mime_type,
            bytes=upload.size or 0,
            checksum=checksum,
            status=asset_status,
            storage={
                "provider": storage_provider,
                "visibility": "public" if is_public_upload else "private",
                "scan_status": scan_status,
            },
            metadata={
                "upload_id": identifier,
                "original_name": filename,
                "context": context,
            },
        )

        safety_scan = MediaSafetyScan.objects.create(
            asset=media_asset,
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

        signed_download_url = ""
        if not is_public_upload and not decision.quarantine:
            signed_token = _sign_media_asset(media_asset)
            signed_path = f"/api/v1/assets/{media_asset.id}/download/?token={quote(signed_token)}"
            signed_download_url = request.build_absolute_uri(signed_path)

        display_url = ""
        if not decision.quarantine:
            display_url = public_url or signed_download_url

        attachment = {
            "id": identifier,
            "assetId": str(media_asset.id),
            "mediaAssetId": str(media_asset.id),
            "mediaAssetRef": str(media_asset.id),
            "url": display_url,
            "displayUrl": display_url,
            "publicUrl": public_url if is_public_upload and not decision.quarantine else "",
            "downloadUrl": signed_download_url,
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
            "asset": {
                "id": str(media_asset.id),
                "type": media_asset.type,
                "status": media_asset.status,
                "private": not is_public_upload,
            },
        }

        return Response({"attachment": attachment}, status=status.HTTP_201_CREATED)


class MediaUploadInitiateView(APIView):
    """POST /api/v1/media/uploads/initiate/ — generic direct-to-S3 handshake
    start. See apps/media/upload_intent.py for the full flow; this view is a
    thin HTTP wrapper so the same logic backs both the generic route and the
    profile-image-specific alias below."""

    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [DeviceBoundJWTAuthentication]
    throttle_scope = "upload"
    locked_context: str | None = None

    def post(self, request):
        context = self.locked_context or str(request.data.get("context") or "").strip()
        filename = request.data.get("filename")
        content_type = request.data.get("content_type")
        size_bytes = request.data.get("size_bytes")

        upload_intent.validate_initiate_payload(
            context=context, filename=filename, content_type=content_type, size_bytes=size_bytes
        )
        intent = upload_intent.create_upload_intent(
            user=request.user,
            context=context,
            filename=filename,
            content_type=str(content_type).strip().lower(),
            size_bytes=int(size_bytes),
            target_id=str(request.data.get("target_id") or ""),
        )
        return Response(upload_intent.build_initiate_response(intent), status=status.HTTP_201_CREATED)


class ProfileImageUploadInitiateView(MediaUploadInitiateView):
    """POST /api/v1/media/uploads/profile-image/initiate/ — the exact route
    named in the task spec. `kind` in the body ("avatar" | "cover", default
    "avatar") picks which profile field this upload targets; everything
    else is identical to the generic initiate view."""

    def post(self, request):
        kind = str(request.data.get("kind") or "avatar").strip().lower()
        self.locked_context = "profile_cover" if kind == "cover" else "profile_avatar"
        return super().post(request)


class MediaUploadConfirmView(APIView):
    """POST /api/v1/media/uploads/<uuid:upload_id>/confirm/ — verifies the S3
    object with head_object and attaches it via the context's handler. Works
    for any context with a registered attach handler (see
    apps.media.upload_intent.ATTACH_HANDLERS) — not profile-specific."""

    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [DeviceBoundJWTAuthentication]
    throttle_scope = "upload"

    def post(self, request, upload_id=None):
        result = upload_intent.confirm_upload_intent(user=request.user, upload_id=upload_id)
        return Response(result, status=status.HTTP_200_OK)


# --------------------------------------------------------------------------
# Phase 2: generic MediaAsset lifecycle endpoints. New features attach one
# purpose registry entry (apps/media/purposes.py) and call these — no new
# view, serializer, or URL of their own. Existing feature-specific
# attach/download endpoints are untouched and continue calling their own
# functions directly (see apps/commerce/views.py, apps/statuses/views.py);
# the *same* functions are now also reachable through these generic routes
# via the per-purpose hooks registered in each feature app's
# AppConfig.ready() (apps/commerce/media_hooks.py etc.) — one
# implementation, two doors in.
# --------------------------------------------------------------------------

from .serializers import CanonicalMediaAssetSerializer  # noqa: E402
from .services import access as media_access  # noqa: E402
from .services import attachments as media_attachments  # noqa: E402
from .services import lifecycle as media_lifecycle  # noqa: E402


class MediaAssetAttachView(APIView):
    """POST /api/v1/media/assets/<uuid:asset_id>/attach/ — see
    apps.media.services.attachments.attach_media for the full flow."""

    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [DeviceBoundJWTAuthentication]
    throttle_scope = "upload"

    def post(self, request, asset_id=None):
        target_type = str(request.data.get("targetType") or request.data.get("target_type") or "").strip()
        target_id = str(request.data.get("targetId") or request.data.get("target_id") or "").strip()
        slot = request.data.get("slot") or None
        position = request.data.get("position")
        if position is not None:
            try:
                position = int(position)
            except (TypeError, ValueError):
                raise ValidationError({"position": "Must be an integer."})

        if not target_type:
            raise ValidationError({"targetType": "This field is required."})
        if not target_id:
            raise ValidationError({"targetId": "This field is required."})

        asset = media_attachments.attach_media(
            user=request.user, asset_id=asset_id, target_type=target_type, target_id=target_id,
            slot=slot, position=position,
        )
        return Response(CanonicalMediaAssetSerializer(asset).data, status=status.HTTP_200_OK)


class MediaAssetSignedUrlView(APIView):
    """GET /api/v1/media/assets/<uuid:asset_id>/signed-url/ — see
    apps.media.services.access.get_signed_url. AllowAny at the DRF layer:
    real authorization is fully centralized in can_user_access_media, which
    some purposes (profile avatar/cover) deliberately allow anonymous
    viewers through, matching current ProfileViewSet visibility — see
    apps/accounts/media_hooks.py."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = [DeviceBoundJWTAuthentication]

    def get(self, request, asset_id=None):
        try:
            asset = MediaAsset.objects.get(id=asset_id)
        except (MediaAsset.DoesNotExist, ValueError, TypeError):
            raise NotFound("Media not found.")

        result = media_access.get_signed_url(user=request.user, asset=asset)
        return Response(result, status=status.HTTP_200_OK)


class MediaUploadCancelView(APIView):
    """POST /api/v1/media/uploads/<uuid:upload_id>/cancel/ — deliberately
    keyed by uploadId (MediaUploadIntent), not assetId. A pending upload
    normally has no MediaAsset yet (_ensure_canonical_asset only runs at
    confirm — apps/media/upload_intent.py), so an assetId-keyed cancel
    route would have nothing to resolve for the overwhelmingly common case
    (cancelling before confirm ever happens). See
    apps.media.services.lifecycle.cancel_upload and the Phase 2 report."""

    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [DeviceBoundJWTAuthentication]
    throttle_scope = "upload"

    def post(self, request, upload_id=None):
        result = media_lifecycle.cancel_upload(user=request.user, upload_id=upload_id)
        return Response(result, status=status.HTTP_200_OK)


def _resolve_purpose_for_asset(asset: MediaAsset):
    """Returns the registered MediaPurpose for `asset`, or a bare
    placeholder (allow_delete=True, no hooks) for legacy multipart assets
    or unregistered purposes — so lifecycle.delete_asset always has a
    purpose object to read allow_delete/detach_handler from, without every
    caller having to special-case "no purpose"."""
    from . import purposes as purposes_module

    if asset.purpose:
        try:
            return purposes_module.get_purpose(asset.purpose)
        except KeyError:
            pass
    return purposes_module.MediaPurpose(
        name=asset.purpose or "", context="", allowed_mime_types=(), max_bytes=0,
        key_prefix="", moderation_context="", retention_days=None,
    )
