from rest_framework import serializers
from .models import BridgeAccount, BridgeThread, BridgeMessage, BridgeAutomation, BridgeAnalytics

class BridgeAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = BridgeAccount
        fields = [
            'id',
            'user_id',
            'external_app',
            'external_user_id',
            'last_sync_at',
            'metadata',
            'created_at',
            'updated_at',
            'is_deleted',
        ]
        read_only_fields = ['id', 'user_id', 'last_sync_at', 'created_at', 'updated_at', 'is_deleted']


class BridgeAccountCredentialSerializer(serializers.ModelSerializer):
    class Meta:
        model = BridgeAccount
        fields = [
            'id',
            'external_app',
            'external_user_id',
            'access_token',
            'refresh_token',
            'metadata',
        ]
        read_only_fields = ['id']

class BridgeThreadSerializer(serializers.ModelSerializer):
    class Meta:
        model = BridgeThread
        fields = [
            'id',
            'external_app',
            'external_thread_id',
            'linked_thread_id',
            'topic',
            'metadata',
            'is_archived',
            'last_activity_at',
            'created_at',
            'updated_at',
            'is_deleted',
        ]
        read_only_fields = ['id', 'linked_thread_id', 'last_activity_at', 'created_at', 'updated_at', 'is_deleted']

class BridgeMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = BridgeMessage
        fields = '__all__'
        read_only_fields = ['id','status','received_at','sent_at','ai_category','sentiment_score','is_flagged']

class BridgeAutomationSerializer(serializers.ModelSerializer):
    class Meta:
        model = BridgeAutomation
        fields = '__all__'
        read_only_fields = ['id','last_triggered_at']

class BridgeAnalyticsSerializer(serializers.ModelSerializer):
    class Meta:
        model = BridgeAnalytics
        fields = '__all__'
        read_only_fields = ['id','top_external_apps','engagement_score']
