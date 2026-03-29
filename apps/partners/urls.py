# apps/partners/urls.py
from rest_framework.routers import DefaultRouter

from .views import PartnerViewSet, PartnerPostViewSet

app_name = "partners"

router = DefaultRouter()
# Register explicit sub-routes before the empty prefix to avoid conflicts with the detail route.
router.register(r"posts", PartnerPostViewSet, basename="partner-post")
router.register(r"", PartnerViewSet, basename="partner")

urlpatterns = router.urls
