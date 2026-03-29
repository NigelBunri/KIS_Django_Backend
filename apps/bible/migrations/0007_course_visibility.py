from django.db import migrations


def set_course_visibility(apps, schema_editor):
    BibleCourse = apps.get_model("bible", "BibleCourse")
    BibleCourse.objects.filter(is_bible_course=True).update(is_public=True)


def reverse_visibility(apps, schema_editor):
    BibleCourse = apps.get_model("bible", "BibleCourse")
    BibleCourse.objects.filter(is_bible_course=True).update(is_public=False)


class Migration(migrations.Migration):
    dependencies = [
        ("bible", "0006_course_social"),
    ]

    operations = [
        migrations.RunPython(set_course_visibility, reverse_visibility),
    ]
