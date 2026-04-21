# Project Audit Report
*Generated 2026-02-11; gathered via static code inspection because the environment lacks Django & dependency installs.*

## 1. Installed apps (per `config/settings/base.py`)
- Django defaults: `django.contrib.admin`, `auth`, `contenttypes`, `sessions`, `messages`, `staticfiles`
- Third-party: `rest_framework`, `drf_spectacular`, `django_extensions`, `django_celery_beat`, `django_celery_results`, `django_filters`
- Local: `apps.accounts`, `apps.core`, `apps.content`, `apps.media`, `apps.events`, `apps.notifications`, `apps.moderation`, `apps.ai_integration`, `apps.commerce`, `apps.surveys`, `apps.bridge`, `apps.analytics`, `apps.tiers`, `apps.otp`, `apps.background_removal`, `apps.statuses`, `apps.billing`, `apps.chat`, `apps.partners`, `apps.communities`, `apps.groups`, `apps.channels`, `apps.broadcasts`, `apps.feed_personalization`, `apps.bible`

## 2. Models per app (class names as defined in `apps/*/models.py`)
- `accounts`: `BaseEntity`, `UserManager`, `User`, `Profile`, `Session`, `Device`, `E2EDeviceKey`, `E2EPreKey`, `ApiToken`, `AccountTier`, `Subscription`, `UsageQuota`, `AuditLog`, `TwoFactor`, `BillingAccount`, `OrganizationLink`, `FeatureFlag`, `AIAccess`, `RevenueAccount`, `GDPRRequest`, `ImpactLedger`, `QuantumFlag`, `ARAccess`, `Experience`, `Education`, `UserSkill`, `Project`, `Recommendation`, `ProfileFieldVisibility`, `ProfileArticle`, `ProfilePreferences`, `ProfileShowcase`
- `ai_integration`: `BaseEntity`, `AIModel`, `AIJob`, `TranslationRequest`, `QnASession`, `AIPipeline`, `AIJobFeedback`, `AISchedule`, `Organization`, `Department`, `StaffProfile`, `PatientMaster`, `EncounterRecord`, `ConsentRecord`, `GroqAIRequestLog`
- `analytics`: `BaseEntity`, `Metric`, `EventStream`, `Dashboard`, `AppSetting`, `FeatureFlag`, `Alert`, `EngagementScore`, `ClinicalAnalyticsReport`, `RiskStratification`, `OutcomeBenchmark`, `PatientSatisfactionScore`, `OutreachCampaign`, `WellnessChallenge`, `HabitTrackingEntry`
- `background_removal`: `BackgroundRemovalJob`
- `bible`: *[multiple models focused on translations, devotions, courses, live sessions, quizzes, enrollments, etc.]* (see file for exhaustive list of ~60 classes)
- `billing`: `WalletAccount`, `CreditAccount`, `WalletLedgerEntry`, `WalletTransaction`, `PromoCode`, `PromoRedemption`, `BillingReconciliation`, `InsuranceClaim`, `PaymentDispute`
- `bridge`: `BaseEntity`, `BridgeAccount`, `BridgeThread`, `BridgeMessage`, `BridgeAutomation`, `BridgeAnalytics`
- `broadcasts`: `BroadcastSourceType`, `BroadcastItem`, `EducationProfileType`, `EducationProfile`, `EducationProfileCourse`, `EducationProfileModule`, `EducationProfileRole`, `EducationProfileRoleAssignment`, `BroadcastReaction`, `BroadcastFeature`, `BroadcastFeatureFlag`, `BroadcastVideo`, `BroadcastLesson`, `LessonEnrollmentStatus`, `LessonEnrollment`
- `channels`: `Channel`
- `chat`: `ConversationType`, `ConversationRequestState`, `ConversationSendPolicy`, `ConversationJoinPolicy`, `ConversationInfoEditPolicy`, `ConversationSubroomPolicy`, `Conversation`, `BaseConversationRole`, `ConversationNotificationLevel`, `ConversationMember`, `ConversationSettings`, `MessageThreadLink`, `RoleScopeType`, `PermissionDefinition`, `RoleDefinition`, `RolePermission`, `PrincipalRole`
- `commerce`: `BaseEntity`, `Shop`, `ShopVerificationRequest`, `Product`, `ProductAuthenticityCheck`, `Order`, `OrderItem`, `Payment`, `Promotion`, `Subscription`, `LoyaltyPoint`, `ShopFollow`, `ProductShare`, `ProductSubscription`, `AIRecommendation`, `AuditLog`, `FraudSignal`
- `communities`: `CommunityVisibility`, `CommunityJoinPolicy`, `CommunityPostPolicy`, `CommunityRole`, `Community`, `CommunityMembership`, `CommunityJoinRequestStatus`, `CommunityJoinRequest`, `CommunityBan`, `CommunityPostStatus`, `CommunityPost`, `CommunityPostComment`, `CommunityPostReaction`, `CommunityCommentReaction`
- `content`: `BaseEntity`, `Content`, `Comment`, `Share`, `Reaction`, `Tag`, `ContentTag`, `ContentView`, `ContentMetrics`, `ContentVariant`, `AIAnalysis`, `Provenance`, `Promotion`, `Tip`, `ModerationAction`, `ReactionBadge`
- `core`: numerous clinical/organization models (permissions, roles, community/group/channel management, healthcare resources, audit logs, regulatory docs, and patient-facing records such as `PatientMasterRecord`, `MedicationOrder`, `Appointment`, `TelemedicineSession`, etc.)
- `events`: `BaseEntity`, `Event`, `EventSession`, `Venue`, `SeatMap`, `Seat`, `Ticket`, `TicketVariant`, `TicketSale`, `Refund`, `Waitlist`, `HybridStream`, `CheckInDevice`, `Beacon`, `Attendance`, `Sponsor`, `SponsorSlot`, `InsurancePolicy`, `SmartContract`, `FraudScore`, `Poll`, `QnA`, `LiveTranscript`, `Highlights`, `AttendanceCertificate`, `CEApproval`, `MatchmakingProfile`, `NetworkingSession`, `EventAIAnalysis`
- `feed_personalization`: `FeedAffinityProfile`, `FeedInteraction`
- `groups`: `GroupJoinPolicy`, `GroupRole`, `Group`, `GroupMembership`, `GroupJoinRequestStatus`, `GroupJoinRequest`, `GroupBan`
- `media`: `BaseEntity`, `MediaAsset`, `MediaVariant`, `ProcessingJob`, `Provenance`, `Watermark`, `AccessPolicy`, `MediaMetrics`
- `moderation`: `BaseEntity`, `Flag`, `ModerationAction`, `AuditLog`, `UserReputation`, `UserBlock`, `ModerationRule`, `SafetyAlert`
- `notifications`: `BaseEntity`, `NotificationTemplate`, `Notification`, `NotificationRule`, `NotificationDelivery`, `NotificationDigest`
- `otp`: `PhoneOTP`
- `partners`: `Partner`, `PartnerJoinConfig`, `PartnerMembershipStatus`, `PartnerMembership`, `PartnerJobPost`, `PartnerApplicationStatus`, `PartnerApplication`, `PartnerFeatureFlag`, `PartnerPost`, `PartnerPostComment`, `PartnerPostReaction`, `PartnerPolicy`, `PartnerRole`, `PartnerRoleAssignment`, `PartnerAuditEvent`, `PartnerIntegration`, `PartnerWebhook`, `PartnerWebhookDeliveryStatus`, `PartnerWebhookDelivery`, `PartnerAutomationRule`, `PartnerReportSnapshot`, `PartnerExportStatus`, `PartnerExportJob`, `PartnerAccessRequestStatus`, `PartnerAccessRequest`, `PartnerAccessReviewStatus`, `PartnerAccessReview`, `PartnerExportScheduleFrequency`, `PartnerExportSchedule`, `PartnerSetting`, `PartnerOrganizationProfile`, `PartnerOrganizationAppType`, `PartnerOrganizationApp`, `PartnerOrganizationAppAccessLog`, `PartnerProfileLink`
- `statuses`: `StatusType`, `StatusItem`, `StatusItemView`
- `surveys`: `BaseEntity`, `SurveyType`, `Visibility`, `VoteType`, `Survey`, `Question`, `Response`, `SurveyShare`, `SurveyAnalytics`
- `tiers`: `BaseEntity`, `User`, `Organization`, `BillingPlan`, `Subscription`, `Entitlement`, `UsageQuota`, `BillingInvoice`, `FeatureFlag`, `PlanFeature`, `PartnerSettings`, `ImpactAnalyticsSettings`, `DonationCampaign`, `EventTicketing`, `HolographicRoom`, `QuantumEncryptionSetting`, `CustomAIModel`

