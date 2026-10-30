from django.urls import path

from . import views

urlpatterns = [
    path("prayer/watches/", views.WatchListView.as_view()),
    path("prayer/watches/<int:hour>/", views.WatchDetailView.as_view()),
    path("prayer/watches/<int:hour>/leadership/claim/", views.ClaimLeadershipView.as_view()),
    path("prayer/watches/<int:hour>/leadership/release/", views.ReleaseLeadershipView.as_view()),
    path("prayer/watches/<int:hour>/leadership/admin-claim/", views.AdminSoloLeadershipView.as_view()),
    path("prayer/watches/<int:hour>/leadership/share-request/", views.ShareRequestView.as_view()),
    path("prayer/watches/<int:hour>/leadership/share-respond/", views.ShareRespondView.as_view()),
    path("prayer/watches/<int:hour>/leadership/override/", views.OverrideLeadershipView.as_view()),
    path("prayer/watches/<int:hour>/participation-mode/", views.ParticipationModeView.as_view()),
    path("prayer/watches/<int:hour>/prayer-points/", views.PrayerPointsView.as_view()),
    path("prayer/watches/<int:hour>/admins/", views.WatchAdminsView.as_view()),
    path("prayer/my-activity/", views.MyActivityView.as_view()),
]
