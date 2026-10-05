"""Real KPI aggregation for the RN Insights screens (Admin/Analytics
Dashboard, Partner Insights, Profile Insights).

The frontend's `fetchDashboardInsights(target, timeframe)` used to call
`/api/v1/dashboards/` (DashboardViewSet, staff-only CRUD over a saved
widget-layout *definition* model) expecting live KPI numbers back - that
endpoint never computed anything, it just listed/created Dashboard rows,
so every one of these screens rendered empty/garbage data. This module
replaces that wrong-tool wiring with real Count/Sum aggregation, scoped
per target:

- "analytics": platform-wide, admin/staff only.
- "partner": scoped to a partner org the requesting user actually has
  reporting permission on (first match) - never platform-wide data.
- "profile": the requesting user's own activity only.

Response shape matches `InsightPayload`/`normalizeInsights()` on the RN
side: {kpis, series, breakdown, distribution, top_items}.
"""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

VALID_TARGETS = {"analytics", "partner", "profile"}
TIMEFRAME_DAYS = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}


def _timeframe_to_days(raw: str | None) -> int:
    return TIMEFRAME_DAYS.get(str(raw or "7d").lower(), 7)


def _kpi(key: str, label: str, value, unit: str = "") -> dict:
    return {"id": key, "label": label, "value": value, "unit": unit}


def _platform_insights(days: int) -> dict:
    from apps.accounts.models import User
    from apps.broadcasts.models import BroadcastChannel, ChannelContent
    from apps.commerce.models import MarketplaceOrder, MarketplaceOrderStatus
    from apps.partners.models import Partner

    since = timezone.now() - timedelta(days=days)

    total_users = User.objects.count()
    new_users = User.objects.filter(created_at__gte=since).count()

    # SATISFIED/COMPLETED are the two statuses that mean real money actually
    # moved (see MarketplaceOrderStatus) - TEMPORAL/AWAITING_SATISFACTION
    # orders have not been paid for yet, CANCELLED/COMPLAINT reversed or
    # disputed the charge.
    paid_orders = MarketplaceOrder.objects.filter(
        status__in=[MarketplaceOrderStatus.SATISFIED, MarketplaceOrderStatus.COMPLETED]
    )
    revenue_total = paid_orders.aggregate(total=Sum("total_amount")).get("total") or 0
    recent_orders = MarketplaceOrder.objects.filter(created_at__gte=since).count()

    published_content = ChannelContent.objects.filter(status=ChannelContent.Status.PUBLISHED).count()
    recent_content = ChannelContent.objects.filter(
        status=ChannelContent.Status.PUBLISHED, created_at__gte=since
    ).count()

    active_partners = Partner.objects.count()

    kpis = [
        _kpi("total_users", "Total users", total_users),
        _kpi("new_users", f"New users ({days}d)", new_users),
        _kpi("revenue", "Marketplace revenue (paid orders)", float(revenue_total), "USD"),
        _kpi("recent_orders", f"Orders ({days}d)", recent_orders),
        _kpi("published_content", "Published content", published_content),
        _kpi("recent_content", f"New content ({days}d)", recent_content),
        _kpi("active_partners", "Partner organizations", active_partners),
    ]

    channel_breakdown = list(
        BroadcastChannel.objects.filter(is_deleted=False)
        .values("category")
        .annotate(value=Count("id"))
        .order_by("-value")[:8]
    )
    breakdown = [
        {"label": (row["category"] or "Uncategorized"), "value": row["value"]}
        for row in channel_breakdown
    ]

    return {"kpis": kpis, "series": [], "breakdown": breakdown, "distribution": [], "top_items": []}


