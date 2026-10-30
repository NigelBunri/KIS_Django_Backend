from django.apps import AppConfig


class PrayerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.prayer'
    label = 'prayer'
    verbose_name = "Prayer Calendar (KIS)"
