from __future__ import annotations

import re

import phonenumbers
from django.db import migrations


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _normalize_code(value: str | None) -> str | None:
    digits = _digits(value)
    return f"+{digits}" if digits else None


def _parse_phone(phone: str | None, country: str | None) -> tuple[str | None, str | None]:
    raw = str(phone or "").strip()
    if not raw:
        return None, None
    try:
        if raw.startswith("+"):
            parsed = phonenumbers.parse(raw, None)
        else:
            parsed = phonenumbers.parse(raw, (country or "CM").upper())
        if phonenumbers.is_possible_number(parsed):
            code = f"+{parsed.country_code}" if parsed.country_code else None
            number = str(parsed.national_number or "")
            return code, number or None
    except Exception:
        pass
    fallback = _digits(raw)
    return None, fallback or None


def normalize_phone_parts(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    queryset = User.objects.all()

    for user in queryset.iterator(chunk_size=500):
        parsed_code, parsed_number = _parse_phone(getattr(user, "phone", None), getattr(user, "country", None))
        existing_code = _normalize_code(getattr(user, "phone_country_code", None))
        existing_number = _digits(getattr(user, "phone_number", None)) or None

        final_code = parsed_code or existing_code
        final_number = parsed_number or existing_number

        if not final_number:
            continue

        final_phone = f"{final_code or ''}{final_number}"
        changed_fields: list[str] = []

        if (getattr(user, "phone_country_code", None) or None) != (final_code or None):
            user.phone_country_code = final_code or None
            changed_fields.append("phone_country_code")
        if (getattr(user, "phone_number", None) or None) != final_number:
            user.phone_number = final_number
            changed_fields.append("phone_number")
        current_phone = getattr(user, "phone", None) or None
        if current_phone != final_phone:
            phone_in_use = User.objects.filter(phone=final_phone).exclude(id=user.id).exists()
            if not phone_in_use:
                user.phone = final_phone
                changed_fields.append("phone")

        if changed_fields:
            user.save(update_fields=changed_fields)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0022_user_phone_parts"),
    ]

    operations = [
        migrations.RunPython(normalize_phone_parts, migrations.RunPython.noop),
    ]
