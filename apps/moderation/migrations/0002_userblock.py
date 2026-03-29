from django.conf import settings
from django.db import migrations, models
from django.utils import timezone
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("Moderation", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserBlock",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(default=timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("reason", models.TextField(blank=True)),
                ("blocked", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="blocked_by", to=settings.AUTH_USER_MODEL)),
                ("blocker", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="blocks_made", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "unique_together": {("blocker", "blocked")},
            },
        ),
        migrations.AddIndex(
            model_name="userblock",
            index=models.Index(fields=["blocker", "blocked"], name="moderation_blocker_blocked_idx"),
        ),
        migrations.AddIndex(
            model_name="userblock",
            index=models.Index(fields=["blocker", "created_at"], name="moderation_blocker_created_idx"),
        ),
    ]
