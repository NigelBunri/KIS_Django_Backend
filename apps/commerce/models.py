

"""
Commerce Django App (commerce)

Purpose:
- Full-featured marketplace & shop platform focusing on security, authenticity, and advanced commerce features.
- Built to integrate with the AI & Automation app for verification, recommendations and fraud detection.

Key capabilities implemented:
- Shop creation + KYC-style verification workflow (ShopVerificationRequest)
- Product catalog with authenticity checks (ProductAuthenticityCheck)
- Orders, Payments (provider hooks), Promotions, Subscriptions, Loyalty
- FraudSignal model and async fraud evaluation
- Audit logs for compliance and traceability
- Background tasks (Celery) for verification & fraud computation
- Admin panels for manual review & audit
- Stubs/adapters in services.py to connect to third-party verifiers, OCR, blockchain verification, or image-forensics providers

Free-tier behavior / safety notes:
- Verification & authenticity adapters are stubbed for free-tier testing. Replace adapters with paid providers when ready (examples: Jumio, Onfido, Trulioo for KYC; Chainpoint or OpenTimestamps for blockchain anchoring; Virustotal/ImageForensics APIs for image checks).
- Use django-storages + S3/Backblaze for secure document storage.
- Enable server-side scanning and virus checks for user uploads.

Quick install:
1. Add "commerce" to INSTALLED_APPS.
2. Ensure DRF and Celery are installed and configured.
3. Run migrations: python manage.py makemigrations commerce && python manage.py migrate
4. Start Celery worker: celery -A your_project worker -l info
5. Configure object storage and set secure upload buckets.

Security & authenticity recommended next steps:
- Integrate a KYC provider for automated identity verification.
- Connect image-forensics models and brand catalogs for counterfeit detection.
- Use blockchain anchoring for high-value goods (store certificate hash on-chain).
- Add manual review queues with role-based access controls for reviewers.
- Add rate-limits, merchant onboarding checks, scoring thresholds and escalation rules.

"""

from datetime import timedelta
import uuid
from django.db import models
from django.db.models import JSONField as DjangoJSONField
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify
from django.core.exceptions import ValidationError
from apps.billing.models import WalletTransaction
from common.media_urls import normalize_image_payload

from .constants import KIS_COIN_CODE


class JSONField(DjangoJSONField):
    """Accept JSON values that the database driver has already decoded."""

    def from_db_value(self, value, expression, connection):
        if value is None or isinstance(value, (dict, list, int, float, bool)):
            return value
        return super().from_db_value(value, expression, connection)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        return name, "django.db.models.JSONField", args, kwargs


class BaseEntity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        abstract = True


class Shop(BaseEntity):
    STATUS_DRAFT = 'draft'
    STATUS_ACTIVE = 'active'
    STATUS_PAUSED = 'paused'
    STATUS_CHOICES = (
        (STATUS_DRAFT, 'Draft'),
        (STATUS_ACTIVE, 'Active'),
        (STATUS_PAUSED, 'Paused'),
    )

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='shops')
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    employee_slots = models.PositiveIntegerField(default=1)
    branding = JSONField(default=dict, blank=True)
    image_file = models.ImageField(upload_to='commerce/shops/', max_length=1024, null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)
    is_verified = models.BooleanField(default=False)
    verification_status = models.CharField(max_length=20, default='UNVERIFIED')  # PENDING | VERIFIED | REJECTED
    rating_avg = models.FloatField(default=0.0)
    rating_count = models.IntegerField(default=0)
    followers_count = models.IntegerField(default=0)
    membership_discount_pct = models.PositiveIntegerField(default=5)
    social_links = JSONField(default=dict, blank=True)
    analytics = JSONField(default=dict, blank=True)
    trust_badges = JSONField(default=list, blank=True)  # e.g., ['kyc','authenticity','secure-pay']
    membership_public = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=['slug'])]

    def __str__(self):
        return f"{self.name} ({self.owner})"

    @property
    def image_url(self):
        try:
            url = self.image_file.url if self.image_file else ''
        except ValueError:
            return ''
        if not url:
            return ''
        if url.startswith('http://') or url.startswith('https://'):
            return url
        site_url = settings.SITE_URL.rstrip('/')
        if site_url:
            return f"{site_url}{url}"
        return url


