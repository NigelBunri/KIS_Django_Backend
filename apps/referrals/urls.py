from django.urls import path

from .views import MyReferralsView

urlpatterns = [
    path("referrals/me/", MyReferralsView.as_view()),
]
