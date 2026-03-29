from django.db import migrations


def seed_courses(apps, schema_editor):
    Partner = apps.get_model("partners", "Partner")
    User = apps.get_model("accounts", "User")
    BibleCourse = apps.get_model("bible", "BibleCourse")
    BibleCourseModule = apps.get_model("bible", "BibleCourseModule")
    BibleLesson = apps.get_model("bible", "BibleLesson")

    owner = User.objects.first()
    if not owner:
        return

    partner = Partner.objects.filter(name__icontains="Christian Community").first()
    if not partner:
        partner = Partner.objects.create(
            name="Christian Community (CC)",
            slug="christian-community-cc",
            description="Default CC partner for Bible courses.",
            owner=owner,
        )

    bible_courses = [
        {
            "title": "Foundations of Faith",
            "subtitle": "Building a solid Christian foundation",
            "description": "A 4-week journey through core doctrines and spiritual habits.",
        },
        {
            "title": "Gospel of John Deep Dive",
            "subtitle": "Encountering Jesus in Scripture",
            "description": "Verse-by-verse study with reflection prompts and prayer.",
        },
        {
            "title": "Prayer & Worship Practices",
            "subtitle": "Growing in intimacy with God",
            "description": "Daily prayer rhythms and worship-centered living.",
        },
        {
            "title": "Psalms for Every Season",
            "subtitle": "Lament, praise, and hope",
            "description": "Learn to pray the Psalms in every circumstance.",
        },
    ]

    normal_courses = [
        {
            "title": "Leadership for Ministry Teams",
            "subtitle": "Serving with clarity and compassion",
            "description": "Leadership modules for CC community leaders.",
        },
        {
            "title": "Community Care & Counseling",
            "subtitle": "Practical support for members",
            "description": "Tools for pastoral care and follow-up.",
        },
        {
            "title": "Youth Mentorship Toolkit",
            "subtitle": "Guiding the next generation",
            "description": "Mentoring practices and discipleship paths.",
        },
        {
            "title": "Creative Ministry Essentials",
            "subtitle": "Media, music, and storytelling",
            "description": "Equipping creative teams with workflows.",
        },
    ]

    def create_course(data, is_bible_course, is_free):
        course = BibleCourse.objects.create(
            partner=partner,
            title=data["title"],
            subtitle=data.get("subtitle", ""),
            description=data.get("description", ""),
            level="intermediate",
            duration_minutes=240,
            is_bible_course=is_bible_course,
            is_free=is_free,
            published=True,
        )
        for module_index in range(1, 4):
            module = BibleCourseModule.objects.create(
                course=course,
                title=f"Module {module_index}",
                summary=f"Module {module_index} highlights and guided reflections.",
                order=module_index,
            )
            for lesson_index in range(1, 4):
                order = (module_index - 1) * 3 + lesson_index
                BibleLesson.objects.create(
                    course=course,
                    module=module,
                    title=f"Lesson {order}: {course.title}",
                    summary="Short lesson summary with prayer focus.",
                    content="This lesson includes scripture reading, key points, and a prayer prompt.",
                    order=order,
                    duration_minutes=12,
                    is_free=is_free,
                )

    for course_data in bible_courses:
        create_course(course_data, is_bible_course=True, is_free=True)

    for course_data in normal_courses:
        create_course(course_data, is_bible_course=False, is_free=False)


def reverse_seed(apps, schema_editor):
    apps.get_model("bible", "BibleLesson").objects.all().delete()
    apps.get_model("bible", "BibleCourseModule").objects.all().delete()
    apps.get_model("bible", "BibleCourse").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("bible", "0003_courses"),
    ]

    operations = [
        migrations.RunPython(seed_courses, reverse_seed),
    ]