class ShopLandingPage(BaseEntity):
    shop = models.OneToOneField(Shop, on_delete=models.CASCADE, related_name='landing_page')
    headline = models.CharField(max_length=255, blank=True)
    subheadline = models.TextField(blank=True)
    hero_image_url = models.URLField(max_length=512, blank=True)
    hero_cta_text = models.CharField(max_length=128, blank=True)
    hero_cta_url = models.URLField(max_length=512, blank=True)
    builder_data = JSONField(default=dict, blank=True)
    is_public = models.BooleanField(default=False)
    is_published = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='shop_landing_pages_created',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='shop_landing_pages_updated',
    )

    class Meta:
        verbose_name = 'Shop landing page'
        verbose_name_plural = 'Shop landing pages'

    def __str__(self):
        return f"Landing page for {self.shop.name}"

    def save(self, *args, **kwargs):
        self.hero_image_url = normalize_image_payload(self.hero_image_url)
        super().save(*args, **kwargs)


class ShopLandingTestimonial(BaseEntity):
    landing_page = models.ForeignKey(
        ShopLandingPage,
        on_delete=models.CASCADE,
        related_name='testimonials',
    )
    quote = models.TextField()
    author = models.CharField(max_length=128, blank=True)
    role = models.CharField(max_length=128, blank=True)
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'created_at']

    def __str__(self):
        return f"Testimonial by {self.author or 'anonymous'} for {self.landing_page.shop.name}"


class ShopCategory(BaseEntity):
    CATEGORY_TYPES = [
        ('product', 'Product'),
        ('service', 'Service'),
        ('both', 'Both'),
    ]
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='categories')
    name = models.CharField(max_length=128)
    slug = models.SlugField(max_length=128)
    description = models.TextField(blank=True)
    category_type = models.CharField(max_length=16, choices=CATEGORY_TYPES, default='product')

    class Meta:
        unique_together = (('shop', 'slug'),)

    def save(self, *args, **kwargs):
        if not self.slug and self.name:
            base = slugify(self.name) or 'category'
            slug = base
            suffix = 1
            while ShopCategory.objects.filter(shop=self.shop, slug=slug).exclude(id=self.id).exists():
                slug = f"{base}-{suffix}"
                suffix += 1
            self.slug = slug
        super().save(*args, **kwargs)


class CatalogCategory(BaseEntity):
    CATEGORY_TYPES = [
        ('product', 'Product'),
        ('service', 'Service'),
    ]
    name = models.CharField(max_length=128)
    slug = models.SlugField(max_length=128, unique=True)
    description = models.TextField(blank=True)
    category_type = models.CharField(max_length=16, choices=CATEGORY_TYPES)
    parent = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='children',
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['category_type', 'sort_order', 'name']

    def __str__(self):
        return f"{self.name} ({self.category_type})"


