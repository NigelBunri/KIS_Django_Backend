from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='UserSeason',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('category', models.CharField(
                    choices=[
                        ('health', 'Health & Medical'),
                        ('finances', 'Financial Hardship'),
                        ('relationships', 'Relationships & Marriage'),
                        ('faith', 'Faith & Spirituality'),
                        ('business', 'Business & Career'),
                        ('grief', 'Loss & Grief'),
                        ('addiction', 'Addiction & Recovery'),
                        ('family', 'Family & Parenting'),
                        ('mental_health', 'Mental Health'),
                        ('other', 'Other'),
                    ],
                    max_length=30,
                )),
                ('title', models.CharField(max_length=200)),
                ('description', models.TextField(blank=True, default='')),
                ('visibility', models.CharField(
                    choices=[
                        ('public', 'Public'),
                        ('testimony_only', 'Visible to testimony holders only'),
                        ('private', 'Private'),
                    ],
                    default='public',
                    max_length=20,
                )),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='seasons',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['category', 'is_active'], name='testimony_us_category_is_active_idx'),
                    models.Index(fields=['user', 'is_active'], name='testimony_us_user_is_active_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='UserTestimony',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('category', models.CharField(
                    choices=[
                        ('health', 'Health & Medical'),
                        ('finances', 'Financial Hardship'),
                        ('relationships', 'Relationships & Marriage'),
                        ('faith', 'Faith & Spirituality'),
                        ('business', 'Business & Career'),
                        ('grief', 'Loss & Grief'),
                        ('addiction', 'Addiction & Recovery'),
                        ('family', 'Family & Parenting'),
                        ('mental_health', 'Mental Health'),
                        ('other', 'Other'),
                    ],
                    max_length=30,
                )),
                ('title', models.CharField(max_length=200)),
                ('story', models.TextField(blank=True, default='')),
                ('is_available', models.BooleanField(default=True, help_text='Willing to be contacted about this')),
                ('endorsement_count', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='testimonies',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['category', 'is_available'], name='testimony_ut_category_is_available_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='TestimonyEndorsement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('endorsed_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='given_endorsements',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('testimony', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='endorsements_set',
                    to='testimony.usertestimony',
                )),
            ],
            options={
                'unique_together': {('testimony', 'endorsed_by')},
            },
        ),
        migrations.CreateModel(
            name='TestimonyReach',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('message', models.TextField(blank=True, default='')),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('accepted', 'Accepted'),
                        ('declined', 'Declined'),
                    ],
                    default='pending',
                    max_length=20,
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('from_user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='sent_reaches',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('to_user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='received_reaches',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('season', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='reaches',
                    to='testimony.userseason',
                )),
                ('testimony', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='reaches',
                    to='testimony.usertestimony',
                )),
            ],
            options={
                'ordering': ['-created_at'],
                'unique_together': {('from_user', 'season')},
                'indexes': [
                    models.Index(fields=['to_user', 'status'], name='testimony_tr_to_user_status_idx'),
                ],
            },
        ),
    ]
