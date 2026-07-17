# media/urls.py
from rest_framework.routers import DefaultRouter
from django.urls import path, include, re_path
from django.views.generic import RedirectView
from .views import (
    MediaAssetViewSet,
    ProcessingJobViewSet,
    MediaSafetyScanViewSet,
    MediaUploadInitiateView,
    ProfileImageUploadInitiateView,
    MediaUploadConfirmView,
)

router = DefaultRouter()
router.register(r"media/assets", MediaAssetViewSet, basename="asset")
# Backward-compatible public media route. Serializers and signed URLs have
# historically emitted /api/v1/assets/... while the router later moved the
# primary collection under /api/v1/media/assets/; keep both live for old app
# builds and signed URLs already issued to devices.
router.register(r"assets", MediaAssetViewSet, basename="legacy-asset")
router.register(r"media/jobs", ProcessingJobViewSet, basename="job")
router.register(r"media/media-safety-scans", MediaSafetyScanViewSet, basename="media-safety-scan")

urlpatterns = [
    path("", include(router.urls)),
    # Direct-to-S3 presigned-PUT upload handshake (initiate/confirm) — see
    # apps/media/upload_intent.py for the full flow.
    path("media/uploads/initiate/", MediaUploadInitiateView.as_view(), name="media-upload-initiate"),
    path(
        "media/uploads/profile-image/initiate/",
        ProfileImageUploadInitiateView.as_view(),
        name="media-upload-profile-image-initiate",
    ),
    path(
        "media/uploads/<uuid:upload_id>/confirm/",
        MediaUploadConfirmView.as_view(),
        name="media-upload-confirm",
    ),
    # Some app builds and server-side signed URLs omit the trailing slash on the
    # /download action.  Redirect them permanently so the token is still usable.
    re_path(
        r"^assets/(?P<pk>[^/]+)/download$",
        RedirectView.as_view(url="/api/v1/assets/%(pk)s/download/", query_string=True, permanent=False),
        name="legacy-asset-download-no-slash",
    ),
    re_path(
        r"^media/assets/(?P<pk>[^/]+)/download$",
        RedirectView.as_view(url="/api/v1/media/assets/%(pk)s/download/", query_string=True, permanent=False),
        name="asset-download-no-slash",
    ),
]
