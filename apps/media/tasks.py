# media/tasks.py
from celery import shared_task
from .models import ProcessingJob, MediaAsset
from .upload_intent import expire_abandoned_upload_intents, expire_unattached_confirmed_intents


@shared_task
def expire_abandoned_media_uploads():
    """Periodic sweep for presigned uploads that were never confirmed (S3
    PUT never happened, or happened but the client never called confirm).
    Schedule via Celery Beat; apps/media/management/commands/
    expire_media_uploads.py wraps the same function for manual/cron use
    where Beat isn't configured."""
    return expire_abandoned_upload_intents()


@shared_task
def expire_unattached_media_uploads():
    """Periodic sweep for CONFIRMED uploads that were never attached to a
    real resource (e.g. a marketplace flow where the client confirmed a
    product photo but never finished creating the product). Never touches
    an attached intent. Schedule via Celery Beat alongside
    expire_abandoned_media_uploads."""
    return expire_unattached_confirmed_intents()

@shared_task(bind=True)
def process_job_worker(self, job_id):
    """
    Worker stub: pick a ProcessingJob, perform pipeline, write results.
    Replace with integration to FFMPEG, image pipelines, ML models, etc.
    """
    job = ProcessingJob.objects.get(id=job_id)
    job.mark_running(worker_meta={"worker": "local-stub"})
    # Fake processing depending on pipeline
    if job.pipeline == "phash":
        # compute a faux perceptual hash
        result_meta = {"phash": "0000abcd1234", "derived_variant": None}
    elif job.pipeline == "analyze":
        result_meta = {"labels": {"nsfw": 0.01}, "derived_variant": None}
    else:
        result_meta = {"notes": "processed by stub"}

    job.mark_done(result_meta=result_meta)
    return {"job": str(job_id), "status": "done"}

@shared_task
def schedule_asset_processing(asset_id):
    asset = MediaAsset.objects.get(id=asset_id)
    # Create jobs for common pipelines
    ProcessingJob.objects.create(asset=asset, pipeline="phash", priority=40)
    ProcessingJob.objects.create(asset=asset, pipeline="analyze", priority=50)
    ProcessingJob.objects.create(asset=asset, pipeline="transcode", priority=60)
    return {"asset": str(asset_id)}
