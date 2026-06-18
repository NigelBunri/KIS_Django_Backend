"""Admin partner oversight views — global partner list, health scores, compliance."""
from __future__ import annotations

from django.core.paginator import Paginator
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from admin_control.audit.logging import AuditLogger
from admin_control.permissions import IsAdminControlUser


def _safe_int(val, default, lo=1, hi=250):
    try:
        return max(lo, min(int(val), hi))
    except (TypeError, ValueError):
        return default


class AdminPartnerListView(APIView):
    """
    GET /control/admin/partners/
    Platform-wide partner list with member counts and health snapshot.
    Query params: q, is_active, tier (owner tier), page, per_page
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "partners.view"

    def get(self, request):
        from apps.partners.models import Partner

        qs = Partner.objects.select_related("owner").order_by("-created_at")

        q = request.query_params.get("q", "").strip()
        if q:
            from django.db.models import Q as DQ
            qs = qs.filter(DQ(name__icontains=q) | DQ(slug__icontains=q))

        is_active = request.query_params.get("is_active")
        if is_active in ("true", "false"):
            qs = qs.filter(is_active=(is_active == "true"))

        page_num = _safe_int(request.query_params.get("page", 1), 1, lo=1, hi=10000)
        per_page = _safe_int(request.query_params.get("per_page", 25), 25, lo=1, hi=100)
        paginator = Paginator(qs, per_page)
        page_obj = paginator.get_page(page_num)

        return Response({
            "partners": [_serialize_partner(p) for p in page_obj.object_list],
            "pagination": {
                "page": page_obj.number,
                "per_page": per_page,
                "total_pages": paginator.num_pages,
                "total_items": paginator.count,
            },
        })


class AdminPartnerDetailView(APIView):
    """
    GET   /control/admin/partners/<partner_id>/
    PATCH /control/admin/partners/<partner_id>/   — activate/deactivate, tier change for owner
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "partners.view"

    def get(self, request, partner_id):
        partner = _get_partner_or_404(partner_id)
        if isinstance(partner, Response):
            return partner

        from apps.partners.models import PartnerMembership, PartnerAuditEvent
        member_count = PartnerMembership.objects.filter(partner=partner, status="member").count()
        recent_events = list(
            PartnerAuditEvent.objects.filter(partner=partner)
            .order_by("-created_at")[:20]
            .values("id", "action", "actor_id", "created_at", "metadata")
        )
        return Response({
            "partner": _serialize_partner(partner, full=True),
            "member_count": member_count,
            "recent_audit_events": recent_events,
        })

    def patch(self, request, partner_id):
        partner = _get_partner_or_404(partner_id)
        if isinstance(partner, Response):
            return partner

        changed = []
        if "is_active" in request.data:
            partner.is_active = bool(request.data["is_active"])
            if not partner.is_active and not partner.deactivated_at:
                partner.deactivated_at = timezone.now()
                changed.append("deactivated_at")
            elif partner.is_active:
                partner.deactivated_at = None
                changed.append("deactivated_at")
            changed.append("is_active")

        if changed:
            partner.save(update_fields=changed)
            AuditLogger.log(
                actor=request.user,
                action_type="partner.admin_updated",
                target_app="partners",
                target_model="Partner",
                target_pk=str(partner.id),
                metadata={"fields": changed},
            )

        return Response({"partner": _serialize_partner(partner)})


class AdminPartnerStatsView(APIView):
    """
    GET /control/admin/partners/stats/
    Platform-wide partner KPIs + 30-day growth series.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "partners.view"

    def get(self, request):
        from datetime import timedelta
        from django.db.models import Count
        from apps.partners.models import Partner

        now = timezone.now()
        total = Partner.objects.count()
        active = Partner.objects.filter(is_active=True).count()
        deactivated = total - active
        new_30d = Partner.objects.filter(created_at__gte=now - timedelta(days=30)).count()

        growth = []
        for i in range(30, 0, -1):
            day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            growth.append({
                "date": day_start.strftime("%Y-%m-%d"),
                "new_partners": Partner.objects.filter(created_at__gte=day_start, created_at__lt=day_end).count(),
            })

        by_owner_tier = list(
            Partner.objects.select_related("owner")
            .values("owner__tier")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        return Response({
            "total_partners": total,
            "active_partners": active,
            "deactivated_partners": deactivated,
            "new_30d": new_30d,
            "growth_series_30d": growth,
            "by_owner_tier": by_owner_tier,
            "generated_at": now.isoformat(),
        })


class AdminRevenueStatsView(APIView):
    """
    GET /control/admin/revenue/
    Platform-wide revenue snapshot and 30-day daily revenue series.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "dashboard.view"

    def get(self, request):
        from datetime import timedelta
        from django.db.models import Sum
        from apps.events.models import TicketSale

        now = timezone.now()

        def _revenue(qs):
            return (qs.aggregate(total=Sum("total_cents"))["total"] or 0) / 100.0

        total_revenue = _revenue(TicketSale.objects.filter(status="completed"))
        revenue_30d = _revenue(
            TicketSale.objects.filter(status="completed", purchased_at__gte=now - timedelta(days=30))
        )
        revenue_7d = _revenue(
            TicketSale.objects.filter(status="completed", purchased_at__gte=now - timedelta(days=7))
        )

        series = []
        for i in range(30, 0, -1):
            day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            day_rev = _revenue(
                TicketSale.objects.filter(
                    status="completed",
                    purchased_at__gte=day_start,
                    purchased_at__lt=day_end,
                )
            )
            series.append({"date": day_start.strftime("%Y-%m-%d"), "revenue_usd": round(day_rev, 2)})

        return Response({
            "total_revenue_usd": round(total_revenue, 2),
            "revenue_30d_usd": round(revenue_30d, 2),
            "revenue_7d_usd": round(revenue_7d, 2),
            "series_30d": series,
            "generated_at": now.isoformat(),
        })


