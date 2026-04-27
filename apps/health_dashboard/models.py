import uuid

from django.db import models
from django.utils import timezone

from apps.accounts.models import User
from apps.broadcasts.models import BroadcastHealthInstitution, BroadcastHealthInstitutionService
from common.media_urls import normalize_image_payload


class TimeStampedUUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class HealthDashboardInstitution(TimeStampedUUIDModel):
    broadcast_institution = models.OneToOneField(
        BroadcastHealthInstitution,
        on_delete=models.CASCADE,
        related_name="health_dashboard",
    )
    institution_uid = models.CharField(max_length=128, unique=True, db_index=True)
    owner_user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="health_dashboard_institutions",
    )
    institution_type = models.CharField(max_length=64, default="clinic")
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True, db_index=True)

    about_text = models.TextField(blank=True, default="")
    staff_display_enabled = models.BooleanField(default=True)
    pricing_visibility_enabled = models.BooleanField(default=True)
    emergency_banner_enabled = models.BooleanField(default=False)
    emergency_banner_message = models.TextField(blank=True, default="")

    landing_background_image_url = models.TextField(blank=True, default="")
    landing_background_color_key = models.CharField(max_length=64, blank=True, default="")
    landing_logo_url = models.TextField(blank=True, default="")

    class Meta:
        db_table = "health_dashboard_institution"
        indexes = [
            models.Index(fields=["owner_user", "institution_type"]),
            models.Index(fields=["is_active"]),
        ]

    def save(self, *args, **kwargs):
        self.landing_background_image_url = normalize_image_payload(self.landing_background_image_url)
        self.landing_logo_url = normalize_image_payload(self.landing_logo_url)
        super().save(*args, **kwargs)


class HealthDashboardCard(TimeStampedUUIDModel):
    dashboard = models.OneToOneField(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="card",
    )
    tagline = models.CharField(max_length=255, blank=True, default="")
    short_description = models.TextField(blank=True, default="")
    accent_color_key = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "health_dashboard_card"


