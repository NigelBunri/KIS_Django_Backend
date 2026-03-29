from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):

    dependencies = [
        ("partners", "0003_rename_partner_po_partner_8e9d24_idx_partner_pos_partner_f8b11b_idx"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="partnerpost",
            name="is_broadcast",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="PartnerPostComment",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("text", models.TextField()),
                ("is_deleted", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(default=timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("author", models.ForeignKey(on_delete=models.deletion.PROTECT, related_name="partner_post_comments", to=settings.AUTH_USER_MODEL)),
                ("post", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="comments", to="partners.partnerpost")),
            ],
            options={"db_table": "partner_post_comment"},
        ),
        migrations.CreateModel(
            name="PartnerPostReaction",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("emoji", models.CharField(max_length=32)),
                ("created_at", models.DateTimeField(default=timezone.now)),
                ("post", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="reactions", to="partners.partnerpost")),
                ("user", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="partner_post_reactions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "partner_post_reaction", "unique_together": {("post", "user")}},
        ),
        migrations.AddIndex(
            model_name="partnerpostcomment",
            index=models.Index(fields=["post", "created_at"], name="partner_post_comment_post_created_idx"),
        ),
        migrations.AddIndex(
            model_name="partnerpostreaction",
            index=models.Index(fields=["post", "created_at"], name="partner_post_reaction_post_created_idx"),
        ),
    ]
