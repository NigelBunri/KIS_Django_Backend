from django.urls import path
from . import views

urlpatterns = [
    path("seasons/",                    views.SeasonListCreateView.as_view(),  name="seasons-list"),
    path("seasons/mine/",               views.MySeasonListView.as_view(),      name="seasons-mine"),
    path("seasons/<int:pk>/",           views.SeasonDetailView.as_view(),      name="seasons-detail"),
    path("testimonies/",                views.TestimonyListCreateView.as_view(), name="testimonies-list"),
    path("testimonies/<int:pk>/",       views.TestimonyDetailView.as_view(),   name="testimonies-detail"),
    path("testimonies/<int:pk>/endorse/", views.EndorseTestimonyView.as_view(), name="testimonies-endorse"),
    path("testimony-reach/",            views.ReachOutView.as_view(),          name="testimony-reach"),
    path("testimony-reach/<int:pk>/",   views.ReachRespondView.as_view(),      name="testimony-reach-respond"),
]