class ShopService(BaseEntity):
    VISIBILITY_CHOICES = [('draft', 'Draft'), ('public', 'Public'), ('unlisted', 'Unlisted'), ('private', 'Private')]
    STATUS_CHOICES = [('draft', 'Draft'), ('published', 'Published'), ('paused', 'Paused')]

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='services')
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    short_summary = models.CharField(max_length=320, blank=True, default='')
    tags = JSONField(default=list, blank=True)
    description = models.TextField(blank=True, default='')
    pricing_model = models.CharField(max_length=32, blank=True, default='standard')
    service_type = models.CharField(max_length=64, blank=True, default='Appointment')
    delivery_modes = JSONField(default=list, blank=True)
    visibility = models.CharField(max_length=16, choices=VISIBILITY_CHOICES, default='draft')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='draft')
    featured = models.BooleanField(default=False)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    compare_at_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    deposit_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    deposit_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    minimum_charge = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    negotiable = models.BooleanField(default=False)
    tax_inclusive = models.BooleanField(default=True)
    quote_required = models.BooleanField(default=False)
    catalog_categories = models.ManyToManyField(
        'CatalogCategory',
        blank=True,
        related_name='services',
    )
    availability = JSONField(default=dict, blank=True)
    availability_rules = JSONField(default=list, blank=True)
    blackout_dates = JSONField(default=list, blank=True)
    coverage = JSONField(default=list, blank=True)
    remote_regions = JSONField(default=list, blank=True)
    remote_meeting_link = models.CharField(max_length=512, blank=True, default='')
    address_line1 = models.CharField(max_length=255, blank=True, default='')
    address_line2 = models.CharField(max_length=255, blank=True, default='')
    city = models.CharField(max_length=128, blank=True, default='')
    state = models.CharField(max_length=128, blank=True, default='')
    country = models.CharField(max_length=128, blank=True, default='')
    postal_code = models.CharField(max_length=20, blank=True, default='')
    travel_radius_km = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    timezone = models.CharField(max_length=64, blank=True, default='UTC')
    duration_minutes = models.PositiveIntegerField(default=60)
    prep_buffer_minutes = models.PositiveIntegerField(default=0)
    cleanup_buffer_minutes = models.PositiveIntegerField(default=0)
    turnaround_hours = models.PositiveIntegerField(default=0)
    max_bookings_per_slot = models.PositiveIntegerField(default=1)
    group_booking_allowed = models.BooleanField(default=False)
    allow_multiple_attendees_per_slot = models.BooleanField(default=False)
    max_participants = models.PositiveIntegerField(default=1)
    staff_required = models.PositiveIntegerField(default=1)
    min_notice_hours = models.PositiveIntegerField(default=24)
    max_advance_booking_days = models.PositiveIntegerField(default=90)
    cancellation_window_hours = models.PositiveIntegerField(default=24)
    reschedule_window_hours = models.PositiveIntegerField(default=24)
    auto_confirm_booking = models.BooleanField(default=True)
    approval_required = models.BooleanField(default=False)
    packages = JSONField(default=list, blank=True)
    addons = JSONField(default=list, blank=True)
    requirements = JSONField(default=list, blank=True)
    refund_policy = models.TextField(blank=True, default='')
    warranty_policy = models.TextField(blank=True, default='')
    service_terms = models.TextField(blank=True, default='')
    seo_title = models.CharField(max_length=255, blank=True, default='')
    seo_description = models.TextField(blank=True, default='')
    published_at = models.DateTimeField(null=True, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    other_shops_discount = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    image_file = models.ImageField(upload_to='commerce/services/', max_length=1024, null=True, blank=True)
    image_url = models.URLField(max_length=512, blank=True)
    rating_avg = models.FloatField(default=0.0)
    rating_count = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)

    class Meta:
        unique_together = (('shop', 'slug'),)
        indexes = [
            models.Index(fields=['shop', 'name']),
            models.Index(fields=['status']),
            models.Index(fields=['visibility']),
            models.Index(fields=['shop']),
            models.Index(fields=['price']),
            models.Index(fields=['published_at']),
        ]

    def __str__(self):
        return f"Service {self.name} ({self.shop.name})"

    def save(self, *args, **kwargs):
        self.image_url = normalize_image_payload(self.image_url)
        super().save(*args, **kwargs)


class ServiceBooking(BaseEntity):
    STATUS_PENDING = 'pending'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_COMPLETED = 'completed'
    STATUS_AWAITING_SATISFACTION = 'awaiting_satisfaction'
    STATUS_DISPUTE = 'dispute'
    STATUS_REFUNDED = 'refunded'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_CONFIRMED, 'Confirmed'),
        (STATUS_CANCELLED, 'Cancelled'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_AWAITING_SATISFACTION, 'Awaiting satisfaction'),
        (STATUS_DISPUTE, 'Dispute'),
        (STATUS_REFUNDED, 'Refunded'),
    ]

    service = models.ForeignKey(ShopService, on_delete=models.CASCADE, related_name='bookings')
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='bookings')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='service_bookings')
    scheduled_at = models.DateTimeField()
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    price_cents = models.BigIntegerField(default=0)
    deposit_cents = models.BigIntegerField(default=0)
    balance_cents = models.BigIntegerField(default=0)
    instructions = models.TextField(blank=True, default='')
    payment_tx_ref = models.CharField(max_length=128, blank=True, default='')
    remote_meeting_link = models.CharField(max_length=512, blank=True, default='')
    metadata = JSONField(default=dict, blank=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    provider_completed_at = models.DateTimeField(null=True, blank=True)
    payer_satisfied_at = models.DateTimeField(null=True, blank=True)
    satisfaction_deadline = models.DateTimeField(null=True, blank=True)

    @property
    def provider_user(self):
        return getattr(self.shop, "owner", None)

    @property
    def is_remote_session(self):
        return bool(self.remote_meeting_link)

    @property
    def awaiting_satisfaction(self):
        return self.status == self.STATUS_AWAITING_SATISFACTION

    def mark_provider_completed(self):
        now = timezone.now()
        self.provider_completed_at = now
        self.satisfaction_deadline = now + timedelta(days=3)

    class Meta:
        ordering = ['-scheduled_at']
        indexes = [
            models.Index(fields=['service', 'scheduled_at']),
            models.Index(fields=['user', 'status']),
        ]
        constraints = []

    def __str__(self):
        return f"Booking {self.service.name} for {self.user} on {self.scheduled_at}"

    @property
    def complaint_window_expires(self):
        if not self.provider_completed_at:
            return None
        return self.provider_completed_at + timedelta(days=3)


class ServiceBookingReceipt(BaseEntity):
    PHASE_DEPOSIT = 'deposit'
    PHASE_REMAINING = 'remaining'
    PHASE_CHOICES = [
        (PHASE_DEPOSIT, 'Deposit payment'),
        (PHASE_REMAINING, 'Remaining payment'),
    ]

    booking = models.ForeignKey(
        ServiceBooking,
        on_delete=models.CASCADE,
        related_name='receipts',
    )
    amount_cents = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=12, default=KIS_COIN_CODE)
    transaction_reference = models.CharField(max_length=128, blank=True, default='')
    phase = models.CharField(max_length=32, choices=PHASE_CHOICES, default=PHASE_DEPOSIT)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['booking', 'phase']),
            models.Index(fields=['transaction_reference']),
        ]

    def __str__(self):
        return f"Receipt {self.phase} for booking {self.booking_id}"


