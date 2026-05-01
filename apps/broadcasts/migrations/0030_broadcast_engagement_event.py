from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("broadcasts", "0029_educationinstitutionassessment_cover_image_url_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="BroadcastEngagementEvent",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("impression", "Impression"),
                            ("view", "View"),
                            ("share", "Share"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                ("platform", models.CharField(blank=True, default="", max_length=64)),
                ("window_key", models.CharField(db_index=True, max_length=128)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                (
                    "broadcast_item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="engagement_events",
                        to="broadcasts.broadcastitem",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="broadcast_engagement_events",
                        to="accounts.user",
                    ),
                ),
            ],
            options={
                "db_table": "broadcast_engagement_event",
                "unique_together": {("broadcast_item", "user", "event_type", "window_key")},
            },
        ),
        migrations.AddIndex(
            model_name="broadcastengagementevent",
            index=models.Index(
                fields=["broadcast_item", "event_type", "created_at"],
                name="broadcast_e_broadca_b8c59e_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="broadcastengagementevent",
            index=models.Index(
                fields=["user", "event_type", "created_at"],
                name="broadcast_e_user_id_59ae71_idx",
            ),
        ),
    ]
