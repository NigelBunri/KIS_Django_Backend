from django.db import models
from django.conf import settings

CATEGORY_CHOICES = [
    ("health",        "Health & Medical"),
    ("finances",      "Financial Hardship"),
    ("relationships", "Relationships & Marriage"),
    ("faith",         "Faith & Spirituality"),
    ("business",      "Business & Career"),
    ("grief",         "Loss & Grief"),
    ("addiction",     "Addiction & Recovery"),
    ("family",        "Family & Parenting"),
    ("mental_health", "Mental Health"),
    ("other",         "Other"),
]

VISIBILITY_CHOICES = [
    ("public",           "Public"),
    ("testimony_only",   "Visible to testimony holders only"),
    ("private",          "Private"),
]


class UserSeason(models.Model):
    """Something a user is currently going through and is open about."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="seasons")
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    visibility = models.CharField(max_length=20, choices=VISIBILITY_CHOICES, default="public")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["category", "is_active"]),
            models.Index(fields=["user", "is_active"]),
        ]


class UserTestimony(models.Model):
    """Something a user has overcome and is willing to speak into."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="testimonies")
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    title = models.CharField(max_length=200)
    story = models.TextField(blank=True, default="")
    is_available = models.BooleanField(default=True, help_text="Willing to be contacted about this")
    endorsement_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["category", "is_available"]),
        ]


class TestimonyEndorsement(models.Model):
    """Community member endorses a testimony as genuine."""
    testimony = models.ForeignKey(UserTestimony, on_delete=models.CASCADE, related_name="endorsements_set")
    endorsed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="given_endorsements")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("testimony", "endorsed_by")


class TestimonyReach(models.Model):
    """A testimony holder reaching out to someone in a matching season."""
    STATUS_PENDING  = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_DECLINED = "declined"
    STATUS_CHOICES = [
        (STATUS_PENDING,  "Pending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_DECLINED, "Declined"),
    ]

    from_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_reaches")
    to_user   = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="received_reaches")
    season    = models.ForeignKey(UserSeason,   on_delete=models.CASCADE, related_name="reaches")
    testimony = models.ForeignKey(UserTestimony, on_delete=models.CASCADE, related_name="reaches")
    message   = models.TextField(blank=True, default="")
    status    = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("from_user", "season")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["to_user", "status"]),
        ]
