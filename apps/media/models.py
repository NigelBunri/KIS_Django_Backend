# media/models.py
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator

USER = settings.AUTH_USER_MODEL

class BaseEntity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    is_deleted = models.BooleanField(default=False, db_index=True)

    class Meta:
        abstract = True

class MediaModerationState(models.TextChoices):
    """Projected, queryable moderation state on MediaAsset. Distinct from
    MediaSafetyScan, which stays the append-only audit trail of every scan
    attempt (provider, version, result history) — this field is just the
    current, at-a-glance answer derived from that trail. Phase 1 sets every
    canonical-asset row created from a presigned upload to NOT_SCANNED,
    since none of the three migrated presigned flows (profile/commerce)
    actually make a moderation decision before the asset exists today —
    only statuses does, and that decision already lives entirely on
    MediaSafetyScan. Wiring this field to real decisions is Phase 2+ work."""

    NOT_SCANNED = "not_scanned", "Not scanned"
    PASSED = "passed", "Passed"
    PENDING_REVIEW = "pending_review", "Pending review"
    QUARANTINED = "quarantined", "Quarantined"


class MediaVisibility(models.TextChoices):
    PRIVATE = "private", "Private"
    PUBLIC = "public", "Public"


class MediaAsset(BaseEntity):
    """
    Core asset with packed advanced fields.

    Phase 1 of the KIS Universal Media Platform designates this model as
    the canonical media entity going forward: every MediaUploadIntent that
    reaches CONFIRMED now gets exactly one linked MediaAsset row (see
    MediaUploadIntent.canonical_asset and
    apps.media.upload_intent._ensure_canonical_asset), created once,
    idempotently, inside the same transaction confirm() already uses.

    The fields below `metadata` were added in that phase — all nullable or
    default-valued, so every pre-Phase-1 row keeps loading unchanged and no
    backfill migration was required. `bucket_key`/`bytes`/`status` (the
    original fields above) are deliberately left as-is rather than renamed
    to `storage_key`/`size`/a new lifecycle enum — `storage_key` and `size`
    below are read-only aliases, and `status` keeps its original
    pending/ready/blocked meaning for the legacy multipart path
    (UploadFileView) that already depends on it; the presigned-upload path
    always creates rows with status="ready" (see _ensure_canonical_asset)
    since a MediaAsset is only ever created once the S3 object is already
    confirmed to exist.
    """
    MEDIA_TYPES = [
        ("image", "Image"),
        ("video", "Video"),
        ("audio", "Audio"),
        ("document", "Document"),
        ("three_d", "3D"),
    ]
    owner = models.ForeignKey(USER, related_name="media_assets", on_delete=models.SET_NULL, null=True)
    type = models.CharField(max_length=32, choices=MEDIA_TYPES)
    bucket_key = models.CharField(max_length=1024)  # path/key in object storage
    canonical_url = models.URLField(max_length=2000, blank=True, null=True)
    mime_type = models.CharField(max_length=256, blank=True)
    bytes = models.BigIntegerField(default=0)
    dims = models.CharField(max_length=128, blank=True, help_text='WxH or duration')
    checksum = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=64, default="pending", db_index=True)  # pending|ready|blocked
    security = models.JSONField(default=dict, blank=True)      # { clientEncrypted, keyRef, drmPolicyId }
    provenance = models.JSONField(default=dict, blank=True)    # { originHash, anchorTx, editSummary }
    labels = models.JSONField(default=dict, blank=True)        # { synthetic: {...}, fingerprints: {...}, tags: [...] }
    storage = models.JSONField(default=dict, blank=True)       # { tier, retentionPolicy }
    metadata = models.JSONField(default=dict, blank=True)      # generic extensible metadata

    # --- Phase 1 additions: canonical-asset metadata (all additive/nullable) ---
    # `purpose` mirrors MediaUploadIntent.context's value exactly (e.g.
    # "status_image") for rows created via the presigned flow; blank for
    # legacy multipart rows (UploadFileView never had a purpose concept).
    purpose = models.CharField(max_length=64, blank=True, default="", db_index=True)
    # `context` is the broader feature bucket a purpose belongs to (e.g.
    # "status" for "status_image"/"status_video"/"status_audio") — see
    # apps.media.purposes.MediaPurpose.context. Distinct from `purpose`
    # itself the same way apps.media.safety's moderation contexts are
    # already a coarser grouping than upload_intent's per-feature contexts.
    context = models.CharField(max_length=64, blank=True, default="", db_index=True)
    target_type = models.CharField(max_length=64, blank=True, default="")
    target_id = models.CharField(max_length=64, blank=True, default="")
    original_filename = models.CharField(max_length=512, blank=True, default="")

    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    thumbnail = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="thumbnail_of",
    )

    moderation_state = models.CharField(
        max_length=20, choices=MediaModerationState.choices,
        default=MediaModerationState.NOT_SCANNED, db_index=True,
    )
    visibility = models.CharField(
        max_length=16, choices=MediaVisibility.choices, default=MediaVisibility.PRIVATE,
    )

    confirmed_at = models.DateTimeField(null=True, blank=True)
    attached_at = models.DateTimeField(null=True, blank=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    error_code = models.CharField(max_length=64, blank=True, default="")
    error_message = models.CharField(max_length=512, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["type", "status"]),
            models.Index(fields=["purpose", "status"]),
            models.Index(fields=["owner", "purpose"]),
            models.Index(fields=["target_type", "target_id"]),
        ]

    def __str__(self):
        return f"{self.type} {self.id}"

    def mark_ready(self, url=None):
        self.status = "ready"
        if url:
            self.canonical_url = url
        self.save(update_fields=["status", "canonical_url", "updated_at"])

    # Read-only naming aliases so Phase 2+ code (and the purpose registry)
    # can use the canonical field names from the platform design doc without
    # a migration renaming `bucket_key`/`bytes` — both already mean exactly
    # this on every row, presigned-created or legacy-multipart-created.
    @property
    def storage_key(self) -> str:
        return self.bucket_key

    @property
    def size(self) -> int:
        return self.bytes

