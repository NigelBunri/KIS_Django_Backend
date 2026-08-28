from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.notifications.services import notify_engagement
from .models import (
    BibleLessonComment,
    BibleCourseComment,
    BibleLessonReaction,
    BibleCourseReaction,
)


def _course_owner_id(course):
    # BibleCourse has no direct "instructor" field - it belongs to a
    # Partner org, and Partner.owner is the actual notifiable user.
    # partner is nullable, so this can legitimately resolve to nothing.
    return course.partner.owner_id if course and course.partner_id else None


@receiver(post_save, sender=BibleLessonComment)
def notify_lesson_commented(sender, instance, created, **kwargs):
    if not created:
        return
    lesson = instance.lesson
    notify_engagement(
        owner_user_id=_course_owner_id(lesson.course),
        actor_user=instance.user,
        notification_type="bible.lesson.commented",
        verb=f"commented on {lesson.title}",
        target_type="bible_lesson",
        target_id=lesson.id,
        target_title=instance.content[:200],
        dedup_key=f"bible.lesson.commented:{instance.id}",
    )


@receiver(post_save, sender=BibleCourseComment)
def notify_course_commented(sender, instance, created, **kwargs):
    if not created:
        return
    course = instance.course
    notify_engagement(
        owner_user_id=_course_owner_id(course),
        actor_user=instance.user,
        notification_type="bible.course.commented",
        verb=f"commented on {course.title}",
        target_type="bible_course",
        target_id=course.id,
        target_title=instance.content[:200],
        dedup_key=f"bible.course.commented:{instance.id}",
    )


@receiver(post_save, sender=BibleLessonReaction)
def notify_lesson_reacted(sender, instance, created, **kwargs):
    if not created:
        return
    lesson = instance.lesson
    notify_engagement(
        owner_user_id=_course_owner_id(lesson.course),
        actor_user=instance.user,
        notification_type="bible.lesson.reacted",
        verb=f"reacted to {lesson.title}",
        target_type="bible_lesson",
        target_id=lesson.id,
        target_title=lesson.title,
    )


@receiver(post_save, sender=BibleCourseReaction)
def notify_course_reacted(sender, instance, created, **kwargs):
    if not created:
        return
    course = instance.course
    notify_engagement(
        owner_user_id=_course_owner_id(course),
        actor_user=instance.user,
        notification_type="bible.course.reacted",
        verb=f"reacted to {course.title}",
        target_type="bible_course",
        target_id=course.id,
        target_title=course.title,
    )
