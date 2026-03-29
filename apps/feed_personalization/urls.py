from django.urls import path

from .views import FeedPersonalizationEventView

app_name = "feed_personalization"

urlpatterns = [
    path("events/", FeedPersonalizationEventView.as_view(), name="events"),
]