class MediaVariant(BaseEntity):
    asset = models.ForeignKey(MediaAsset, related_name="variants", on_delete=models.CASCADE)
    purpose = models.CharField(max_length=64)   # thumbnail | adaptive | preview | low_bandwidth
    codec = models.CharField(max_length=128, blank=True)
    dims = models.CharField(max_length=128, blank=True)
    bytes = models.BigIntegerField(default=0)
    url = models.URLField(max_length=2000, blank=True, null=True)
    variant_meta = models.JSONField(default=dict, blank=True)  # { signed, edgeHints, personalizationRuleId }

    class Meta:
        unique_together = ("asset", "purpose", "codec")

class ProcessingJob(BaseEntity):
    PIPELINES = [
        ("transcode", "Transcode"),
        ("phash", "PerceptualHash"),
        ("watermark", "Watermark"),
        ("analyze", "Analyze"),
        ("redact", "Redact"),
    ]
    STATUS = [
        ("queued", "Queued"),
        ("running", "Running"),
        ("done", "Done"),
        ("failed", "Failed"),
    ]
    asset = models.ForeignKey(MediaAsset, related_name="jobs", on_delete=models.CASCADE)
    pipeline = models.CharField(max_length=64, choices=PIPELINES)
    status = models.CharField(max_length=32, choices=STATUS, default="queued", db_index=True)
    priority = models.IntegerField(default=50, validators=[MinValueValidator(0), MaxValueValidator(100)])
    worker_meta = models.JSONField(default=dict, blank=True)   # { region, costEstimateCents }
    result_meta = models.JSONField(default=dict, blank=True)   # outcome (phash, labels, errors...)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    def mark_running(self, worker_meta=None):
        self.status = "running"
        self.started_at = timezone.now()
        if worker_meta:
            self.worker_meta = worker_meta
        self.save(update_fields=["status", "started_at", "worker_meta", "updated_at"])

    def mark_done(self, result_meta=None):
        self.status = "done"
        self.finished_at = timezone.now()
        if result_meta:
            self.result_meta = result_meta
        self.save(update_fields=["status", "finished_at", "result_meta", "updated_at"])

