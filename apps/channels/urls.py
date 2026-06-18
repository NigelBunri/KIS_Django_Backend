# apps/channels/urls.py
from rest_framework.routers import DefaultRouter

from .views import ChannelViewSet, SubchannelViewSet

app_name = "channels"

router = DefaultRouter()
router.register(r"channels", ChannelViewSet, basename="channel")
router.register(r"subchannels", SubchannelViewSet, basename="subchannel")

urlpatterns = router.urls
