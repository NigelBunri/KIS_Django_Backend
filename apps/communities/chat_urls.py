from rest_framework.routers import DefaultRouter

from .views import CommunityViewSet


app_name = "chat_communities"

router = DefaultRouter()
router.register(r"", CommunityViewSet, basename="chat-community")

urlpatterns = router.urls
