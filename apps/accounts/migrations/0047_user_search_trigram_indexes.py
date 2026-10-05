import django.contrib.postgres.indexes
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0046_add_phone_is_placeholder'),
    ]

    operations = [
        # Idempotent (CREATE EXTENSION IF NOT EXISTS) - safe even though
        # commerce/0072_enable_trigram_search already enables this on most
        # deployments; migration order across apps isn't guaranteed.
        TrigramExtension(),
        # UnifiedSearchView._search_contacts (apps/core/views.py) filters
        # User with __icontains on all four of these fields. Plain btree
        # indexes (see User.Meta.indexes) don't help substring LIKE '%x%'
        # queries - without a trigram GIN index this degrades to a full
        # table scan on every "Search the whole app" keystroke once the
        # users table is large.
        migrations.AddIndex(
            model_name='user',
            index=django.contrib.postgres.indexes.GinIndex(fields=['display_name'], name='accounts_user_dispname_trgm', opclasses=['gin_trgm_ops']),
        ),
        migrations.AddIndex(
            model_name='user',
            index=django.contrib.postgres.indexes.GinIndex(fields=['username'], name='accounts_user_username_trgm', opclasses=['gin_trgm_ops']),
        ),
        migrations.AddIndex(
            model_name='user',
            index=django.contrib.postgres.indexes.GinIndex(fields=['phone'], name='accounts_user_phone_trgm', opclasses=['gin_trgm_ops']),
        ),
        migrations.AddIndex(
            model_name='user',
            index=django.contrib.postgres.indexes.GinIndex(fields=['email'], name='accounts_user_email_trgm', opclasses=['gin_trgm_ops']),
        ),
    ]
