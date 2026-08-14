from django.urls import path

from .views import AchievementCatalogView, RewardBalanceView, RewardHistoryView

urlpatterns = [
    path("rewards/balance/", RewardBalanceView.as_view()),
    path("rewards/history/", RewardHistoryView.as_view()),
    path("rewards/achievements/", AchievementCatalogView.as_view()),
]
