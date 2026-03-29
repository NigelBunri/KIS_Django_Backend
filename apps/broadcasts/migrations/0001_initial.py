import uuid
from datetime import timedelta

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def _default_expires_at():
    return django.utils.timezone.now() + timedelta(days=10)


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("accounts", "0001_initial"),
        ("chat", "0001_initial"),
        ("channels", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="BroadcastItem",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("source_type", models.CharField(choices=[("community_post", "Community Post"), ("partner_post", "Partner Post"), ("channel_message", "Channel Message"), ("market_product", "Market Product")], db_index=True, max_length=32)),
                ("source_id", models.CharField(db_index=True, max_length=128)),
                ("conversation_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("broadcasted_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField(db_index=True, default=_default_expires_at)),
                ("is_deleted", models.BooleanField(db_index=True, default=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("broadcasted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="broadcasts", to="accounts.user")),
                ("channel", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="broadcast_items", to="channels.channel")),
                ("comment_conversation", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="broadcast_comment_threads", to="chat.conversation")),
            ],
            options={
                "db_table": "broadcast_item",
                "unique_together": {("source_type", "source_id")},
            },
        ),
        migrations.CreateModel(
            name="BroadcastReaction",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("emoji", models.CharField(default="❤️", max_length=16)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("broadcast_item", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reactions", to="broadcasts.broadcastitem")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="broadcast_reactions", to="accounts.user")),
            ],
            options={
                "db_table": "broadcast_reaction",
                "unique_together": {("broadcast_item", "user")},
            },
        ),
        migrations.AddIndex(
            model_name="broadcastitem",
            index=models.Index(fields=["source_type", "source_id"], name="broadcast_item_source_t_2c0f87_idx"),
        ),
        migrations.AddIndex(
            model_name="broadcastitem",
            index=models.Index(fields=["expires_at"], name="broadcast_item_expires_9f4a2b_idx"),
        ),
        migrations.AddIndex(
            model_name="broadcastreaction",
            index=models.Index(fields=["broadcast_item", "user"], name="broadcast_react_item_us_4a3b9d_idx"),
        ),
    ]
