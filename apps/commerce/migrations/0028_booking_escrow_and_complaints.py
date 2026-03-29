from __future__ import annotations

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("commerce", "0027_allow_multiple_attendees"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="servicebooking",
            name="payer_satisfied_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="servicebooking",
            name="provider_completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="servicebooking",
            name="satisfaction_deadline",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="ServiceBookingEscrow",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("amount_cents", models.BigIntegerField(default=0)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("awaiting_satisfaction", "Awaiting satisfaction"),
                            ("released", "Released"),
                            ("refunded", "Refunded"),
                            ("dispute", "Dispute"),
                        ],
                        default="pending",
                        max_length=32,
                    ),
                ),
                (
                    "payment_reference",
                    models.CharField(blank=True, default="", max_length=128),
                ),
                ("locked_at", models.DateTimeField(auto_now_add=True)),
                ("released_at", models.DateTimeField(blank=True, null=True)),
                ("refunded_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("note", models.TextField(blank=True)),
                (
                    "booking",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="escrow",
                        to="commerce.servicebooking",
                    ),
                ),
                (
                    "payer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="service_escrows",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "provider",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="service_payouts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "released_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "refunded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["status"], name="commerce_se_status_idx"),
                    models.Index(fields=["locked_at"], name="commerce_se_locked_idx"),
                    models.Index(fields=["released_at"], name="commerce_se_released_idx"),
                    models.Index(fields=["refunded_at"], name="commerce_se_refunded_idx"),
                ]
            },
        ),
        migrations.CreateModel(
            name="ServiceBookingComplaint",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "Open"),
                            ("investigating", "Investigating"),
                            ("resolved", "Resolved"),
                        ],
                        default="open",
                        max_length=32,
                    ),
                ),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("none", "None"),
                            ("release", "Release payment"),
                            ("refund", "Refund payment"),
                        ],
                        default="none",
                        max_length=32,
                    ),
                ),
                (
                    "transaction_reference",
                    models.CharField(blank=True, max_length=128),
                ),
                ("receipt_url", models.URLField(blank=True, max_length=512)),
                ("personal_statement", models.TextField(blank=True)),
                ("reason", models.TextField(blank=True)),
                ("service_name", models.CharField(blank=True, max_length=255)),
                ("shop_name", models.CharField(blank=True, max_length=255)),
                ("provider_info", models.JSONField(blank=True, default=dict)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("resolution_note", models.TextField(blank=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                (
                    "booking",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="complaints",
                        to="commerce.servicebooking",
                    ),
                ),
                (
                    "escrow",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="complaints",
                        to="commerce.servicebookingescrow",
                    ),
                ),
                (
                    "submitted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="service_booking_complaints",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "provider",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="service_provider_complaints",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "resolved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="resolved_service_booking_complaints",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["status"], name="commerce_sc_status_idx"),
                    models.Index(fields=["action"], name="commerce_sc_action_idx"),
                    models.Index(fields=["created_at"], name="commerce_sc_created_idx"),
                ]
            },
        ),
    ]
