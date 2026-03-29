import uuid
from datetime import timedelta

import django.utils.timezone
from django.db import migrations, models


def seed_sample_lessons(apps, schema_editor):
    BroadcastItem = apps.get_model("broadcasts", "BroadcastItem")
    BroadcastLesson = apps.get_model("broadcasts", "BroadcastLesson")
    partner = apps.get_model("partners", "Partner").objects.first()
    community = apps.get_model("communities", "Community").objects.first()

    if not BroadcastItem.objects.exists():
        return

    now = django.utils.timezone.now()
    expires = now + timedelta(days=10)

    for item in BroadcastItem.objects.filter(is_deleted=False)[:2]:
        BroadcastLesson.objects.update_or_create(
            broadcast_item=item,
            defaults={
                "title": f"Lesson for {item.get_source_type_display()}",
                "summary": "An immersive learning session accentuated with premium resources.",
                "lesson_url": "https://example.com/lesson",
                "lesson_type": "global",
                "public_info": {"tagline": "New broadcast lesson"},
                "starts_at": now,
                "ends_at": expires,
                "price_cents": 0,
                "currency": "USD",
                "is_public": True,
                "partner": partner if partner else None,
                "community": community if community else None,
            },
        )


def remove_sample_lessons(apps, schema_editor):
    BroadcastLesson = apps.get_model("broadcasts", "BroadcastLesson")
    BroadcastLesson.objects.filter(title__icontains="Lesson").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("broadcasts", "0003_broadcast_features_and_videos"),
        ("broadcasts", "0004_rename_broadcast_feature_flag_feature_channel_idx_broadcast_f_feature_b65c9e_idx"),
        ("partners", "0024_lesson_access"),
        ("communities", "0009_lesson_access"),
    ]

    operations = [
        migrations.CreateModel(
            name="BroadcastLesson",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=255)),
                ("summary", models.TextField(blank=True)),
                ("lesson_url", models.URLField(blank=True)),
                ("lesson_type", models.CharField(choices=[("partner", "Partner"), ("community", "Community"), ("global", "Global")], db_index=True, default="global", max_length=16)),
                ("public_info", models.JSONField(blank=True, default=dict)),
                ("starts_at", models.DateTimeField(blank=True, null=True)),
                ("ends_at", models.DateTimeField(blank=True, null=True)),
                ("price_cents", models.BigIntegerField(default=0)),
                ("currency", models.CharField(default="USD", max_length=10)),
                ("is_public", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("broadcast_item", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="lesson", to="broadcasts.broadcastitem")),
                ("community", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="lessons", to="communities.community")),
                ("partner", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="lessons", to="partners.partner")),
            ],
            options={
                "db_table": "broadcast_lesson",
            },
        ),
        migrations.AddIndex(
            model_name="broadcastlesson",
            index=models.Index(fields=["lesson_type"], name="broadcast_lesson_les_type_idx"),
        ),
        migrations.AddIndex(
            model_name="broadcastlesson",
            index=models.Index(fields=["partner"], name="broadcast_lesson_part_idx"),
        ),
        migrations.AddIndex(
            model_name="broadcastlesson",
            index=models.Index(fields=["community"], name="broadcast_lesson_comm_idx"),
        ),
        migrations.CreateModel(
            name="LessonEnrollment",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("status", models.CharField(choices=[("enrolled", "Enrolled"), ("completed", "Completed"), ("cancelled", "Cancelled")], default="enrolled", max_length=16)),
                ("enrolled_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("partner_membership_id", models.BigIntegerField(blank=True, null=True)),
                ("community_membership_id", models.BigIntegerField(blank=True, null=True)),
                ("lesson", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="enrollments", to="broadcasts.broadcastlesson")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lesson_enrollments", to="accounts.user")),
            ],
            options={
                "db_table": "lesson_enrollment",
                "unique_together": {("lesson", "user")},
            },
        ),
        migrations.AddIndex(
            model_name="lessonenrollment",
            index=models.Index(fields=["user", "status"], name="lesson_enrollment_user_stat_idx"),
        ),
        migrations.AddIndex(
            model_name="lessonenrollment",
            index=models.Index(fields=["lesson", "user"], name="lesson_enrollment_les_user_idx"),
        ),
        migrations.RunPython(seed_sample_lessons, remove_sample_lessons),
    ]
