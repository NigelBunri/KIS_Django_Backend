from __future__ import annotations

import re

from django.db import migrations, models


def _normalize_digits(value: str | None) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _split_parts(phone_raw: str | None) -> tuple[str | None, str | None]:
    raw = str(phone_raw or "").strip()
    if not raw:
        return None, None

    digits = _normalize_digits(raw)
    if not digits:
        return None, None

    if raw.startswith("+") and len(digits) > 10:
        return f"+{digits[:-10]}", digits[-10:]
    return None, digits


def backfill_phone_parts(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    queryset = User.objects.exclude(phone__isnull=True).exclude(phone="")

    for user in queryset.iterator(chunk_size=500):
        code, number = _split_parts(getattr(user, "phone", None))
        changed = False
        if number and not getattr(user, "phone_number", None):
            user.phone_number = number
            changed = True
        if code and not getattr(user, "phone_country_code", None):
            user.phone_country_code = code
            changed = True
        if changed:
            user.save(update_fields=["phone_country_code", "phone_number"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0021_profile_language"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="phone_country_code",
            field=models.CharField(blank=True, db_index=True, max_length=12, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="phone_number",
            field=models.CharField(blank=True, db_index=True, max_length=32, null=True),
        ),
        migrations.RunPython(backfill_phone_parts, migrations.RunPython.noop),
    ]
