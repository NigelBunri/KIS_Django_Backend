from rest_framework import serializers
from .models import (
    Metric,
    EventStream,
    Dashboard,
    AppSetting,
    FeatureFlag,
    Alert,
    EngagementScore,
    ClinicalAnalyticsReport,
    RiskStratification,
    OutcomeBenchmark,
    PatientSatisfactionScore,
    OutreachCampaign,
    WellnessChallenge,
    HabitTrackingEntry,
)

class MetricSerializer(serializers.ModelSerializer):
    class Meta:
        model = Metric
        fields = '__all__'
        read_only_fields = ['id','predicted_value','confidence','captured_at']

class EventStreamSerializer(serializers.ModelSerializer):
    class Meta:
        model = EventStream
        fields = '__all__'
        read_only_fields = ['id','processed','timestamp']

class DashboardSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dashboard
        fields = '__all__'

class AppSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppSetting
        fields = '__all__'

class FeatureFlagSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeatureFlag
        fields = '__all__'

class AlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = Alert
        fields = '__all__'
        read_only_fields = ['triggered_at']

class EngagementScoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = EngagementScore
        fields = '__all__'
        read_only_fields = ['calculated_at']


class ClinicalAnalyticsReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClinicalAnalyticsReport
        fields = '__all__'


class RiskStratificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = RiskStratification
        fields = '__all__'


class OutcomeBenchmarkSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutcomeBenchmark
        fields = '__all__'


class PatientSatisfactionScoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = PatientSatisfactionScore
        fields = '__all__'


class OutreachCampaignSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutreachCampaign
        fields = '__all__'


class WellnessChallengeSerializer(serializers.ModelSerializer):
    class Meta:
        model = WellnessChallenge
        fields = '__all__'


class HabitTrackingEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = HabitTrackingEntry
        fields = '__all__'
