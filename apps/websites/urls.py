from django.urls import path

from apps.websites import views

app_name = "websites"

urlpatterns = [
    # Public read API (AllowAny) — kingdomimpactventures.org/page/<slug>[/<page-slug>]
    path("public/sites/<slug:website_slug>/", views.WebsitePublicSiteView.as_view(), name="public-site"),
    path("public/sites/<slug:website_slug>/pages/<slug:page_slug>/", views.WebsitePublicPageView.as_view(), name="public-page"),
    path("public/sitemap-plan/", views.WebsitePublicSitemapPlanView.as_view(), name="public-sitemap-plan"),

    # Authenticated owner CRUD
    path("mine/", views.WebsiteMineView.as_view(), name="mine"),
    path("<uuid:website_id>/", views.WebsiteDetailView.as_view(), name="detail"),
    path("<uuid:website_id>/publish/", views.WebsitePublishView.as_view(), name="publish"),
    path("<uuid:website_id>/unpublish/", views.WebsiteUnpublishView.as_view(), name="unpublish"),
    path("<uuid:website_id>/preview-token/", views.WebsitePreviewTokenView.as_view(), name="preview-token"),
    path("<uuid:website_id>/pages/", views.WebsitePageListCreateView.as_view(), name="page-list-create"),
    path("<uuid:website_id>/pages/<uuid:page_id>/", views.WebsitePageDetailView.as_view(), name="page-detail"),
    path("<uuid:website_id>/pages/<uuid:page_id>/publish/", views.WebsitePagePublishView.as_view(), name="page-publish"),
    path("<uuid:website_id>/pages/<uuid:page_id>/unpublish/", views.WebsitePageUnpublishView.as_view(), name="page-unpublish"),

    path("kis-content/<str:target_type>/search/", views.WebsiteKisContentSearchView.as_view(), name="kis-content-search"),
]
