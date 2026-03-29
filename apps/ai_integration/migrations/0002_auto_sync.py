# Placeholder migration to keep makemigrations from recreating 0002 files everytime we edit services.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("ai_integration", "0001_initial")]

    operations = []