class Provenance(BaseEntity):
    asset = models.OneToOneField(MediaAsset, related_name="provenance_detail", on_delete=models.CASCADE)
    origin_hash = models.CharField(max_length=256)
    anchor = models.JSONField(default=dict, blank=True)       # { chain, txHash, anchoredAt }
    version_log = models.JSONField(default=list, blank=True)  # edits and signatures

class Watermark(BaseEntity):
    asset = models.ForeignKey(MediaAsset, related_name="watermarks", on_delete=models.CASCADE)
    type = models.CharField(max_length=32, default="invisible")  # visible|invisible|robust
    proof = models.JSONField(default=dict, blank=True)           # detection proofs, confidence

class AccessPolicy(BaseEntity):
    asset = models.OneToOneField(MediaAsset, related_name="access_policy", on_delete=models.CASCADE)
    rules = models.JSONField(default=dict, blank=True)  # { users, roles, geofence, timeWindow }
    drm = models.JSONField(default=dict, blank=True)    # { enabled, policyId, licenseRef }

class MediaMetrics(BaseEntity):
    asset = models.OneToOneField(MediaAsset, related_name="metrics", on_delete=models.CASCADE)
    views = models.BigIntegerField(default=0)
    stream_minutes = models.BigIntegerField(default=0)
    downloads = models.BigIntegerField(default=0)
    reaction_summary = models.JSONField(default=dict, blank=True)  # aggregated interactions
    carbon_grams = models.FloatField(default=0.0)
    cost_cents = models.BigIntegerField(default=0)

    def add_view(self, minutes=0):
        self.views = models.F('views') + 1
        if minutes:
            self.stream_minutes = models.F('stream_minutes') + minutes
        self.save(update_fields=["views", "stream_minutes", "updated_at"])
        # refresh from db to get int value
        self.refresh_from_db()

    def estimate_carbon(self, bytes_processed: int, region_factor: float = 0.0000001):
        """
        Very simple carbon estimator: bytes_processed * region_factor => grams
        This should be replaced with a more robust model in production.
        """
        added = bytes_processed * region_factor
        self.carbon_grams = models.F('carbon_grams') + added
        self.save(update_fields=["carbon_grams", "updated_at"])
        self.refresh_from_db()
        return self.carbon_grams


