from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0002_rename_billing_ca_user_9ee7d2_idx_billing_cre_user_id_70b1bb_idx_and_more"),
        ("core", "0011_merge_20260209_1403"),
        ("accounts", "0002_profile_privacy_articles_tiers"),
    ]

    operations = [
        migrations.CreateModel(
            name="BillingReconciliation",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False, editable=False, default=uuid.uuid4)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False, db_index=True)),
                ("insurance_provider", models.CharField(blank=True, max_length=128)),
                ("amount_cents", models.BigIntegerField(default=0)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("reconciled", "Reconciled"), ("requires_action", "Requires action")], default="pending", max_length=32)),
                ("reconciled_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("note", models.TextField(blank=True)),
                (
                    "organization",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="billing_reconciliations",
                        to="core.healthcareorganization",
                    ),
                ),
                (
                    "transaction",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="billing_reconciliations",
                        to="billing.wallettransaction",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="InsuranceClaim",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False, editable=False, default=uuid.uuid4)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False, db_index=True)),
                ("insurance_provider", models.CharField(blank=True, max_length=128)),
                ("service_code", models.CharField(blank=True, max_length=64)),
                ("claim_reference", models.CharField(blank=True, max_length=128)),
                ("amount_cents", models.BigIntegerField(default=0)),
                ("paid_amount_cents", models.BigIntegerField(default=0)),
                ("status", models.CharField(choices=[("submitted", "Submitted"), ("in_review", "In review"), ("approved", "Approved"), ("denied", "Denied"), ("paid", "Paid")], default="submitted", max_length=32)),
                ("submitted_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("notes", models.TextField(blank=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "organization",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="insurance_claims",
                        to="core.healthcareorganization",
                    ),
                ),
                (
                    "patient",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="insurance_claims",
                        to="core.patientmasterrecord",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="PaymentDispute",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False, editable=False, default=uuid.uuid4)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False, db_index=True)),
                ("dispute_reason", models.TextField(blank=True)),
                ("resolution", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("open", "Open"), ("investigating", "Investigating"), ("resolved", "Resolved")], default="open", max_length=32)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "claim",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="disputes",
                        to="billing.insuranceclaim",
                    ),
                ),
                (
                    "reported_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="payment_disputes",
                        to="accounts.user",
                    ),
                ),
                (
                    "wallet_transaction",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="disputes",
                        to="billing.wallettransaction",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="billingreconciliation",
            index=models.Index(fields=["status"], name="billing_br_status_idx"),
        ),
        migrations.AddIndex(
            model_name="billingreconciliation",
            index=models.Index(fields=["organization"], name="billing_br_org_idx"),
        ),
        migrations.AddIndex(
            model_name="insuranceclaim",
            index=models.Index(fields=["status"], name="billing_ic_status_idx"),
        ),
        migrations.AddIndex(
            model_name="insuranceclaim",
            index=models.Index(fields=["insurance_provider"], name="billing_ic_provider_idx"),
        ),
        migrations.AddIndex(
            model_name="paymentdispute",
            index=models.Index(fields=["status"], name="billing_pd_status_idx"),
        ),
    ]
