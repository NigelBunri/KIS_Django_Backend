from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import uuid


def migrate_preferences_languages(apps, schema_editor):
    ProfilePreferences = apps.get_model("accounts", "ProfilePreferences")
    ProfileLanguage = apps.get_model("accounts", "ProfileLanguage")

    pending = []
    seen = set()
    for pref in ProfilePreferences.objects.all():
        raw_languages = pref.languages or []
        if not isinstance(raw_languages, list):
            continue
        for value in raw_languages:
            label = str(value or "").strip()
            if not label:
                continue
            key = (pref.user_id, label.lower())
            if key in seen:
                continue
            seen.add(key)
            pending.append(ProfileLanguage(user_id=pref.user_id, name=label))

    if pending:
        ProfileLanguage.objects.bulk_create(pending, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0020_privacy_everywhere"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProfileLanguage",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(db_index=True, default=False)),
                ("name", models.CharField(max_length=120)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="profile_languages",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "unique_together": {("user", "name")},
            },
        ),
        migrations.AddIndex(
            model_name="profilelanguage",
            index=models.Index(fields=["user", "name"], name="accounts_pr_user_id_542574_idx"),
        ),
        migrations.RunPython(migrate_preferences_languages, migrations.RunPython.noop),
    ]