class ServiceBookingPayment(BaseEntity):
    STATUS_PENDING = 'pending'
    STATUS_PAID = 'paid'
    STATUS_FAILED = 'failed'
    STATUS_REFUNDED = 'refunded'
    STATUS_SATISFIED = 'satisfied'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_PAID, 'Paid'),
        (STATUS_FAILED, 'Failed'),
        (STATUS_REFUNDED, 'Refunded'),
        (STATUS_SATISFIED, 'Satisfied'),
    ]

    booking = models.OneToOneField(
        ServiceBooking,
        on_delete=models.CASCADE,
        related_name='payment',
    )
    amount_cents = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=12, default=KIS_COIN_CODE)
    payment_method = models.CharField(max_length=64, blank=True, default='wallet')
    payment_status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    paid_at = models.DateTimeField(null=True, blank=True)
    transaction_reference = models.CharField(max_length=128, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    satisfied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['payment_status']),
            models.Index(fields=['paid_at']),
            models.Index(fields=['satisfied_at']),
        ]

    def __str__(self):
        return f"Payment {self.transaction_reference or self.id} for booking {self.booking_id}"


class ServiceBookingEscrow(BaseEntity):
    STATUS_PENDING = 'pending'
    STATUS_AWAITING_SATISFACTION = 'awaiting_satisfaction'
    STATUS_RELEASED = 'released'
    STATUS_REFUNDED = 'refunded'
    STATUS_DISPUTE = 'dispute'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_AWAITING_SATISFACTION, 'Awaiting satisfaction'),
        (STATUS_RELEASED, 'Released'),
        (STATUS_REFUNDED, 'Refunded'),
        (STATUS_DISPUTE, 'Dispute'),
    ]

    booking = models.OneToOneField(ServiceBooking, on_delete=models.CASCADE, related_name='escrow')
    payer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='service_escrows',
    )
    provider = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='service_payouts',
    )
    amount_cents = models.BigIntegerField(default=0)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    payment_reference = models.CharField(max_length=128, blank=True, default='')
    locked_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    refunded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    metadata = JSONField(default=dict, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['locked_at']),
            models.Index(fields=['released_at']),
            models.Index(fields=['refunded_at']),
        ]

    def __str__(self):
        return f"Escrow for {self.booking_id} ({self.amount_cents} cents)"

    @property
    def is_pending(self):
        return self.status == self.STATUS_PENDING


class ServiceBookingComplaint(BaseEntity):
    STATUS_SUBMITTED = 'submitted'
    STATUS_UNDER_REVIEW = 'under_review'
    STATUS_RESOLVED_RELEASE = 'resolved_release_provider'
    STATUS_RESOLVED_REFUND = 'resolved_refund_customer'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_SUBMITTED, 'Submitted'),
        (STATUS_UNDER_REVIEW, 'Under review'),
        (STATUS_RESOLVED_RELEASE, 'Resolved • Release payment'),
        (STATUS_RESOLVED_REFUND, 'Resolved • Refund customer'),
        (STATUS_REJECTED, 'Rejected'),
    ]
    ACTION_NONE = 'none'
    ACTION_RELEASE = 'release'
    ACTION_REFUND = 'refund'
    ACTION_CHOICES = [
        (ACTION_NONE, 'None'),
        (ACTION_RELEASE, 'Release payment'),
        (ACTION_REFUND, 'Refund payment'),
    ]

    booking = models.ForeignKey(
        ServiceBooking,
        on_delete=models.CASCADE,
        related_name='complaints',
    )
    payment = models.ForeignKey(
        ServiceBookingPayment,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='complaints',
    )
    escrow = models.ForeignKey(
        ServiceBookingEscrow,
        on_delete=models.CASCADE,
        related_name='complaints',
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='service_booking_complaints',
    )
    provider = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='service_provider_complaints',
    )
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_SUBMITTED)
    action = models.CharField(max_length=32, choices=ACTION_CHOICES, default=ACTION_NONE)
    transaction_reference = models.CharField(max_length=128, blank=True)
    receipt_url = models.URLField(max_length=512, blank=True)
    personal_statement = models.TextField(blank=True)
    reason = models.TextField(blank=True)
    service_name = models.CharField(max_length=255, blank=True)
    shop_name = models.CharField(max_length=255, blank=True)
    provider_info = JSONField(default=dict, blank=True)
    metadata = JSONField(default=dict, blank=True)
    resolution_note = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='resolved_service_booking_complaints',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['action']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"Complaint {self.id} · booking {self.booking_id}"