*Note: the database schema is wide; some apps manage smaller sets (e.g., `background_removal`, `otp`, `channels`) while others (e.g., `bible`, `core`) include dozens of tables. Where underscores indicate base/utility classes, there are additional manager/helper classes defined in other modules.*

## 3. Existing analytics & observability de-duplication
- `apps.analytics` (models: `Metric`, `EventStream`, `Dashboard`, `EngagementScore`, `ClinicalAnalyticsReport`, etc.; README mentions streaming ingestion, dashboards stored as SQL/DSL, Celery tasks). It already exposes serializers, tasks, signals, and views under `apps/analytics/`.
- `apps.feed_personalization` stores affinity profiles + interactions per user (useful for personalization signals).
- `apps.ai_integration`, `apps.core`, `apps.billing`, `apps.chat`, and `apps.events` include domain-specific usage/event tables that can feed analytics (AuditLog, FraudSignal, Notifications, Chat roles/permissions, Bookings). There is also `apps.bible` course/lesson models that capture completions.
- Celery is configured (`django_celery_beat`, `django_celery_results`, `config/celery.py` auto discovers tasks). Existing tasks include `apps/analytics/tasks.py` (needs detail). The project currently logs to console and uses JSON-friendly log handlers defined in `config/settings/base.py` `LOGGING` section.

