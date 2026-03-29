from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("bible", "0012_course_community_live"),
        ("partners", "0022_merge_0021_org_profile_and_setting_rename"),
    ]

    operations = [
        migrations.CreateModel(
            name="BibleCourseBundle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True)),
                ("price_amount", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("price_currency", models.CharField(default="USD", max_length=10)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("partner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="course_bundles", to="partners.partner")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="BibleCourseBundleItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("order", models.PositiveIntegerField(default=1)),
                ("bundle", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="bible.biblecoursebundle")),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="bundle_items", to="bible.biblecourse")),
            ],
            options={"ordering": ["order"], "unique_together": {("bundle", "course")}},
        ),
        migrations.CreateModel(
            name="BibleCourseCoupon",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=32, unique=True)),
                ("percent_off", models.PositiveIntegerField(default=0)),
                ("amount_off", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("max_redemptions", models.PositiveIntegerField(default=0)),
                ("redeemed_count", models.PositiveIntegerField(default=0)),
                ("valid_until", models.DateTimeField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("bundle", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="coupons", to="bible.biblecoursebundle")),
                ("course", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="coupons", to="bible.biblecourse")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="BibleEnterpriseSeatPool",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("seats_total", models.PositiveIntegerField(default=0)),
                ("seats_used", models.PositiveIntegerField(default=0)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("course", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="seat_pools", to="bible.biblecourse")),
                ("partner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="seat_pools", to="partners.partner")),
            ],
        ),
        migrations.CreateModel(
            name="BibleRefundRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reason", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("requested", "Requested"), ("approved", "Approved"), ("rejected", "Rejected")], default="requested", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("enrollment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="refund_requests", to="bible.biblecourseenrollment")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="BibleCourseCredential",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("badge_name", models.CharField(default="Course Certificate", max_length=200)),
                ("share_token", models.CharField(max_length=64, unique=True)),
                ("issued_at", models.DateTimeField(auto_now_add=True)),
                ("enrollment", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="credential", to="bible.biblecourseenrollment")),
            ],
        ),
    ]