class ShopServiceImage(BaseEntity):
    service = models.ForeignKey(ShopService, on_delete=models.CASCADE, related_name='images')
    image_file = models.ImageField(upload_to='commerce/service-images/', max_length=1024, null=True, blank=True)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"Image {self.order} for {self.service.name}"


class ServiceRating(BaseEntity):
    service = models.ForeignKey(ShopService, on_delete=models.CASCADE, related_name='ratings')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='service_ratings')
    score = models.PositiveSmallIntegerField()

    class Meta:
        unique_together = ('service', 'user')

    def __str__(self):
        return f"Rating {self.score} for {self.service.name} by {self.user}"


class ShopVerificationRequest(BaseEntity):
    SHOP_DOC_TYPES = [('ID', 'ID Document'), ('BUSINESS_REG', 'Business Registration'), ('INVOICE', 'Proof of Sales')]
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='verification_requests')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, default='PENDING')  # PENDING | IN_REVIEW | APPROVED | REJECTED
    documents = JSONField(default=list, blank=True)  # list of {type, url, meta}
    reviewer_notes = models.TextField(blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    risk_score = models.FloatField(null=True, blank=True)



class Product(BaseEntity):
    INVENTORY_TYPES = [
        ('PHYSICAL', 'Physical'),
        ('DIGITAL', 'Digital'),
        ('SERVICE', 'Service'),
    ]

    # Core Relations & Identity
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='products')
    sku = models.CharField(max_length=128, unique=True)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)

    # Media
    main_image = models.ImageField(upload_to='commerce/products/main/', max_length=1024, null=True, blank=True)
    description = models.TextField(blank=True)

    # Pricing
    price = models.DecimalField(max_digits=12, decimal_places=2, help_text="Original retail price")
    sale_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Discounted selling price"
    )
    currency = models.CharField(max_length=8, default=KIS_COIN_CODE)

    # Inventory & Classification
    inventory_type = models.CharField(max_length=20, choices=INVENTORY_TYPES, default='PHYSICAL')
    stock_qty = models.IntegerField(default=0)
    low_stock_threshold = models.PositiveIntegerField(null=True, blank=True)
    catalog_categories = models.ManyToManyField('CatalogCategory', blank=True, related_name='products')

    # Flexible Data
    attributes = JSONField(default=dict, blank=True)
    variants = JSONField(default=list, blank=True)

    # Status & Visibility
    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    requires_shipping = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=['sku']),
            models.Index(fields=['slug']),
            models.Index(fields=['shop', 'is_active']),
            models.Index(fields=['inventory_type']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} [{self.sku}]"

    @property
    def effective_image_url(self):
        try:
            if self.main_image:
                return self.main_image.url
        except ValueError:
            pass

        first_gallery = self.gallery_images.filter(is_active=True).order_by('sort_order', 'id').first()
        if first_gallery and first_gallery.image_file:
            try:
                return first_gallery.image_file.url
            except ValueError:
                pass

        return ''

    @property
    def gallery_image_urls(self):
        urls = []
        for item in self.gallery_images.filter(is_active=True).order_by('sort_order', 'id'):
            try:
                if item.image_file:
                    urls.append(item.image_file.url)
            except ValueError:
                continue
        return urls

    def clean(self):
        super().clean()

        if self.sale_price is not None and self.sale_price > self.price:
            raise ValidationError({'sale_price': 'Sale price cannot be greater than price.'})

        if self.inventory_type in ['DIGITAL', 'SERVICE'] and self.requires_shipping:
            raise ValidationError({
                'requires_shipping': 'Digital or service products should not require shipping.'
            })