class HealthDashboardInstitutionService(TimeStampedUUIDModel):
    dashboard = models.ForeignKey(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="institution_services",
    )
    source_broadcast_service = models.OneToOneField(
        BroadcastHealthInstitutionService,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dashboard_service",
    )
    service_uid = models.CharField(max_length=128, db_index=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    active = models.BooleanField(default=True, db_index=True)
    base_price_cents = models.PositiveIntegerField(null=True, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "health_dashboard_institution_service"
        constraints = [
            models.UniqueConstraint(
                fields=["dashboard", "service_uid"],
                name="health_dashboard_institution_service_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["dashboard", "sort_order"]),
            models.Index(fields=["dashboard", "active"]),
        ]


class HealthDashboardInstitutionServiceMedium(TimeStampedUUIDModel):
    service = models.ForeignKey(
        HealthDashboardInstitutionService,
        on_delete=models.CASCADE,
        related_name="medium_rows",
    )
    medium_uid = models.CharField(max_length=128, blank=True, default="", db_index=True)
    medium_name = models.CharField(max_length=255, blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "health_dashboard_institution_service_medium"
        constraints = [
            models.UniqueConstraint(
                fields=["service", "medium_uid", "medium_name"],
                name="health_dashboard_service_medium_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["service", "sort_order"]),
        ]


class HealthDashboardHero(TimeStampedUUIDModel):
    dashboard = models.OneToOneField(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="hero",
    )
    image_url = models.TextField(blank=True, default="")
    title = models.CharField(max_length=255, blank=True, default="")
    slogan = models.TextField(blank=True, default="")
    cta_label = models.CharField(max_length=255, blank=True, default="Book Now")
    cta_url = models.TextField(blank=True, default="")

    class Meta:
        db_table = "health_dashboard_hero"

    def save(self, *args, **kwargs):
        self.image_url = normalize_image_payload(self.image_url)
        super().save(*args, **kwargs)


class HealthDashboardContact(TimeStampedUUIDModel):
    dashboard = models.OneToOneField(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="contact",
    )
    phone = models.CharField(max_length=64, blank=True, default="")
    email = models.CharField(max_length=255, blank=True, default="")
    address = models.TextField(blank=True, default="")

    class Meta:
        db_table = "health_dashboard_contact"


class HealthDashboardSeo(TimeStampedUUIDModel):
    dashboard = models.OneToOneField(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="seo",
    )
    title = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")

    class Meta:
        db_table = "health_dashboard_seo"


class HealthDashboardSeoKeyword(TimeStampedUUIDModel):
    seo = models.ForeignKey(
        HealthDashboardSeo,
        on_delete=models.CASCADE,
        related_name="keywords",
    )
    keyword = models.CharField(max_length=255)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "health_dashboard_seo_keyword"
        constraints = [
            models.UniqueConstraint(
                fields=["seo", "keyword", "sort_order"],
                name="health_dashboard_seo_keyword_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["seo", "sort_order"]),
        ]


class HealthDashboardFaq(TimeStampedUUIDModel):
    dashboard = models.ForeignKey(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="faqs",
    )
    question = models.TextField()
    answer = models.TextField(blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "health_dashboard_faq"
        indexes = [
            models.Index(fields=["dashboard", "sort_order"]),
        ]


class HealthDashboardCertification(TimeStampedUUIDModel):
    dashboard = models.ForeignKey(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="certifications",
    )
    value = models.TextField()
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "health_dashboard_certification"
        indexes = [
            models.Index(fields=["dashboard", "sort_order"]),
        ]


class HealthDashboardGalleryItem(TimeStampedUUIDModel):
    dashboard = models.ForeignKey(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="gallery_items",
    )
    media_url = models.TextField()
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "health_dashboard_gallery_item"
        indexes = [
            models.Index(fields=["dashboard", "sort_order"]),
        ]


class HealthDashboardSocialLink(TimeStampedUUIDModel):
    dashboard = models.ForeignKey(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="social_links",
    )
    url = models.TextField()
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "health_dashboard_social_link"
        indexes = [
            models.Index(fields=["dashboard", "sort_order"]),
        ]


class HealthDashboardInstitutionLandingPage(TimeStampedUUIDModel):
    dashboard = models.OneToOneField(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="landing_page",
    )
    title = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    hero_headline = models.CharField(max_length=255, blank=True, default="")
    hero_subheadline = models.TextField(blank=True, default="")
    hero_cta_label = models.CharField(max_length=120, blank=True, default="Book Appointment")
    hero_cta_url = models.TextField(blank=True, default="")
    logo_url = models.TextField(blank=True, default="")
    background_image_url = models.TextField(blank=True, default="")
    background_color_key = models.CharField(max_length=64, blank=True, default="")
    is_published = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="health_dashboard_landing_pages_created",
    )
    updated_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="health_dashboard_landing_pages_updated",
    )

    class Meta:
        db_table = "health_dashboard_institution_landing_page"
        indexes = [
            models.Index(fields=["dashboard", "is_published"]),
        ]

    def save(self, *args, **kwargs):
        self.logo_url = normalize_image_payload(self.logo_url)
        self.background_image_url = normalize_image_payload(self.background_image_url)
        super().save(*args, **kwargs)


class HealthDashboardLandingPageContact(TimeStampedUUIDModel):
    landing_page = models.OneToOneField(
        HealthDashboardInstitutionLandingPage,
        on_delete=models.CASCADE,
        related_name="contact",
    )
    primary_phone = models.CharField(max_length=64, blank=True, default="")
    secondary_phone = models.CharField(max_length=64, blank=True, default="")
    email = models.CharField(max_length=255, blank=True, default="")
    website_url = models.TextField(blank=True, default="")
    whatsapp_phone = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "health_dashboard_landing_page_contact"
        indexes = [
            models.Index(fields=["primary_phone"]),
            models.Index(fields=["email"]),
        ]


class HealthDashboardLandingPageAddress(TimeStampedUUIDModel):
    landing_page = models.OneToOneField(
        HealthDashboardInstitutionLandingPage,
        on_delete=models.CASCADE,
        related_name="address",
    )
    line_one = models.CharField(max_length=255, blank=True, default="")
    line_two = models.CharField(max_length=255, blank=True, default="")
    city = models.CharField(max_length=120, blank=True, default="")
    state = models.CharField(max_length=120, blank=True, default="")
    postal_code = models.CharField(max_length=32, blank=True, default="")
    country = models.CharField(max_length=120, blank=True, default="")
    landmark = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "health_dashboard_landing_page_address"
        indexes = [
            models.Index(fields=["city", "state", "country"]),
            models.Index(fields=["country", "postal_code"]),
        ]


class HealthDashboardLandingPageService(TimeStampedUUIDModel):
    landing_page = models.ForeignKey(
        HealthDashboardInstitutionLandingPage,
        on_delete=models.CASCADE,
        related_name="services",
    )
    institution_service = models.ForeignKey(
        HealthDashboardInstitutionService,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="landing_page_entries",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    price_cents = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "health_dashboard_landing_page_service"
        indexes = [
            models.Index(fields=["landing_page", "sort_order"]),
            models.Index(fields=["landing_page", "is_active"]),
        ]


class HealthDashboardLandingPageImage(TimeStampedUUIDModel):
    landing_page = models.ForeignKey(
        HealthDashboardInstitutionLandingPage,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image_url = models.TextField()
    caption = models.CharField(max_length=255, blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "health_dashboard_landing_page_image"
        indexes = [
            models.Index(fields=["landing_page", "sort_order"]),
        ]

    def save(self, *args, **kwargs):
        self.image_url = normalize_image_payload(self.image_url)
        super().save(*args, **kwargs)


class HealthDashboardLandingPageSocialLink(TimeStampedUUIDModel):
    landing_page = models.ForeignKey(
        HealthDashboardInstitutionLandingPage,
        on_delete=models.CASCADE,
        related_name="social_links",
    )
    platform = models.CharField(max_length=64, blank=True, default="")
    url = models.TextField()
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "health_dashboard_landing_page_social_link"
        indexes = [
            models.Index(fields=["landing_page", "sort_order"]),
        ]


class HealthDashboardLandingPageOperatingHour(TimeStampedUUIDModel):
    landing_page = models.ForeignKey(
        HealthDashboardInstitutionLandingPage,
        on_delete=models.CASCADE,
        related_name="operating_hours",
    )
    day_key = models.CharField(max_length=16)
    opens_at = models.CharField(max_length=16, blank=True, default="")
    closes_at = models.CharField(max_length=16, blank=True, default="")
    is_closed = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "health_dashboard_landing_page_operating_hour"
        indexes = [
            models.Index(fields=["landing_page", "sort_order"]),
        ]


class HealthDashboardOperatingHour(TimeStampedUUIDModel):
    dashboard = models.ForeignKey(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="operating_hours",
    )
    value = models.TextField()
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "health_dashboard_operating_hour"
        indexes = [
            models.Index(fields=["dashboard", "sort_order"]),
        ]


class HealthDashboardServiceVisibility(TimeStampedUUIDModel):
    dashboard = models.ForeignKey(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="service_visibilities",
    )
    service_uid = models.CharField(max_length=128, db_index=True)
    is_visible = models.BooleanField(default=True)

    class Meta:
        db_table = "health_dashboard_service_visibility"
        constraints = [
            models.UniqueConstraint(
                fields=["dashboard", "service_uid"],
                name="health_dashboard_service_visibility_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["dashboard", "service_uid"]),
        ]


class HealthDashboardSection(TimeStampedUUIDModel):
    dashboard = models.ForeignKey(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="sections",
    )
    section_uid = models.CharField(max_length=128, db_index=True)
    name = models.CharField(max_length=255)
    section_type = models.CharField(max_length=120)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "health_dashboard_section"
        constraints = [
            models.UniqueConstraint(
                fields=["dashboard", "section_uid"],
                name="health_dashboard_section_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["dashboard", "sort_order"]),
        ]


class SectionValueType(models.TextChoices):
    STRING = "string", "String"
    INTEGER = "integer", "Integer"
    FLOAT = "float", "Float"
    BOOLEAN = "boolean", "Boolean"
    NULL = "null", "Null"
    EMPTY_LIST = "empty_list", "Empty List"
    EMPTY_OBJECT = "empty_object", "Empty Object"


class HealthDashboardSectionField(TimeStampedUUIDModel):
    section = models.ForeignKey(
        HealthDashboardSection,
        on_delete=models.CASCADE,
        related_name="fields",
    )
    field_path = models.CharField(max_length=255, db_index=True)
    value_text = models.TextField(blank=True, default="")
    value_type = models.CharField(max_length=24, choices=SectionValueType.choices, default=SectionValueType.STRING)

    class Meta:
        db_table = "health_dashboard_section_field"
        constraints = [
            models.UniqueConstraint(
                fields=["section", "field_path"],
                name="health_dashboard_section_field_unique",
            ),
        ]


class AvailabilityStatus(models.TextChoices):
    AVAILABLE = "available", "Available"
    LIMITED = "limited", "Limited"
    FULLY_BOOKED = "fully_booked", "Fully Booked"
    ON_CALL = "on_call", "On Call"
    HOLIDAY = "holiday", "Holiday"
    BLOCKED = "blocked", "Blocked"


class HealthDashboardAvailabilityDay(TimeStampedUUIDModel):
    dashboard = models.ForeignKey(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="availability_days",
    )
    date_value = models.DateField(db_index=True)
    status = models.CharField(max_length=32, choices=AvailabilityStatus.choices, default=AvailabilityStatus.AVAILABLE)

    class Meta:
        db_table = "health_dashboard_availability_day"
        constraints = [
            models.UniqueConstraint(
                fields=["dashboard", "date_value"],
                name="health_dashboard_availability_day_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["dashboard", "date_value"]),
        ]


class HealthDashboardAvailabilityTime(TimeStampedUUIDModel):
    availability_day = models.ForeignKey(
        HealthDashboardAvailabilityDay,
        on_delete=models.CASCADE,
        related_name="times",
    )
    time_value = models.CharField(max_length=16)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "health_dashboard_availability_time"
        constraints = [
            models.UniqueConstraint(
                fields=["availability_day", "time_value", "sort_order"],
                name="health_dashboard_availability_time_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["availability_day", "sort_order"]),
        ]


class HealthDashboardAvailabilityService(TimeStampedUUIDModel):
    availability_day = models.ForeignKey(
        HealthDashboardAvailabilityDay,
        on_delete=models.CASCADE,
        related_name="services",
    )
    service_uid = models.CharField(max_length=128, db_index=True)

    class Meta:
        db_table = "health_dashboard_availability_service"
        constraints = [
            models.UniqueConstraint(
                fields=["availability_day", "service_uid"],
                name="health_dashboard_availability_service_unique",
            ),
        ]


class HealthDashboardServiceAvailability(TimeStampedUUIDModel):
    dashboard = models.ForeignKey(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="service_availability_rows",
    )
    service_uid = models.CharField(max_length=128, db_index=True)
    enabled = models.BooleanField(default=True)
    duration_min = models.PositiveIntegerField(default=30)
    slot_gap_min = models.PositiveIntegerField(default=10)

    class Meta:
        db_table = "health_dashboard_service_availability"
        constraints = [
            models.UniqueConstraint(
                fields=["dashboard", "service_uid"],
                name="health_dashboard_service_availability_unique",
            ),
        ]


class HealthDashboardBlockedTime(TimeStampedUUIDModel):
    dashboard = models.ForeignKey(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="blocked_times",
    )
    date_value = models.DateField(db_index=True)
    start_time = models.CharField(max_length=16)
    end_time = models.CharField(max_length=16)
    reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "health_dashboard_blocked_time"
        indexes = [
            models.Index(fields=["dashboard", "date_value"]),
        ]


class HealthDashboardRecurringRule(TimeStampedUUIDModel):
    dashboard = models.ForeignKey(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="recurring_rules",
    )
    day_key = models.CharField(max_length=16)
    frequency = models.CharField(max_length=32, default="weekly")
    start_time = models.CharField(max_length=16, blank=True, default="")
    end_time = models.CharField(max_length=16, blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "health_dashboard_recurring_rule"
        indexes = [
            models.Index(fields=["dashboard", "sort_order"]),
        ]


class HealthDashboardSlotTemplate(TimeStampedUUIDModel):
    dashboard = models.ForeignKey(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="slot_templates",
    )
    day_key = models.CharField(max_length=16)
    start_time = models.CharField(max_length=16)
    end_time = models.CharField(max_length=16)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "health_dashboard_slot_template"
        indexes = [
            models.Index(fields=["dashboard", "sort_order"]),
        ]


class HealthDashboardScheduleEntry(TimeStampedUUIDModel):
    dashboard = models.ForeignKey(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="schedule_entries",
    )
    title = models.CharField(max_length=255)
    service_uid = models.CharField(max_length=128, blank=True, default="", db_index=True)
    patient_name = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=32, default="scheduled")
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "health_dashboard_schedule_entry"
        indexes = [
            models.Index(fields=["dashboard", "starts_at"]),
            models.Index(fields=["dashboard", "status"]),
        ]


class HealthDashboardFinancialSummary(TimeStampedUUIDModel):
    dashboard = models.OneToOneField(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="financial_summary",
    )
    total_revenue_cents = models.BigIntegerField(default=0)
    insurance_revenue_cents = models.BigIntegerField(default=0)
    direct_revenue_cents = models.BigIntegerField(default=0)
    pending_payments_cents = models.BigIntegerField(default=0)
    refunds_cents = models.BigIntegerField(default=0)
    disputes_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "health_dashboard_financial_summary"


class HealthDashboardComplianceSummary(TimeStampedUUIDModel):
    dashboard = models.OneToOneField(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="compliance_summary",
    )
    audit_log_count = models.PositiveIntegerField(default=0)
    pending_credential_reviews = models.PositiveIntegerField(default=0)
    license_expiring_soon_count = models.PositiveIntegerField(default=0)
    active_consents = models.PositiveIntegerField(default=0)
    pending_documents = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "health_dashboard_compliance_summary"


class AnalyticsMetricType(models.TextChoices):
    BOOKING = "booking", "Booking"
    CONSULTATION = "consultation", "Consultation"
    SCHEDULE = "schedule", "Schedule"
    PAYMENT = "payment", "Payment"
    RATING = "rating", "Rating"
    TRAFFIC = "traffic", "Traffic"


class HealthDashboardAnalyticsRecord(TimeStampedUUIDModel):
    dashboard = models.ForeignKey(
        HealthDashboardInstitution,
        on_delete=models.CASCADE,
        related_name="analytics_records",
    )
    metric_type = models.CharField(max_length=32, choices=AnalyticsMetricType.choices, db_index=True)
    label = models.CharField(max_length=255, blank=True, default="")
    subject_name = models.CharField(max_length=255, blank=True, default="")
    payment_method = models.CharField(max_length=64, blank=True, default="")
    service_uid = models.CharField(max_length=128, blank=True, default="", db_index=True)
    patient_uid = models.CharField(max_length=128, blank=True, default="", db_index=True)
    value_decimal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount_cents = models.BigIntegerField(default=0)
    status = models.CharField(max_length=64, blank=True, default="")
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="health_dashboard_analytics_records",
    )

    class Meta:
        db_table = "health_dashboard_analytics_record"
        indexes = [
            models.Index(fields=["dashboard", "metric_type", "occurred_at"]),
            models.Index(fields=["dashboard", "occurred_at"]),
        ]
