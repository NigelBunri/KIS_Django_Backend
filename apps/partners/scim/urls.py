from django.urls import path

from .views import ScimUserDetailView, ScimUsersView

app_name = "partners_scim"

urlpatterns = [
    path("<slug:partner_slug>/Users", ScimUsersView.as_view(), name="scim-users"),
    path("<slug:partner_slug>/Users/<uuid:user_id>", ScimUserDetailView.as_view(), name="scim-user-detail"),
]
