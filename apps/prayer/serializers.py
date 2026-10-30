# apps/prayer/serializers.py
from __future__ import annotations

from rest_framework import serializers


class StageSerializer(serializers.Serializer):
    key = serializers.CharField(allow_null=True)
    label = serializers.CharField(allow_null=True)
    display_text = serializers.CharField(allow_null=True)
    started_at = serializers.DateTimeField(allow_null=True)
    ends_at = serializers.DateTimeField(allow_null=True)
    seconds_remaining = serializers.IntegerField(allow_null=True)


class LeaderSerializer(serializers.Serializer):
    id = serializers.CharField()
    display_name = serializers.CharField()
    kind = serializers.CharField()  # "member" | "admin"


class WatchSummarySerializer(serializers.Serializer):
    """One row in the 'All 24 watches' list."""
    hour = serializers.IntegerField()
    name = serializers.CharField()
    group_id = serializers.CharField()
    conversation_id = serializers.CharField()
    scheduled_start = serializers.DateTimeField()
    scheduled_end = serializers.DateTimeField()
    session_status = serializers.CharField()  # scheduled | active | completed | interrupted
    is_current_watch = serializers.BooleanField()
    leader = LeaderSerializer(allow_null=True)
    sharing_admin = LeaderSerializer(allow_null=True)
    participant_count = serializers.IntegerField()


class WatchDetailSerializer(WatchSummarySerializer):
    session_id = serializers.CharField()
    participation_mode = serializers.CharField()
    stage = StageSerializer(allow_null=True)
    seconds_remaining_in_session = serializers.IntegerField(allow_null=True)
    my_leadership_state = serializers.CharField()  # see views.LEADERSHIP_STATE_* constants
    my_monthly_leadership_count = serializers.IntegerField()
    monthly_leadership_limit = serializers.IntegerField()
    is_group_admin = serializers.BooleanField()
    is_go = serializers.BooleanField()
    pending_share_request = serializers.DictField(allow_null=True)


class PrayerPointSerializer(serializers.Serializer):
    id = serializers.CharField()
    user_id = serializers.CharField()
    display_name = serializers.CharField()
    text = serializers.CharField()
    is_mine = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class SubmitPrayerPointSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=500, allow_blank=False)


class MyActivitySerializer(serializers.Serializer):
    joined_watch_hours = serializers.ListField(child=serializers.IntegerField())
    upcoming_leadership = serializers.ListField(child=serializers.DictField())
    led_this_month = serializers.IntegerField()
    remaining_this_month = serializers.IntegerField()
    monthly_limit = serializers.IntegerField()


class GroupAdminSerializer(serializers.Serializer):
    user_id = serializers.CharField()
    display_name = serializers.CharField()
    phone = serializers.CharField(allow_blank=True)
