import uuid
from datetime import timedelta

import django.utils.timezone
from django.db import migrations, models


def create_broadcast_features(apps, schema_editor):
    BroadcastFeature = apps.get_model("broadcasts", "BroadcastFeature")
    features = [
        {
            "slug": "broadcast_scheduling",
            "name": "Broadcast scheduling",
            "description": "Plan broadcasts ahead of time and let the system auto-launch.",
            "category": "Planning",
            "default_enabled": True,
        },
        {
            "slug": "live_ratings",
            "name": "Live ratings",
            "description": "Let viewers rate the broadcast in real time.",
            "category": "Engagement",
            "default_enabled": True,
        },
        {
            "slug": "automated_highlights",
            "name": "Automated highlights",
            "description": "AI-generated highlight reels after every broadcast.",
            "category": "Discovery",
            "default_enabled": True,
        },
        {
            "slug": "interactive_polling",
            "name": "Interactive polls",
            "description": "Embed polls that update instantly for the audience.",
            "category": "Engagement",
            "default_enabled": True,
        },
        {
            "slug": "collaborative_annotations",
            "name": "Collaborative annotations",
            "description": "Viewers and hosts can pin notes or callouts together.",
            "category": "Collaboration",
            "default_enabled": False,
        },
        {
            "slug": "layered_reactions",
            "name": "Layered reactions",
            "description": "Support stacked reactions and reaction heatmaps.",
            "category": "Engagement",
            "default_enabled": True,
        },
        {
            "slug": "monetized_pin",
            "name": "Monetized pin",
            "description": "Pin your product, link or CTA as a paid highlight.",
            "category": "Commerce",
            "default_enabled": False,
        },
        {
            "slug": "multi_host",
            "name": "Multi-host desk",
            "description": "Switch smoothly between hosts and moderators.",
            "category": "Production",
            "default_enabled": True,
        },
        {
            "slug": "audience_qna",
            "name": "Audience Q&A",
            "description": "Curate an expert Q&A queue for live broadcasts.",
            "category": "Engagement",
            "default_enabled": True,
        },
        {
            "slug": "live_translation",
            "name": "Live translation",
            "description": "Auto-translate captions for every viewer region.",
            "category": "Accessibility",
            "default_enabled": False,
        },
        {
            "slug": "custom_cta",
            "name": "Custom CTA",
            "description": "Embed programmable CTAs with tracking.",
            "category": "Commerce",
            "default_enabled": True,
        },
        {
            "slug": "private_replay",
            "name": "Private replay",
            "description": "Share replays only with approved viewers.",
            "category": "Privacy",
            "default_enabled": False,
        },
        {
            "slug": "broadcast_rankings",
            "name": "Broadcast rankings",
            "description": "Show your placement on a dynamic leaderboard.",
            "category": "Discovery",
            "default_enabled": True,
        },
        {
            "slug": "real_time_moderation",
            "name": "Real-time moderation",
            "description": "Auto-filter comments and highlight infractions.",
            "category": "Safety",
            "default_enabled": True,
        },
        {
            "slug": "adaptive_layout",
            "name": "Adaptive layout",
            "description": "Switch between cinematic, grid, and engagement layouts.",
            "category": "Production",
            "default_enabled": False,
        },
    ]
    for feature in features:
        BroadcastFeature.objects.update_or_create(
            slug=feature["slug"],
            defaults={
                "name": feature["name"],
                "description": feature["description"],
                "category": feature["category"],
                "default_enabled": feature["default_enabled"],
            },
        )


def remove_broadcast_features(apps, schema_editor):
    BroadcastFeature = apps.get_model("broadcasts", "BroadcastFeature")
    BroadcastFeature.objects.filter(slug__in=[
        "broadcast_scheduling",
        "live_ratings",
        "automated_highlights",
        "interactive_polling",
        "collaborative_annotations",
        "layered_reactions",
        "monetized_pin",
        "multi_host",
        "audience_qna",
        "live_translation",
        "custom_cta",
        "private_replay",
        "broadcast_rankings",
        "real_time_moderation",
        "adaptive_layout",
    ]).delete()


def seed_sample_videos(apps, schema_editor):
    BroadcastVideo = apps.get_model("broadcasts", "BroadcastVideo")
    User = apps.get_model("accounts", "User")
    creator = User.objects.first()
    now = django.utils.timezone.now()
    samples = [
        {
            "title": "Partner Studio | Behind the scenes",
            "description": "Short documentary-style peek at our partner studio workflow.",
            "video_url": "https://example.com/videos/partner_studio_short.mp4",
            "thumbnail_url": "https://example.com/thumbnails/partner_studio_short.jpg",
            "type": "short",
            "duration_seconds": 42,
        },
        {
            "title": "Broadcast masterclass: format secrets",
            "description": "45-minute walkthrough of formatting premium broadcasts.",
            "video_url": "https://example.com/videos/broadcast_masterclass.mp4",
            "thumbnail_url": "https://example.com/thumbnails/broadcast_masterclass.jpg",
            "type": "video",
            "duration_seconds": 2700,
        },
    ]
    for sample in samples:
        BroadcastVideo.objects.update_or_create(
            title=sample["title"],
            defaults={
                "description": sample["description"],
                "video_url": sample["video_url"],
                "thumbnail_url": sample["thumbnail_url"],
                "type": sample["type"],
                "duration_seconds": sample["duration_seconds"],
                "creator": creator,
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            },
        )


def unseed_sample_videos(apps, schema_editor):
    BroadcastVideo = apps.get_model("broadcasts", "BroadcastVideo")
    BroadcastVideo.objects.filter(
        title__in=[
            "Partner Studio | Behind the scenes",
            "Broadcast masterclass: format secrets",
        ]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("broadcasts", "0002_rename_broadcast_item_source_t_2c0f87_idx_broadcast_i_source__17f53d_idx_and_more"),
        ("channels", "0001_initial"),
        ("accounts", "0001_initial"),
        ("chat", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="BroadcastFeature",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.CharField(max_length=64, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("description", models.TextField(blank=True)),
                ("category", models.CharField(blank=True, max_length=64)),
                ("default_enabled", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "broadcast_feature",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="BroadcastFeatureFlag",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("enabled", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("broadcast_item", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="feature_flags", to="broadcasts.broadcastitem")),
                ("channel", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="feature_flags", to="channels.channel")),
                ("feature", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="flags", to="broadcasts.broadcastfeature")),
            ],
            options={
                "db_table": "broadcast_feature_flag",
                "unique_together": {("feature", "channel", "broadcast_item")},
            },
        ),
        migrations.AddIndex(
            model_name="broadcastfeatureflag",
            index=models.Index(fields=["feature", "channel"], name="broadcast_feature_flag_feature_channel_idx"),
        ),
        migrations.CreateModel(
            name="BroadcastVideo",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                ("video_url", models.URLField()),
                ("thumbnail_url", models.URLField(blank=True)),
                ("type", models.CharField(choices=[("short", "Short"), ("video", "Video")], db_index=True, default="video", max_length=16)),
                ("duration_seconds", models.PositiveIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True, db_index=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("channel", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="videos", to="channels.channel")),
                ("creator", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="videos", to="accounts.user")),
            ],
            options={
                "db_table": "broadcast_video",
                "ordering": ["-created_at"],
            },
        ),
        migrations.RunPython(create_broadcast_features, remove_broadcast_features),
        migrations.RunPython(seed_sample_videos, unseed_sample_videos),
    ]
