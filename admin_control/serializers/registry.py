"""Serializers for exposed admin models."""
from rest_framework import serializers


class ModelRegistrySerializer(serializers.Serializer):
    app_label = serializers.CharField()
    models = serializers.ListField(child=serializers.CharField())
