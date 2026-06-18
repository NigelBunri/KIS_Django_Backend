# media/urls.py
from rest_framework.routers import DefaultRouter
from django.urls import path, include
from .views import MediaAssetViewSet, ProcessingJobViewSet, MediaSafetyScanViewSet

router = DefaultRouter()
router.register(r"media/assets", MediaAssetViewSet, basename="asset")
router.register(r"media/jobs", ProcessingJobViewSet, basename="job")
router.register(r"media/media-safety-scans", MediaSafetyScanViewSet, basename="media-safety-scan")

urlpatterns = [
    path("", include(router.urls)),
]
