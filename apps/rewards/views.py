from __future__ import annotations

from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AchievementDefinition, RewardLedgerEntry
from .serializers import AchievementSerializer, RewardLedgerEntrySerializer
from .services import get_reward_balance


class RewardBalanceView(APIView):
    """
    GET /api/v1/rewards/balance/
    available/pending mirror apps.rewards.services.get_reward_balance
    exactly — the same formula used everywhere server-side (sum of
    CONFIRMED/REDEEMED for available, PENDING for pending; nothing is
    computed differently for display than for the redemption engine
    itself). this_period_* covers the current calendar month, for a
    "+1,250 earned / -500 used this month" style summary.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        balance = get_reward_balance(request.user)

        now = timezone.now()
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        period_entries = RewardLedgerEntry.objects.filter(
            user=request.user,
            status__in=RewardLedgerEntry.BALANCE_STATUSES,
            effective_at__gte=period_start,
        )
        this_period_earned = sum(e.amount for e in period_entries if e.amount > 0)
        this_period_spent = -sum(e.amount for e in period_entries if e.amount < 0)

        return Response({
            "available": balance["available"],
            "pending": balance["pending"],
            "this_period_earned": this_period_earned,
            "this_period_spent": this_period_spent,
        }, status=status.HTTP_200_OK)


class RewardHistoryView(generics.ListAPIView):
    """GET /api/v1/rewards/history/ — this user's ledger, newest first,
    paginated via the project's standard pagination class. Every field
    shown is exactly what the ledger stores — no client-side reconstruction
    of "why" from anything other than type/source/description."""
    permission_classes = [IsAuthenticated]
    serializer_class = RewardLedgerEntrySerializer

    def get_queryset(self):
        return RewardLedgerEntry.objects.filter(user=self.request.user).order_by("-effective_at")


class AchievementCatalogView(APIView):
    """
    GET /api/v1/rewards/achievements/
    Every active achievement plus this user's completion state, derived
    directly from the ledger's idempotency-key-enforced dedup (the same
    mechanism grant_achievement itself relies on) rather than a separate
    tracking table — there isn't one, by the Phase 1/2 design.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        definitions = AchievementDefinition.objects.filter(is_active=True).order_by("title")
        completed_entries = {
            e.source: e.effective_at
            for e in RewardLedgerEntry.objects.filter(
                user=request.user, type=RewardLedgerEntry.TYPE_ACHIEVEMENT,
            )
        }
        results = [
            {
                "code": d.code,
                "title": d.title,
                "coin_amount": d.coin_amount,
                "completed": d.code in completed_entries,
                "completed_at": completed_entries.get(d.code),
            }
            for d in definitions
        ]
        serializer = AchievementSerializer(results, many=True)
        return Response({"results": serializer.data}, status=status.HTTP_200_OK)
