from rest_framework import serializers
from django.contrib.auth import get_user_model
from . import models

User = get_user_model()


class AuthorSerializer(serializers.ModelSerializer):
    avatar_url = serializers.SerializerMethodField()
    headline   = serializers.SerializerMethodField()

    class Meta:
        model  = User
        fields = ["id", "display_name", "avatar_url", "headline"]

    def get_avatar_url(self, obj):
        p = getattr(obj, "profile", None)
        return getattr(p, "avatar_url", "") or ""

    def get_headline(self, obj):
        p = getattr(obj, "profile", None)
        return getattr(p, "headline", "") or ""


class UserSeasonSerializer(serializers.ModelSerializer):
    user = AuthorSerializer(read_only=True)
    reach_count = serializers.SerializerMethodField()

    class Meta:
        model  = models.UserSeason
        fields = ["id", "user", "category", "title", "description", "visibility",
                  "is_active", "reach_count", "created_at", "resolved_at"]
        read_only_fields = ["id", "user", "reach_count", "created_at"]

    def get_reach_count(self, obj):
        return obj.reaches.count()


class UserTestimonySerializer(serializers.ModelSerializer):
    user = AuthorSerializer(read_only=True)

    class Meta:
        model  = models.UserTestimony
        fields = ["id", "user", "category", "title", "story", "is_available",
                  "endorsement_count", "created_at"]
        read_only_fields = ["id", "user", "endorsement_count", "created_at"]


class TestimonyReachSerializer(serializers.ModelSerializer):
    from_user = AuthorSerializer(read_only=True)
    to_user   = AuthorSerializer(read_only=True)
    season    = UserSeasonSerializer(read_only=True)
    testimony = UserTestimonySerializer(read_only=True)

    class Meta:
        model  = models.TestimonyReach
        fields = ["id", "from_user", "to_user", "season", "testimony",
                  "message", "status", "created_at"]
        read_only_fields = ["id", "from_user", "to_user", "season", "testimony", "created_at"]
