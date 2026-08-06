from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.core import models, tasks
from apps.notifications.models import Notification


class MedicationAdherenceReminderIdempotencyTests(TestCase):
    """Regression test: send_medication_adherence_reminders previously called
    create_notification with no dedup_key. If the Celery task were retried
    (or run twice) before the reminder's status flipped from PENDING to SENT
    — e.g. two overlapping beat schedules, or a retry after the notification
    was created but before `reminder.save()` committed — the same reminder
    would generate a second, duplicate push. dedup_key now anchors on the
    reminder's own id, which is stable and unique per scheduled reminder."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(phone="+237670005301", password="TestPass123!", country="CM")
        self.patient = models.PatientMasterRecord.objects.create(
            mrn="MRN-ADH-1",
            first_name="Pat",
            last_name="Ient",
            primary_contact={"user_id": str(self.user.id)},
        )
        self.reminder = models.MedicationAdherenceReminder.objects.create(
            patient=self.patient,
            scheduled_at=timezone.now() - timezone.timedelta(minutes=1),
            status=models.MedicationAdherenceReminder.STATUS_PENDING,
            channel="push",
        )

    def test_creates_a_dedup_keyed_notification(self):
        with patch("apps.notifications.tasks.process_notification_delivery.delay"):
            tasks.send_medication_adherence_reminders()

        notif = Notification.objects.filter(user_id=self.user.id, type="medication.adherence").first()
        self.assertIsNotNone(notif)
        self.assertEqual(notif.dedup_key, f"medication:adherence:{self.reminder.id}")

    def test_running_the_task_twice_for_the_same_reminder_does_not_duplicate_the_notification(self):
        # Simulates two overlapping task runs both observing the reminder as
        # still PENDING before either has committed its status update.
        with patch("apps.notifications.tasks.process_notification_delivery.delay"):
            tasks.send_medication_adherence_reminders()
            self.reminder.status = models.MedicationAdherenceReminder.STATUS_PENDING
            self.reminder.save(update_fields=["status"])
            tasks.send_medication_adherence_reminders()

        count = Notification.objects.filter(user_id=self.user.id, type="medication.adherence").count()
        self.assertEqual(count, 1)
