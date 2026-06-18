# apps/channels/subchannel_urls.py
from rest_framework.routers import DefaultRouter

from .views import SubchannelViewSet

router = DefaultRouter()
router.register(r"subchannels", SubchannelViewSet, basename="subchannel-standalone")

urlpatterns = router.urls
