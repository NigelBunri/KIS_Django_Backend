class VerificationSubjectType:
    USER = "user"
    SHOP = "shop"
    HEALTH_INSTITUTION = "health_institution"
    EDUCATION_INSTITUTION = "education_institution"
    PARTNER = "partner"

    CHOICES = (
        (USER, "User"),
        (SHOP, "Shop"),
        (HEALTH_INSTITUTION, "Health Institution"),
        (EDUCATION_INSTITUTION, "Education Institution"),
        (PARTNER, "Partner"),
    )


class VerificationCaseStatus:
    DRAFT = "draft"
    SUBMITTED = "submitted"
    IN_REVIEW = "in_review"
    PROVIDER_PENDING = "provider_pending"
    NEEDS_MORE_INFO = "needs_more_info"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

    CHOICES = (
        (DRAFT, "Draft"),
        (SUBMITTED, "Submitted"),
        (IN_REVIEW, "In Review"),
        (PROVIDER_PENDING, "Provider Pending"),
        (NEEDS_MORE_INFO, "Needs More Info"),
        (APPROVED, "Approved"),
        (REJECTED, "Rejected"),
        (EXPIRED, "Expired"),
        (CANCELLED, "Cancelled"),
    )


class VerificationCheckStatus:
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    SKIPPED = "skipped"
    EXPIRED = "expired"

    CHOICES = (
        (PENDING, "Pending"),
        (PASSED, "Passed"),
        (FAILED, "Failed"),
        (NEEDS_REVIEW, "Needs Review"),
        (SKIPPED, "Skipped"),
        (EXPIRED, "Expired"),
    )


class VerificationBadgeStatus:
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"

    CHOICES = (
        (ACTIVE, "Active"),
        (REVOKED, "Revoked"),
        (EXPIRED, "Expired"),
    )


class VerificationBadgeCode:
    VERIFIED_USER = "verified_user"
    ID_VERIFIED = "id_verified"
    VERIFIED_PROFESSIONAL = "verified_professional"
    VERIFIED_SHOP = "verified_shop"
    TRUSTED_MERCHANT = "trusted_merchant"
    VERIFIED_HEALTH_INSTITUTION = "verified_health_institution"
    LICENSED_PROVIDER = "licensed_provider"
    VERIFIED_EDUCATION_INSTITUTION = "verified_education_institution"
    ACCREDITED_EDUCATION = "accredited_education"
    VERIFIED_PARTNER = "verified_partner"
    VERIFIED_ORGANIZATION = "verified_organization"
    OFFICIAL_PARTNER = "official_partner"


PUBLIC_BADGE_LABELS = {
    VerificationBadgeCode.VERIFIED_USER: "Verified user",
    VerificationBadgeCode.ID_VERIFIED: "ID verified",
    VerificationBadgeCode.VERIFIED_PROFESSIONAL: "Verified professional",
    VerificationBadgeCode.VERIFIED_SHOP: "Verified shop",
    VerificationBadgeCode.TRUSTED_MERCHANT: "Trusted merchant",
    VerificationBadgeCode.VERIFIED_HEALTH_INSTITUTION: "Verified health institution",
    VerificationBadgeCode.LICENSED_PROVIDER: "Licensed provider",
    VerificationBadgeCode.VERIFIED_EDUCATION_INSTITUTION: "Verified education institution",
    VerificationBadgeCode.ACCREDITED_EDUCATION: "Accredited",
    VerificationBadgeCode.VERIFIED_PARTNER: "Verified partner",
    VerificationBadgeCode.VERIFIED_ORGANIZATION: "Verified organization",
    VerificationBadgeCode.OFFICIAL_PARTNER: "Official partner",
}