class MediaUploadIntent(BaseEntity):
    """
    A server-issued reservation for a direct-to-S3 presigned-PUT upload.

    Distinct from MediaAsset/MediaSafetyScan on purpose: those model an
    asset that already exists (or is mid-processing) and carry a heavier
    pipeline (variants, processing jobs, safety scans) this feature doesn't
    need. A MediaUploadIntent instead tracks the handshake BEFORE the S3
    object exists — key/content-type/size were decided by the server, the
    client hasn't uploaded anything yet, and the record can legitimately
    expire unconfirmed. `context` is deliberately generic (not
    "profile_image_upload") so the same model/service serves any future
    upload surface (feed images, product images, documents, video) without
    a new table per surface.
    """

    STATUS_PENDING = "pending"
    STATUS_UPLOADED = "uploaded"
    STATUS_CONFIRMED = "confirmed"
    STATUS_FAILED = "failed"
    STATUS_EXPIRED = "expired"
    STATUS_ABORTED = "aborted"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_UPLOADED, "Uploaded"),
        (STATUS_CONFIRMED, "Confirmed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_ABORTED, "Aborted"),
    ]

    owner = models.ForeignKey(USER, related_name="media_upload_intents", on_delete=models.CASCADE)
    context = models.CharField(max_length=64, db_index=True)
    # target_id lets a context attach to a specific non-singleton resource
    # (e.g. a particular ProfileShowcase or Product row) instead of always
    # updating a single owner-scoped record like Profile. Optional — most
    # contexts implemented so far don't need it.
    target_id = models.CharField(max_length=64, blank=True, default="")
    original_filename = models.CharField(max_length=512, blank=True)
    object_key = models.CharField(max_length=1024)
    content_type = models.CharField(max_length=256)
    size_bytes = models.BigIntegerField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # Set once a confirmed upload has actually been attached to a real
    # resource (e.g. a Shop/Product/Service image, a complaint attachment).
    # Distinguishes "confirmed but orphaned" (never attached — safe to
    # garbage-collect after a grace period) from "confirmed and in use"
    # (must never be swept). Contexts that auto-attach at confirm time
    # (profile_avatar, profile_cover) set this in the same call that sets
    # confirmed_at; contexts with a separate explicit attach step (commerce)
    # set it only when that attach call actually succeeds.
    attached_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.CharField(max_length=512, blank=True)

    # Phase 1: link to the canonical MediaAsset row created once this intent
    # reaches CONFIRMED (see apps.media.upload_intent._ensure_canonical_asset).
    # Nullable/SET_NULL so every pre-Phase-1 row loads unchanged with no
    # backfill — only intents confirmed after this field existed ever get it
    # populated. One-to-one, not a FK, because an intent can never fan out to
    # more than one canonical asset (mirrors attached_at's "exactly once"
    # semantics one level up).
    canonical_asset = models.OneToOneField(
        MediaAsset, null=True, blank=True, on_delete=models.SET_NULL, related_name="source_intent",
    )

    class Meta:
        indexes = [
            models.Index(fields=["owner", "status"]),
            models.Index(fields=["status", "expires_at"]),
            models.Index(fields=["context", "status"]),
        ]

    def __str__(self):
        return f"{self.context} {self.status} {self.id}"

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def mark_confirmed(self):
        self.status = self.STATUS_CONFIRMED
        self.confirmed_at = timezone.now()
        self.save(update_fields=["status", "confirmed_at", "updated_at"])

    def mark_attached(self):
        if self.attached_at is not None:
            return
        self.attached_at = timezone.now()
        self.save(update_fields=["attached_at", "updated_at"])

    def mark_failed(self, code: str, message: str):
        self.status = self.STATUS_FAILED
        self.error_code = code[:64]
        self.error_message = message[:512]
        self.save(update_fields=["status", "error_code", "error_message", "updated_at"])

    def mark_aborted(self):
        """Phase 2: user-initiated cancellation of a not-yet-confirmed
        upload. STATUS_ABORTED has existed since Phase 0/1's STATUS_CHOICES
        but nothing wrote it until apps.media.services.lifecycle.cancel_upload —
        no migration needed, this is a new method over an existing column."""
        if self.status == self.STATUS_ABORTED:
            return
        self.status = self.STATUS_ABORTED
        self.save(update_fields=["status", "updated_at"])


class MediaSafetyScan(BaseEntity):
    STATUS_CHOICES = [
        ("pending_review", "Pending Review"),
        ("passed", "Passed"),
        ("blocked", "Blocked"),
        ("failed", "Failed"),
        ("not_configured", "Not Configured"),
    ]

    asset = models.ForeignKey(
        MediaAsset,
        related_name="safety_scans",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    owner = models.ForeignKey(
        USER,
        related_name="media_safety_scans",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    upload_id = models.CharField(max_length=128, blank=True, db_index=True)
    context = models.CharField(max_length=64, default="general", db_index=True)
    original_name = models.CharField(max_length=512, blank=True)
    mime_type = models.CharField(max_length=256, blank=True)
    bytes = models.BigIntegerField(default=0)
    checksum = models.CharField(max_length=128, blank=True)
    provider = models.CharField(max_length=64, default="stub", db_index=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default="pending_review", db_index=True)
    quarantine = models.BooleanField(default=True, db_index=True)
    requires_review = models.BooleanField(default=True, db_index=True)
    policy_version = models.CharField(max_length=64, default="kis-christian-safety-v1")
    reason = models.CharField(max_length=256, blank=True)
    result = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["context", "status"]),
            models.Index(fields=["owner", "status"]),
            models.Index(fields=["upload_id", "status"]),
        ]

    def __str__(self):
        return f"{self.context} {self.status} {self.upload_id or self.id}"
