from celery import shared_task
from django.utils import timezone
from django.db.models import Q

from . import models
from apps.notifications import services as notification_services


@shared_task(bind=True)
def send_telemedicine_session_reminders(self):
    """
    Scan pending telemedicine sessions with appointments scheduled within the next 24 hours
    and deliver reminder notifications to the assigned clinician.
    """
    now = timezone.now()
    window_end = now + timezone.timedelta(hours=24)
    sessions = (
        models.TelemedicineSession.objects.filter(
            Q(status=models.TelemedicineSession.STATUS_PENDING),
            reminder_sent=False,
            appointment__scheduled_at__gte=now,
            appointment__scheduled_at__lte=window_end,
        )
        .select_related("appointment", "clinician", "patient")
        .order_by("appointment__scheduled_at")
    )

    reminders_sent = 0
    for session in sessions:
        if not session.clinician_id or not session.appointment:
            continue
        scheduled_at = session.appointment.scheduled_at or now
        scheduled_label = scheduled_at.strftime("%Y-%m-%d %H:%M %Z")
        notification_services.create_notification(
            user_id=session.clinician_id,
            type="telemedicine.session.reminder",
            title="Upcoming telemedicine session",
            body=(
                f"Reminder: session for {session.patient or 'your patient'} "
                f"is happening at {scheduled_label}."
            ),
            context={
                "session_id": str(session.id),
                "scheduled_at": scheduled_at.isoformat(),
                "patient_id": str(session.patient_id) if session.patient_id else None,
            },
            dedup_key=f"telemedicine:session:{session.id}:reminder",
        )
        session.reminder_sent = True
        session.reminder_sent_at = now
        session.save(update_fields=["reminder_sent", "reminder_sent_at"])
        reminders_sent += 1
    return {"reminders_sent": reminders_sent}


@shared_task
def send_credential_expiration_alerts():
    today = timezone.now().date()
    window_end = today + timezone.timedelta(days=30)
    credentials = (
        models.CredentialVerification.objects.filter(
            expires_at__isnull=False,
            expires_at__lte=window_end,
            status__in=[models.CredentialVerification.STATUS_PENDING, models.CredentialVerification.STATUS_VERIFIED],
        )
        .select_related("staff_profile", "staff_profile__user")
        .order_by("expires_at")
    )
    alerts_sent = 0
    for credential in credentials:
        user = getattr(credential.staff_profile, "user", None)
        if not user or not user.id:
            continue
        notification_services.create_notification(
            user_id=user.id,
            type="compliance.credential.expiring",
            title="Credential expiring soon",
            body=(
                f"{credential.credential_type} ({credential.license_number}) will expire on "
                f"{credential.expires_at.isoformat()}."
            ),
            context={
                "credential_id": str(credential.id),
                "expires_at": credential.expires_at.isoformat(),
            },
            dedup_key=f"compliance:credential:{credential.id}:expiring",
        )
        alerts_sent += 1
    return {"alerts_sent": alerts_sent}


@shared_task(bind=True)
def send_medication_adherence_reminders(self):
    now = timezone.now()
    reminders = (
        models.MedicationAdherenceReminder.objects.filter(
            status=models.MedicationAdherenceReminder.STATUS_PENDING,
            scheduled_at__lte=now,
        )
        .select_related("patient", "medication_order")
        .order_by("scheduled_at")
    )

    sent = 0
    for reminder in reminders:
        patient_label = str(reminder.patient)
        medication_label = reminder.medication_order.drug_name if reminder.medication_order else "medication"
        notification_services.create_notification(
            user_id=reminder.patient.primary_contact.get("user_id") if reminder.patient.primary_contact else None,
            type="medication.adherence",
            title="Medication reminder",
            body=f"Reminder for {medication_label} scheduled at {reminder.scheduled_at.strftime('%Y-%m-%d %H:%M')} (channel: {reminder.channel}).",
            context={
                "reminder_id": str(reminder.id),
                "patient_id": str(reminder.patient_id),
                "medication_order_id": str(reminder.medication_order_id) if reminder.medication_order_id else None,
            },
            dedup_key=f"medication:adherence:{reminder.id}",
        )
        reminder.status = models.MedicationAdherenceReminder.STATUS_SENT
        reminder.save(update_fields=["status"])
        sent += 1

    return {"reminders_sent": sent}