class ProductImage(BaseEntity):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='gallery_images')
    image_file = models.ImageField(upload_to='commerce/products/gallery/', max_length=1024)
    alt_text = models.CharField(max_length=255, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort_order', 'id']
        indexes = [
            models.Index(fields=['product', 'sort_order']),
            models.Index(fields=['product', 'is_active']),
        ]

    def __str__(self):
        return f"{self.product.name} - Gallery Image #{self.id}"


class ProductVariant(BaseEntity):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='product_variants')
    size = models.CharField(max_length=64, blank=True, default='')
    color = models.CharField(max_length=64, blank=True, default='')
    sku = models.CharField(max_length=128, blank=True, default='')
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    stock_qty = models.IntegerField(default=0)
    image_url = models.URLField(max_length=512, blank=True, default='')
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=['product'], name='commerce_variant_product_idx'),
            models.Index(fields=['sku'], name='commerce_variant_sku_idx'),
        ]

    def __str__(self):
        return f"Variant {self.sku or self.id} of {self.product.name}"

    def save(self, *args, **kwargs):
        self.image_url = normalize_image_payload(self.image_url)
        super().save(*args, **kwargs)


class Cart(BaseEntity):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('checked_out', 'Checked out'),
        ('abandoned', 'Abandoned'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='carts')
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='carts')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='active')
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        indexes = [models.Index(fields=['user', 'shop', 'status'])]

    def __str__(self):
        return f"Cart {self.id} ({self.user}) of {self.shop.name}"


class CartItem(BaseEntity):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='cart_items')
    variant = models.CharField(max_length=128, blank=True, default='')
    size = models.CharField(max_length=64, blank=True, default='')
    color = models.CharField(max_length=64, blank=True, default='')
    variant_snapshot = JSONField(default=dict, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    price_snapshot = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    stock_snapshot = models.IntegerField(default=0)
    selected_attributes = JSONField(default=dict, blank=True)
    attribute_labels = JSONField(default=dict, blank=True)
    custom_description = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=['cart'])]
        unique_together = [('cart', 'product', 'variant')]

    def __str__(self):
        return f"{self.quantity}x {self.product.name} in cart {self.cart.id}"


class MarketplaceOrderStatus(models.TextChoices):
    TEMPORAL = 'temporal', 'Temporal'
    CANCELLED = 'cancelled', 'Cancelled'
    AWAITING_SATISFACTION = 'awaiting_satisfaction', 'Awaiting satisfaction'
    SATISFIED = 'satisfied', 'Satisfied'
    COMPLETED = 'completed', 'Completed'
    COMPLAINT = 'complaint', 'Complaint'


class MarketplaceOrder(BaseEntity):
    buyer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='marketplace_orders')
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='marketplace_orders')
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=16, default=KIS_COIN_CODE)
    status = models.CharField(max_length=32, choices=MarketplaceOrderStatus.choices, default=MarketplaceOrderStatus.TEMPORAL)
    metadata = JSONField(default=dict, blank=True)
    buyer_debit_transaction = models.ForeignKey(
        WalletTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='marketplace_buyer_orders',
    )
    provider_credit_transaction = models.ForeignKey(
        WalletTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='marketplace_provider_orders',
    )
    receipt_generated = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=['buyer', 'status']),
            models.Index(fields=['shop', 'status']),
        ]

    def __str__(self):
        return f"MarketplaceOrder {self.id} ({self.buyer})"


class MarketplaceOrderItem(BaseEntity):
    order = models.ForeignKey(MarketplaceOrder, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='marketplace_order_items')
    variant_id = models.CharField(max_length=128, blank=True, default='')
    quantity = models.PositiveIntegerField(default=1)
    unit_price_cents = models.PositiveIntegerField()
    selected_attributes = JSONField(default=dict, blank=True)
    custom_description = models.TextField(blank=True)

    class Meta:
        unique_together = [('order', 'product', 'variant_id')]
        indexes = [models.Index(fields=['order'])]

    def __str__(self):
        return f"{self.quantity}x {self.product.name} in order {self.order.id}"


class MarketplaceComplaintStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    REVIEWED = 'reviewed', 'Reviewed'
    RESOLVED = 'resolved', 'Resolved'


