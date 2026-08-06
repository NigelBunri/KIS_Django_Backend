import uuid
from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase

from . import models, services


class DedupKeyUniqueConstraintTests(TestCase):
    """The dedup_key field was only db_index=True, not a real uniqueness
    constraint — the create_notification dedupe check was a plain
    "SELECT then INSERT" with no protection against two callers racing the
    same window (e.g. two overlapping webhook redeliveries hitting different
    app workers). The DB is now the final arbiter via a partial unique
    constraint on (user_id, dedup_key) for non-deleted rows."""

    def test_db_rejects_a_second_active_notification_with_the_same_user_and_dedup_key(self):
        uid = uuid.uuid4()
        models.Notification.objects.create(user_id=uid, type="EVENT_ALERT", title="a", body="a", dedup_key="dup-1")
        with self.assertRaises(IntegrityError):
            models.Notification.objects.create(user_id=uid, type="EVENT_ALERT", title="b", body="b", dedup_key="dup-1")

    def test_soft_deleted_rows_do_not_block_a_new_notification_with_the_same_dedup_key(self):
        uid = uuid.uuid4()
        first = models.Notification.objects.create(user_id=uid, type="EVENT_ALERT", title="a", body="a", dedup_key="dup-2")
        first.is_deleted = True
        first.save(update_fields=["is_deleted"])
        # Must not raise — the constraint only applies to is_deleted=False rows.
        second = models.Notification.objects.create(user_id=uid, type="EVENT_ALERT", title="b", body="b", dedup_key="dup-2")
        self.assertNotEqual(first.id, second.id)

    def test_different_users_can_share_the_same_dedup_key(self):
        models.Notification.objects.create(user_id=uuid.uuid4(), type="EVENT_ALERT", title="a", body="a", dedup_key="shared")
        # Must not raise — uniqueness is scoped per user.
        models.Notification.objects.create(user_id=uuid.uuid4(), type="EVENT_ALERT", title="b", body="b", dedup_key="shared")


class CreateNotificationRaceConditionTests(TestCase):
    """Proves create_notification survives losing a genuine TOCTOU race: two
    concurrent calls both pass the "does a notification with this dedup_key
    already exist?" check before either commits its insert. Simulated here by
    patching the existence check to report nothing found (the state a second,
    truly concurrent caller would observe) while a row with that dedup_key
    already exists — the second caller's INSERT then hits the unique
    constraint, and create_notification must catch that and hand back the
    winner's row instead of raising or creating a duplicate."""

    def test_losing_the_insert_race_reuses_the_winning_row_instead_of_raising(self):
        uid = uuid.uuid4()
        with patch("apps.notifications.tasks.process_notification_delivery.delay"):
            winner = services.create_notification(
                user_id=uid, type="EVENT_ALERT", title="Hello", body="World", dedup_key="race-1",
            )

            original_filter = models.Notification.objects.filter

            def fake_filter(*args, **kwargs):
                qs = original_filter(*args, **kwargs)
                if kwargs.get("dedup_key") == "race-1":
                    return qs.none()
                return qs

            with patch.object(models.Notification.objects, "filter", side_effect=fake_filter):
                loser_result = services.create_notification(
                    user_id=uid, type="EVENT_ALERT", title="Hello again", body="World again", dedup_key="race-1",
                )

        self.assertEqual(str(loser_result.id), str(winner.id))
        self.assertEqual(
            models.Notification.objects.filter(user_id=uid, dedup_key="race-1", is_deleted=False).count(), 1,
        )
