from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("bible", "0017_bibletranslationmetadata_license_review_status_and_more"),
        ("partners", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="KCANBook",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=300)),
                ("author", models.CharField(blank=True, max_length=300)),
                ("description", models.TextField(blank=True)),
                ("genre", models.CharField(
                    choices=[
                        ("theology", "Theology"),
                        ("devotional", "Devotional"),
                        ("biography", "Biography"),
                        ("ministry", "Ministry"),
                        ("prophecy", "Prophecy"),
                        ("prayer", "Prayer"),
                        ("family", "Family"),
                        ("leadership", "Leadership"),
                        ("missions", "Missions"),
                        ("other", "Other"),
                    ],
                    default="theology",
                    max_length=40,
                )),
                ("cover_image", models.ImageField(blank=True, null=True, upload_to="kcan/books/covers/")),
                ("pdf_file", models.FileField(blank=True, null=True, upload_to="kcan/books/pdfs/")),
                ("page_count", models.PositiveIntegerField(blank=True, null=True)),
                ("language", models.CharField(default="en", max_length=20)),
                ("status", models.CharField(
                    choices=[("draft", "Draft"), ("published", "Published"), ("archived", "Archived")],
                    default="draft",
                    max_length=20,
                )),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("partner", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="kcan_books",
                    to="partners.partner",
                )),
                ("created_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="kcan_books_created",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["sort_order", "-created_at"]},
        ),
        migrations.CreateModel(
            name="KCANMessageTopic",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=200)),
                ("slug", models.SlugField(max_length=200, unique=True)),
                ("description", models.TextField(blank=True)),
                ("cover_image", models.ImageField(blank=True, null=True, upload_to="kcan/topics/covers/")),
                ("status", models.CharField(
                    choices=[("draft", "Draft"), ("published", "Published"), ("archived", "Archived")],
                    default="draft",
                    max_length=20,
                )),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("partner", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="kcan_message_topics",
                    to="partners.partner",
                )),
            ],
            options={"ordering": ["sort_order", "name"]},
        ),
        migrations.CreateModel(
            name="KCANMinister",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=300)),
                ("title", models.CharField(blank=True, help_text="e.g. Pastor, Bishop, Dr.", max_length=200)),
                ("bio", models.TextField(blank=True)),
                ("photo", models.ImageField(blank=True, null=True, upload_to="kcan/ministers/photos/")),
                ("status", models.CharField(
                    choices=[("draft", "Draft"), ("published", "Published"), ("archived", "Archived")],
                    default="published",
                    max_length=20,
                )),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("partner", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="kcan_ministers",
                    to="partners.partner",
                )),
                ("topics", models.ManyToManyField(
                    blank=True,
                    related_name="ministers",
                    to="bible.kcanmessagetopic",
                )),
            ],
            options={"ordering": ["sort_order", "name"]},
        ),
        migrations.CreateModel(
            name="KCANMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=300)),
                ("description", models.TextField(blank=True)),
                ("video_type", models.CharField(
                    choices=[("youtube", "YouTube Embed"), ("direct", "Direct Video File")],
                    default="youtube",
                    max_length=20,
                )),
                ("youtube_url", models.URLField(blank=True, help_text="Full YouTube URL or embed URL")),
                ("youtube_video_id", models.CharField(blank=True, help_text="Auto-extracted YouTube video ID", max_length=20)),
                ("video_url", models.URLField(blank=True, help_text="Direct video file URL")),
                ("thumbnail", models.ImageField(blank=True, null=True, upload_to="kcan/messages/thumbnails/")),
                ("thumbnail_url", models.URLField(blank=True, help_text="External thumbnail URL")),
                ("duration_seconds", models.PositiveIntegerField(blank=True, null=True)),
                ("scripture_reference", models.CharField(blank=True, max_length=200)),
                ("tags", models.JSONField(blank=True, default=list)),
                ("status", models.CharField(
                    choices=[("draft", "Draft"), ("published", "Published"), ("archived", "Archived")],
                    default="draft",
                    max_length=20,
                )),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("view_count", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("partner", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="kcan_messages",
                    to="partners.partner",
                )),
                ("topic", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="messages",
                    to="bible.kcanmessagetopic",
                )),
                ("minister", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="messages",
                    to="bible.kcanminister",
                )),
                ("created_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="kcan_messages_created",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["sort_order", "-created_at"]},
        ),
    ]