class MarketplaceComplaint(BaseEntity):
    order = models.ForeignKey(MarketplaceOrder, on_delete=models.CASCADE, related_name='complaints')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='marketplace_complaints')
    text = models.TextField()
    attachment = models.FileField(upload_to='commerce/marketplace/complaints/', max_length=1024, null=True, blank=True)
    status = models.CharField(max_length=32, choices=MarketplaceComplaintStatus.choices, default=MarketplaceComplaintStatus.PENDING)

    class Meta:
        indexes = [models.Index(fields=['order'])]

    def __str__(self):
        return f"Complaint {self.id} for order {self.order.id}"


class ProductRating(BaseEntity):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='ratings')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='product_ratings')
    score = models.PositiveSmallIntegerField()

    class Meta:
        unique_together = ('product', 'user')


class ProductReview(BaseEntity):
    STATUS_PENDING = 'pending_review'
    STATUS_PUBLISHED = 'published'
    STATUS_HIDDEN = 'hidden'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending review'),
        (STATUS_PUBLISHED, 'Published'),
        (STATUS_HIDDEN, 'Hidden'),
    ]

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='commerce_product_reviews')
    rating = models.PositiveSmallIntegerField(default=5)
    title = models.CharField(max_length=160, blank=True, default='')
    body = models.TextField(blank=True, default='')
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PUBLISHED, db_index=True)
    helpful_count = models.PositiveIntegerField(default=0)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ('product', 'user')
        indexes = [
            models.Index(fields=['product', 'status']),
            models.Index(fields=['user', 'status']),
        ]

    def __str__(self):
        return f"Review {self.rating} for {self.product_id} by {self.user_id}"


class ProductQuestion(BaseEntity):
    STATUS_OPEN = 'open'
    STATUS_ANSWERED = 'answered'
    STATUS_HIDDEN = 'hidden'
    STATUS_CHOICES = [
        (STATUS_OPEN, 'Open'),
        (STATUS_ANSWERED, 'Answered'),
        (STATUS_HIDDEN, 'Hidden'),
    ]

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='questions')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='commerce_product_questions')
    question = models.TextField()
    answer = models.TextField(blank=True, default='')
    answered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='commerce_product_answers',
    )
    answered_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['product', 'status']),
            models.Index(fields=['user', 'status']),
        ]

    def __str__(self):
        return f"Question for {self.product_id} by {self.user_id}"


class ProductAuthenticityCheck(BaseEntity):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='auth_checks')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    provider = models.CharField(max_length=100, default='local_ai')
    status = models.CharField(max_length=20, default='PENDING')  # PENDING | PROCESSING | VERIFIED | FLAGGED | ERROR
    result = JSONField(default=dict, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)


class Promotion(BaseEntity):
    DISCOUNT_TYPES = [('PERCENT','Percent'),('FIXED','Fixed')]
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='promotions')
    code = models.CharField(max_length=64, unique=True)
    description = models.TextField(blank=True)
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPES)
    discount_value = models.DecimalField(max_digits=8, decimal_places=2)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    usage_limit = models.IntegerField(null=True, blank=True)
    used_count = models.IntegerField(default=0)
    applicable_products = JSONField(default=list, blank=True)
    social_boost = models.BooleanField(default=False)


class Subscription(BaseEntity):
    STATUS = [('ACTIVE','Active'),('PAUSED','Paused'),('CANCELLED','Cancelled')]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='shop_subscriptions')
    shop = models.ForeignKey(Shop, on_delete=models.SET_NULL, null=True, blank=True, related_name='shop_subscriptions')
    plan_name = models.CharField(max_length=128)
    status = models.CharField(max_length=20, choices=STATUS, default='ACTIVE')
    start_date = models.DateTimeField()
    end_date = models.DateTimeField(null=True, blank=True)
    next_billing_date = models.DateTimeField(null=True, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(
        max_length=8,
        choices=[(KIS_COIN_CODE, KIS_COIN_CODE)],
        default=KIS_COIN_CODE,
    )
    perks = JSONField(default=dict, blank=True)


class LoyaltyPoint(BaseEntity):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='loyalty_points')
    shop = models.ForeignKey(Shop, on_delete=models.SET_NULL, null=True, blank=True)
    points = models.IntegerField()
    earned_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True)


class ShopFollow(BaseEntity):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='follows')
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='followers')
    followed_at = models.DateTimeField(auto_now_add=True)


class ShopRole(models.TextChoices):
    OWNER = 'owner', 'Owner'
    MANAGER = 'manager', 'Manager'
    ADMIN = 'admin', 'Admin'
    MEMBER = 'member', 'Member'