class AdminEngagementStatsView(APIView):
    """
    GET /control/admin/engagement/
    Content engagement stats: posts, reactions, messages, channels.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "dashboard.view"

    def get(self, request):
        from datetime import timedelta
        from apps.chat.models import Conversation
        from apps.partners.models import PartnerPost

        now = timezone.now()
        posts_7d = PartnerPost.objects.filter(created_at__gte=now - timedelta(days=7)).count()
        posts_30d = PartnerPost.objects.filter(created_at__gte=now - timedelta(days=30)).count()
        conversations_7d = Conversation.objects.filter(created_at__gte=now - timedelta(days=7)).count()
        conversations_30d = Conversation.objects.filter(created_at__gte=now - timedelta(days=30)).count()

        post_series = []
        for i in range(30, 0, -1):
            day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            post_series.append({
                "date": day_start.strftime("%Y-%m-%d"),
                "posts": PartnerPost.objects.filter(created_at__gte=day_start, created_at__lt=day_end).count(),
                "conversations": Conversation.objects.filter(created_at__gte=day_start, created_at__lt=day_end).count(),
            })

        return Response({
            "posts_7d": posts_7d,
            "posts_30d": posts_30d,
            "conversations_7d": conversations_7d,
            "conversations_30d": conversations_30d,
            "series_30d": post_series,
            "generated_at": now.isoformat(),
        })


class AdminAnalyticsDashboardsView(APIView):
    """
    GET /control/admin/analytics/dashboards/
    Aggregated dashboards payload: combines revenue + engagement stats.
    """
    permission_classes = [IsAuthenticated, IsAdminControlUser]
    required_permission = "dashboard.view"

    def get(self, request):
        from datetime import timedelta
        from django.db.models import Sum
        from apps.events.models import TicketSale
        from apps.chat.models import Conversation
        from apps.partners.models import PartnerPost

        now = timezone.now()

        def _revenue(qs):
            return (qs.aggregate(total=Sum("total_cents"))["total"] or 0) / 100.0

        total_revenue = _revenue(TicketSale.objects.filter(status="completed"))
        revenue_30d = _revenue(
            TicketSale.objects.filter(status="completed", purchased_at__gte=now - timedelta(days=30))
        )
        revenue_7d = _revenue(
            TicketSale.objects.filter(status="completed", purchased_at__gte=now - timedelta(days=7))
        )

        posts_7d = PartnerPost.objects.filter(created_at__gte=now - timedelta(days=7)).count()
        posts_30d = PartnerPost.objects.filter(created_at__gte=now - timedelta(days=30)).count()
        conversations_7d = Conversation.objects.filter(created_at__gte=now - timedelta(days=7)).count()
        conversations_30d = Conversation.objects.filter(created_at__gte=now - timedelta(days=30)).count()

        return Response({
            "generated_at": now.isoformat(),
            "revenue": {
                "total_revenue_usd": round(total_revenue, 2),
                "revenue_30d_usd": round(revenue_30d, 2),
                "revenue_7d_usd": round(revenue_7d, 2),
            },
            "engagement": {
                "posts_7d": posts_7d,
                "posts_30d": posts_30d,
                "conversations_7d": conversations_7d,
                "conversations_30d": conversations_30d,
            },
        })


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_partner_or_404(partner_id):
    from apps.partners.models import Partner
    try:
        return Partner.objects.select_related("owner").get(id=partner_id)
    except (Partner.DoesNotExist, Exception):
        return Response({"detail": "Partner not found."}, status=status.HTTP_404_NOT_FOUND)


def _serialize_partner(partner, full: bool = False):
    base = {
        "id": str(partner.id),
        "slug": partner.slug,
        "name": partner.name,
        "is_active": partner.is_active,
        "created_at": partner.created_at.isoformat() if partner.created_at else None,
        "deactivated_at": partner.deactivated_at.isoformat() if partner.deactivated_at else None,
        "owner": {
            "id": str(partner.owner_id) if partner.owner_id else None,
            "email": partner.owner.email if hasattr(partner, "owner") and partner.owner else None,
            "username": partner.owner.username if hasattr(partner, "owner") and partner.owner else None,
            "tier": partner.owner.tier if hasattr(partner, "owner") and partner.owner else None,
        } if partner.owner_id else None,
    }
    if full:
        base["avatar_url"] = getattr(partner, "avatar_url", None)
    return base