## 4. Existing logging & middleware
- Logging config: base settings define single `console` handler with simple formatter, root logger level from `LOG_LEVEL` env (default DEBUG). No file/structured logging yet beyond console.
- Custom middleware: `common.middleware.RequestLoggingMiddleware` (starts timer/logs before/after requests) and `QuotaEnforcementMiddleware` (auth-only checks for `/api/v1/ai/` POST/PUT). Both registered in `MIDDLEWARE` after Django defaults.
- Audit logging: `apps/accounts.models.AuditLog` (used by signals), `apps.commerce.AuditLog`, `moderation.AuditLog`, `partners.PartnerAuditEvent`, etc., already persist action history.
- Signals: there are signal modules in `apps/{commerce,core,chat,bridge,content,tiers,accounts,partners,surveys,events,notifications,billing,analytics,media}` each hooking into Django lifecycle (post_save/post_delete). Example: `apps/accounts/signals.py` handles profile creation & audit entries.

## 5. Middleware list (from settings)
1. `django.middleware.security.SecurityMiddleware`
2. `django.contrib.sessions.middleware.SessionMiddleware`
3. `django.middleware.common.CommonMiddleware`
4. `django.middleware.csrf.CsrfViewMiddleware`
5. `django.contrib.auth.middleware.AuthenticationMiddleware`
6. `django.contrib.messages.middleware.MessageMiddleware`
7. `django.middleware.clickjacking.XFrameOptionsMiddleware`
8. `common.middleware.RequestLoggingMiddleware`
9. `common.middleware.QuotaEnforcementMiddleware`

## 6. Authentication stack
- Custom user model: `AUTH_USER_MODEL = "accounts.User"` with a rich `UserManager` enforcing phone/email normalization, separate flows for regular vs. superusers, and helper methods for entitlements, tokens, etc.
- Custom authentication backends: `apps.accounts.auth_backends.PhoneOrEmailBackend` plus Django default `ModelBackend`.
- REST: DRF default permission `IsAuthenticated`, JWT auth (`DeviceBoundJWTAuthentication`, session auth for browsable API), `SimpleJWT` configured with env-driven lifetimes (`JWT_ACCESS_MINUTES`, `JWT_REFRESH_DAYS`), `ACCESS_TOKEN`/`REFRESH_TOKEN` endpoints under `/api/v1/auth/jwt/` plus verify endpoint.

## 7. API structure
- Root router (`config/urls.py`) mounts each app under `api/v1/`, with a few namespaced ones (`chat`, `partners`, `communities`, `groups`, `channels`, `broadcasts`, `bible`, `feed-personalization`, `background_removal`, `statuses`, `billing`).
- Additional uploads endpoint (`/uploads/file`).
- JWT token endpoints and drf-spectacular schema + Swagger/Redoc.
- `rest_framework` setup includes pagination, filters, search, ordering via `common.pagination.StandardResultsSetPagination` and `DjangoFilterBackend`.

## 8. Infrastructure
- Database: default `sqlite3` (`BASE_DIR / db.sqlite3`); environment-specific overrides expected in `config/settings/local.py` or production.
- Caching: `LocMemCache` (non-shared; keyed by `unique-snowflake`).
- Celery: broker/res result backend default to Redis on 10.14.20.99 (env overrides via `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND`). Auto-loads tasks from `apps/*/tasks.py`.
- Static/media roots configured for `staticfiles` and `media` directories.

## 9. Additional notes
- Current analytics tooling is app-based; there is no standalone admin observability UI beyond DRF/Swagger.
- Logging is console-only; no dedicated audit viewer yet (AuditLog tables exist but no UI).
- Signals exist across most feature apps, so new admin actions should respect those hooks (e.g., AuditLog, partner events).
- Project strongly relies on DRF + Celery; any admin system should integrate with existing routers, tasks, and authentication settings.
