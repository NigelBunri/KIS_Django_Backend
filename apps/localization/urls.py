from django.urls import path

from .views import LanguageFileView

app_name = "localization"

urlpatterns = [
    path("languages/<str:code>/", LanguageFileView.as_view(), name="language-file"),
]
