from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("health_ops", "0012_alter_institutionenginemanageditem_image_url"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="videoconsultationsession",
            name="appointment_booking",
        ),
        migrations.RemoveField(
            model_name="securemessagingsession",
            name="appointment_booking",
        ),
        migrations.RemoveField(
            model_name="clinicalenginesession",
            name="appointment_booking",
        ),
        migrations.RemoveField(
            model_name="admissionbedsession",
            name="appointment_booking",
        ),
        migrations.RemoveField(
            model_name="emergencydispatchsession",
            name="appointment_booking",
        ),
        migrations.RemoveField(
            model_name="pharmacyfulfillmentsession",
            name="appointment_booking",
        ),
        migrations.RemoveField(
            model_name="paymentbillingsession",
            name="appointment_booking",
        ),
        migrations.RemoveField(
            model_name="homelogisticssession",
            name="appointment_booking",
        ),
        migrations.RemoveField(
            model_name="wellnessprogramsession",
            name="appointment_booking",
        ),
        migrations.RemoveField(
            model_name="notificationremindersession",
            name="appointment_booking",
        ),
        migrations.DeleteModel(
            name="AppointmentBooking",
        ),
    ]
