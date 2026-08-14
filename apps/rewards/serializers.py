from __future__ import annotations

from rest_framework import serializers

from .models import RewardLedgerEntry


class RewardLedgerEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = RewardLedgerEntry
        fields = [
            "id", "type", "source", "amount", "status",
            "description", "effective_at", "expires_at", "created_at",
        ]


class AchievementSerializer(serializers.Serializer):
    code = serializers.CharField()
    title = serializers.CharField()
    coin_amount = serializers.IntegerField()
    completed = serializers.BooleanField()
    completed_at = serializers.DateTimeField(allow_null=True)
