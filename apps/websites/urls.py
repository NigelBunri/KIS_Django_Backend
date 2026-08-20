from django.urls import path

from apps.websites import views

app_name = "websites"

urlpatterns = [
    # Public read API (AllowAny) — kingdomimpactventures.org/page/<slug>[/<page-slug>]
    path("public/sites/<slug:website_slug>/", views.WebsitePublicSiteView.as_view(), name="public-site"),
    path("public/sites/<slug:website_slug>/pages/<slug:page_slug>/", views.WebsitePublicPageView.as_view(), name="public-page"),
    path(
        "public/sites/<slug:website_slug>/pages/<slug:page_slug>/sections/<str:section_id>/more/",
        views.WebsitePublicKisContentLoadMoreView.as_view(), name="public-kis-content-load-more",
    ),
    path(
        "public/sites/<slug:website_slug>/kis-content/<str:target_type>/<str:item_id>/",
        views.WebsitePublicKisContentDetailView.as_view(), name="public-kis-content-detail",
    ),
    path("public/sitemap-plan/", views.WebsitePublicSitemapPlanView.as_view(), name="public-sitemap-plan"),
    path(
        "public/sites/<slug:website_slug>/pages/<slug:page_slug>/forms/<str:section_id>/submit/",
        views.WebsitePublicFormSubmitView.as_view(), name="public-form-submit",
    ),
    path("public/analytics/", views.WebsitePublicAnalyticsBeaconView.as_view(), name="public-analytics-beacon"),
    path("public/sites/by-domain/<str:domain>/", views.WebsitePublicSiteByDomainView.as_view(), name="public-site-by-domain"),

    # Authenticated owner CRUD
    path("mine/", views.WebsiteMineView.as_view(), name="mine"),
    path("templates/", views.WebsiteTemplateListView.as_view(), name="template-list"),
    path("redeem-invite/", views.WebsiteInviteRedeemView.as_view(), name="redeem-invite"),
    path("<uuid:website_id>/form-responses/", views.WebsiteFormResponsesView.as_view(), name="form-responses"),
    path("<uuid:website_id>/analytics/summary/", views.WebsiteAnalyticsSummaryView.as_view(), name="analytics-summary"),
    path("<uuid:website_id>/custom-domain/", views.WebsiteCustomDomainView.as_view(), name="custom-domain"),
    path("<uuid:website_id>/webhooks/", views.WebsiteWebhookListCreateView.as_view(), name="webhook-list-create"),
    path("<uuid:website_id>/webhooks/<uuid:webhook_id>/", views.WebsiteWebhookDetailView.as_view(), name="webhook-detail"),
    path("<uuid:website_id>/collaborators/", views.WebsiteCollaboratorListView.as_view(), name="collaborator-list"),
    path(
        "<uuid:website_id>/collaborators/<uuid:collaborator_id>/",
        views.WebsiteCollaboratorDetailView.as_view(), name="collaborator-detail",
    ),
    path("<uuid:website_id>/invites/", views.WebsiteInviteListCreateView.as_view(), name="invite-list-create"),
    path("<uuid:website_id>/invites/<uuid:invite_id>/revoke/", views.WebsiteInviteRevokeView.as_view(), name="invite-revoke"),
    path("<uuid:website_id>/", views.WebsiteDetailView.as_view(), name="detail"),
    path("<uuid:website_id>/publish/", views.WebsitePublishView.as_view(), name="publish"),
    path("<uuid:website_id>/unpublish/", views.WebsiteUnpublishView.as_view(), name="unpublish"),
    path("<uuid:website_id>/preview-token/", views.WebsitePreviewTokenView.as_view(), name="preview-token"),
    path("<uuid:website_id>/pages/", views.WebsitePageListCreateView.as_view(), name="page-list-create"),
    path("<uuid:website_id>/pages/<uuid:page_id>/", views.WebsitePageDetailView.as_view(), name="page-detail"),
    path("<uuid:website_id>/pages/<uuid:page_id>/publish/", views.WebsitePagePublishView.as_view(), name="page-publish"),
    path("<uuid:website_id>/pages/<uuid:page_id>/unpublish/", views.WebsitePageUnpublishView.as_view(), name="page-unpublish"),

    path("kis-content/<str:target_type>/search/", views.WebsiteKisContentSearchView.as_view(), name="kis-content-search"),
    path("kis-video/search/", views.WebsiteKisVideoSearchView.as_view(), name="kis-video-search"),
]
