from celery import shared_task
from django.core.management import call_command


@shared_task
def dispatch_bible_reading_reminders():
    """Celery-beat entrypoint for the dispatch_bible_reading_reminders
    management command (apps/bible/management/commands/...). The command
    itself was correct but had no scheduler ever invoking it in
    production - only its own test called it directly via call_command.
    Kept as a thin wrapper rather than moving the logic here so the
    management command still works standalone for manual/local runs.
    """
    call_command("dispatch_bible_reading_reminders")
