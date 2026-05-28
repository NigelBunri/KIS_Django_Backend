from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("broadcasts", "0043_user_playlists_cameras_viewer_count"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LiveChatMessage",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("stream", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="chat_messages", to="broadcasts.channellivestream")),
                ("user", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("display_name", models.CharField(blank=True, default="", max_length=150)),
                ("avatar_url", models.URLField(blank=True, default="", max_length=1024)),
                ("message", models.TextField(max_length=1000)),
                ("is_deleted", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "live_chat_message"},
        ),
        migrations.AddIndex(
            model_name="livechatmessage",
            index=models.Index(fields=["stream", "created_at"], name="live_chat_stream_time_idx"),
        ),
        migrations.CreateModel(
            name="ChannelMembershipTier",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("channel", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="membership_tiers", to="broadcasts.broadcastchannel")),
                ("title", models.CharField(max_length=100)),
                ("description", models.TextField(blank=True, default="")),
                ("price_cents", models.PositiveIntegerField(default=0)),
                ("currency", models.CharField(default="USD", max_length=3)),
                ("perks", models.JSONField(blank=True, default=list)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "channel_membership_tier", "ordering": ["sort_order", "price_cents"]},
        ),
        migrations.AddIndex(
            model_name="channelmembershiptier",
            index=models.Index(fields=["channel", "is_active"], name="membership_tier_channel_idx"),
        ),
        migrations.CreateModel(
            name="ChannelMembership",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="channel_memberships", to=settings.AUTH_USER_MODEL)),
                ("tier", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="memberships", to="broadcasts.channelmembershiptier")),
                ("status", models.CharField(choices=[("active", "Active"), ("cancelled", "Cancelled"), ("expired", "Expired")], default="active", max_length=16)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("payment_reference", models.CharField(blank=True, default="", max_length=256)),
                ("metadata", models.JSONField(blank=True, default=dict)),
            ],
            options={"db_table": "channel_membership"},
        ),
        migrations.AlterUniqueTogether(
            name="channelmembership",
            unique_together={("user", "tier")},
        ),
        migrations.AddIndex(
            model_name="channelmembership",
            index=models.Index(fields=["user", "status"], name="membership_user_status_idx"),
        ),
        migrations.AddIndex(
            model_name="channelmembership",
            index=models.Index(fields=["tier", "status"], name="membership_tier_status_idx"),
        ),
    ]
