from django.db import migrations


def backfill_legacy_broadcast_institution(apps, schema_editor):
    """Best-effort, safe backfill of the new legacy_broadcast_institution FK
    for HealthInstitution rows created before this field existed (they only
    carry settings["legacy_institution_id"], a bare string with no real
    relational integrity). institution_uid is only guaranteed unique per
    BroadcastHealthProfile, not globally, so this only sets the FK when the
    uid resolves to exactly one BroadcastHealthInstitution row across the
    whole table — ambiguous matches are left untouched (NULL) rather than
    guessed at. This is intentionally conservative: a missed backfill is
    just a no-op (the lookup helper still falls back to the settings key),
    an incorrect one would silently misattribute institution identity."""
    HealthInstitution = apps.get_model("health_ops", "HealthInstitution")
    BroadcastHealthInstitution = apps.get_model("broadcasts", "BroadcastHealthInstitution")

    candidates = HealthInstitution.objects.filter(
        legacy_broadcast_institution__isnull=True,
    ).exclude(settings__legacy_institution_id__isnull=True)

    for institution in candidates.iterator():
        legacy_id = (institution.settings or {}).get("legacy_institution_id")
        if not legacy_id:
            continue
        matches = list(BroadcastHealthInstitution.objects.filter(institution_uid=legacy_id)[:2])
        if len(matches) == 1:
            institution.legacy_broadcast_institution_id = matches[0].id
            institution.save(update_fields=["legacy_broadcast_institution"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("broadcasts", "0064_education_program_class_details_and_broadcast"),
        ("health_ops", "0025_healthinstitution_legacy_broadcast_institution"),
    ]

    operations = [
        migrations.RunPython(backfill_legacy_broadcast_institution, noop_reverse),
    ]
