"""Serializers for admin dashboard payloads."""
from rest_framework import serializers


class DashboardSummarySerializer(serializers.Serializer):
    status = serializers.CharField()
    generated_at = serializers.DateTimeField()
    widgets = serializers.DictField(child=serializers.DictField())
    graphs = serializers.DictField(
        child=serializers.ListField(child=serializers.DictField()), required=False
    )
    top_institutions = serializers.ListField(
        child=serializers.DictField(), required=False
    )
    suspicious_activity = serializers.ListField(
        child=serializers.DictField(), required=False
    )
    system_health = serializers.DictField(required=False)
    database_growth = serializers.DictField(
        child=serializers.IntegerField(), required=False
    )
    micro_apps = serializers.ListField(
        child=serializers.DictField(), required=False
    )
    micro_apps_detailed = serializers.ListField(
        child=serializers.DictField(), required=False
    )
    live_activity = serializers.ListField(
        child=serializers.DictField(), required=False
    )
    api_usage_per_endpoint = serializers.ListField(
        child=serializers.DictField(), required=False
    )
    institution_activity = serializers.ListField(
        child=serializers.DictField(), required=False
    )
    institution_growth = serializers.ListField(
        child=serializers.DictField(), required=False
    )
    chat_metrics = serializers.ListField(
        child=serializers.DictField(), required=False
    )
    message_throughput = serializers.DictField(required=False)
