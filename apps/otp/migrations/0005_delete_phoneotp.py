# Email/SMS OTP authentication removed — KIS Google Auth migration (2026-10).
# Reversible: Django regenerates the CreateModel operation automatically if
# this migration is ever unapplied (`migrate otp 0004`).
#
# Deployment note: apply this migration in each environment as part of the
# rollout (see docs/KIS_GOOGLE_AUTH_DEPLOYMENT_RUNBOOK.md). It has NOT been
# run against production as part of this change.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('otp', '0004_alter_phoneotp_purpose'),
    ]

    operations = [
        migrations.DeleteModel(
            name='PhoneOTP',
        ),
    ]