class ShopTeamMember(BaseEntity):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='team_members')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='shop_memberships')
    role = models.CharField(max_length=16, choices=ShopRole.choices, default=ShopRole.MEMBER)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = (('shop', 'user'),)
        indexes = [
            models.Index(fields=['shop', 'role']),
            models.Index(fields=['user']),
        ]

    def __str__(self):
        return f"{self.user} as {self.role} in {self.shop}"


class ProductShare(BaseEntity):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    shared_at = models.DateTimeField(auto_now_add=True)


class ProductSubscription(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='product_subscriptions')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='subscriptions')
    subscribed_at = models.DateTimeField(default=timezone.now)
    platform = models.CharField(max_length=64, default='kis-platform')

    class Meta:
        unique_together = ('user', 'product')

    def __str__(self):
        return f'{self.user} subscribes to {self.product}'


class AIRecommendation(BaseEntity):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    target_type = models.CharField(max_length=50)  # Product | Shop
    target_id = models.UUIDField()
    score = models.FloatField()
    reason = models.TextField(blank=True)


class AuditLog(BaseEntity):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=255)
    target_type = models.CharField(max_length=255)
    target_id = models.UUIDField(null=True, blank=True)
    metadata = JSONField(default=dict, blank=True)


class FraudSignal(BaseEntity):
    source = models.CharField(max_length=100)
    entity_type = models.CharField(max_length=100)  # order, payment, shop, product
    entity_id = models.UUIDField()
    score = models.FloatField()
    details = JSONField(default=dict, blank=True)
    processed = models.BooleanField(default=False)


class OrderStatus(models.TextChoices):
    TEMPORAL = "temporal", "Temporal"
    ESCROW = "escrow", "Escrow"
    CANCELLED = "cancelled", "Cancelled"
    SATISFIED = "satisfied", "Satisfied"
    COMPLETED = "completed", "Completed"


class Order(BaseEntity):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="orders",
    )
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="orders")
    provider = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="received_orders",
        null=True,
        blank=True,
    )
    total_cents = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=8, default=KIS_COIN_CODE)
    status = models.CharField(max_length=32, choices=OrderStatus.choices, default=OrderStatus.TEMPORAL)
    metadata = JSONField(default=dict, blank=True)
    wallet_transaction = models.ForeignKey(
        WalletTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    provider_transaction = models.ForeignKey(
        WalletTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="provider_orders",
    )
    escrow_expires_at = models.DateTimeField(null=True, blank=True)

    is_buyer = models.BooleanField(default=True)

    class Meta:
        indexes = [models.Index(fields=["status"]), models.Index(fields=["user"]), models.Index(fields=["shop"])]
    def __str__(self):
        return f"Order {self.id} · {self.status}"


class OrderItem(BaseEntity):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("Product", on_delete=models.CASCADE)
    variant_id = models.CharField(max_length=255, blank=True)
    sku = models.CharField(max_length=128, blank=True)
    unit_price_cents = models.BigIntegerField(default=0)
    quantity = models.PositiveIntegerField(default=1)
    selected_attributes = JSONField(default=dict, blank=True)
    metadata = JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("order", "product", "variant_id")
        indexes = [models.Index(fields=["product"])]

    def line_total(self):
        return self.unit_price_cents * self.quantity


class Payment(BaseEntity):
    PAYMENT_STATUS = [
        ("pending", "Pending"),
        ("escrow", "Escrow"),
        ("success", "Success"),
        ("failed", "Failed"),
        ("refunded", "Refunded"),
    ]
    PAYMENT_ROLE = [
        ("buyer", "Buyer debit"),
        ("provider", "Provider payout"),
        ("escrow_release", "Escrow release"),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    provider = models.CharField(max_length=100)
    method = models.CharField(max_length=50)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=8, choices=[(KIS_COIN_CODE, KIS_COIN_CODE)], default=KIS_COIN_CODE)
    status = models.CharField(max_length=20, choices=PAYMENT_STATUS, default="pending")
    role = models.CharField(max_length=32, choices=PAYMENT_ROLE, default="buyer")
    provider_ref = models.CharField(max_length=255, blank=True)
    related_payment = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="related_payments",
    )
    captured_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["order", "status"])]

    def __str__(self):
        return f"Payment {self.id} · {self.role} ({self.status})"


class Complaint(BaseEntity):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="complaints")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    description = models.TextField()
    receipt_file = models.FileField(upload_to="commerce/complaints/", null=True, blank=True)
    resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["order", "user"])]

    def __str__(self):
        return f"Complaint {self.id} · Order {self.order_id}"