def _resolve_user_partner(user):
    """First Partner org this user has reporting permission on - mirrors
    PartnerViewSet._require_permission's own-or-granted check, but without
    a specific partner pk from the request (the RN insights screen has no
    partner-id param), so we scan the user's own memberships rather than
    assuming platform-wide access."""
    from apps.partners.models import Partner, PartnerMembership, PartnerMembershipStatus
    from apps.partners.services import user_has_partner_permission

    owned = Partner.objects.filter(owner=user).first()
    if owned:
        return owned

    member_partner_ids = PartnerMembership.objects.filter(
        user=user, status=PartnerMembershipStatus.MEMBER
    ).values_list("partner_id", flat=True)
    for partner in Partner.objects.filter(id__in=member_partner_ids):
        if user_has_partner_permission(partner, user, "partner.reports.view"):
            return partner
    return None


def _partner_insights(user, days: int) -> dict:
    from apps.partners.models import PartnerMembership, PartnerMembershipStatus, PartnerPost, PartnerPostComment, PartnerPostReaction

    partner = _resolve_user_partner(user)
    if partner is None:
        return {"kpis": [], "series": [], "breakdown": [], "distribution": [], "top_items": []}

    since = timezone.now() - timedelta(days=days)
    members_qs = PartnerMembership.objects.filter(partner=partner)
    total_members = members_qs.count()
    active_members = members_qs.filter(status=PartnerMembershipStatus.MEMBER).count()

    posts_qs = PartnerPost.objects.filter(partner=partner)
    recent_posts = posts_qs.filter(created_at__gte=since).count()
    recent_reactions = PartnerPostReaction.objects.filter(
        post__partner=partner, created_at__gte=since
    ).count()
    recent_comments = PartnerPostComment.objects.filter(
        post__partner=partner, created_at__gte=since
    ).count()

    kpis = [
        _kpi("partner_name", partner.name, "", ""),
        _kpi("total_members", "Total members", total_members),
        _kpi("active_members", "Active members", active_members),
        _kpi("recent_posts", f"Posts ({days}d)", recent_posts),
        _kpi("recent_reactions", f"Reactions ({days}d)", recent_reactions),
        _kpi("recent_comments", f"Comments ({days}d)", recent_comments),
    ]
    return {"kpis": kpis, "series": [], "breakdown": [], "distribution": [], "top_items": []}


def _profile_insights(user, days: int) -> dict:
    from apps.broadcasts.models import BroadcastChannel, ChannelContent

    since = timezone.now() - timedelta(days=days)
    owned_channels = BroadcastChannel.objects.filter(owner_user=user, is_deleted=False)
    owned_contents = ChannelContent.objects.filter(channel__in=owned_channels, is_deleted=False)
    total_subscribers = owned_channels.aggregate(total=Sum("subscriber_count")).get("total") or 0
    recent_content = owned_contents.filter(created_at__gte=since).count()

    kpis = [
        _kpi("channels", "Your channels", owned_channels.count()),
        _kpi("subscribers", "Total subscribers", total_subscribers),
        _kpi("content_total", "Total content published", owned_contents.count()),
        _kpi("content_recent", f"Published ({days}d)", recent_content),
    ]

    top_items = [
        {
            "id": str(c.id),
            "title": c.title,
            "subtitle": (c.channel.name if c.channel_id else None),
            "metric": int((c.stats or {}).get("views") or 0),
        }
        for c in owned_contents.select_related("channel").order_by("-created_at")[:5]
    ]

    return {"kpis": kpis, "series": [], "breakdown": [], "distribution": [], "top_items": top_items}


class PlatformInsightsView(APIView):
    """GET /api/v1/analytics/insights/?target=analytics|partner|profile&timeframe=7d"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        target = str(request.query_params.get("target") or "profile").lower()
        if target not in VALID_TARGETS:
            target = "profile"
        days = _timeframe_to_days(request.query_params.get("timeframe"))

        if target == "analytics":
            if not (request.user.is_staff or request.user.is_superuser):
                raise PermissionDenied("Platform-wide analytics is admin-only.")
            data = _platform_insights(days)
        elif target == "partner":
            data = _partner_insights(request.user, days)
        else:
            data = _profile_insights(request.user, days)

        return Response({"data": data})
