# Education System Progress

> Active integration planning now continues in:
> - [docs/education-system-integration-plan.md](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-integration-plan.md)
> - [docs/education-system-phase-progress.md](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-phase-progress.md)

## Audit Summary

### Current state
- The current education feature is split across two different systems inside `apps/broadcasts`.
- The stronger system is the table-backed education profile stack:
  - `EducationProfile`
  - `EducationProfileCourse`
  - `EducationProfileModule`
  - `EducationProfileRole`
  - `EducationProfileRoleAssignment`
- A weaker legacy path still exists through the profile blob returned as `profiles["education"]`.
- The backend currently emits both:
  - `education_profiles`
  - `education`
- That duplication is the main architectural weakness and violates the "one source of truth" requirement.

### Real source-of-truth decision
- The legacy `profiles["education"]` JSON blob is **not** the long-term source of truth.
- The education domain must become institution-rooted and table-backed.
- Effective Phase 1 source-of-truth direction:
  - `EducationInstitution`
  - `EducationInstitutionMembership`
- Existing `EducationProfile` remains useful, but it should evolve into a public education profile / broadcast presentation layer rather than the root operational data model.

### Duplicated or legacy implementations
- Legacy/duplicated backend flow:
  - `ProfileCreationView._handle_education_profile`
  - `ProfileManagementView._update_education`
  - `_build_education_summary`
- Stronger table-backed backend flow:
  - `EducationProfileListView`
  - `EducationProfileDetailView`
  - `EducationProfileBroadcastView`
- The system currently has no first-class institution root object, which is why courses, memberships, applications, and dashboard behavior are not coherently modeled.

### What is good
- There is already a meaningful education foundation in the database.
- Education profile CRUD already exists.
- Education course/module/role tables exist.
- Broadcast lessons and lesson enrollments already exist.
- This gives a workable base to extend instead of starting from zero.

### What is bad
- No first-class institution model.
- No institution membership/application system.
- No institution-level roles for owner/manager/admin/lecturer/student workflows.
- Main education behavior has been partially shaped like a profile editor instead of an academic operations system.
- Legacy profile JSON continues to compete with table-backed entities.
- Existing education models do not yet express the user's workflow:
  - institutions
  - institution dashboard
  - programs/departments
  - classes
  - exams
  - bookable paid learning offers
  - membership approval pipelines

### Missing capabilities
- Institutions
- Institution dashboard foundation
- Institution membership and approval workflow
- Academic hierarchy:
  - programs/departments
  - courses
  - lessons
  - class sessions
- Assessments:
  - MCQ exams
  - theory/structured exams
- Learning operations:
  - attendance
  - grading
  - student progress
  - certificates
- Commercial workflow:
  - booking
  - seat limits
  - payment tracking
  - refunds
  - waitlists
- Governance:
  - audit logs
  - moderation
  - role-based permissions
  - institution settings
  - branding
- Planning:
  - timetable
  - calendar
  - reminders
  - reporting

## Architecture Decisions

### Decision 1: institution becomes the root aggregate
- All future education operations should hang off `EducationInstitution`.
- Institutions are the operational root for ownership, governance, staffing, students, enrollments, scheduling, and payments.

### Decision 2: keep existing education profiles temporarily, but downgrade their role
- `EducationProfile` is retained for compatibility and later use as public-facing education identity / broadcast presentation.
- It should not remain the primary operational model for institutional management.

### Decision 3: do not extend the legacy `profiles["education"]` blob
- New development should not add more behavior to the legacy JSON summary.
- Existing clients can continue to consume it temporarily, but all new domain growth should be table-backed.

### Decision 4: phased migration over rewrite
- This system is too large for a single rewrite.
- The migration path is:
  1. add institution foundation
  2. add academic structure
  3. attach broadcasts to structured education entities
  4. add student application, enrollment, and payment flows
  5. add analytics, compliance, and reporting

## Phase Plan

### Phase 1
- Add institution and membership foundation.
- Add safe CRUD endpoints for institution management.
- Add membership application / approval workflow foundation.
- Create persistent architecture progress file.

### Phase 2
- Add academic structure:
  - departments/programs
  - courses
  - lessons
  - scheduled classes
  - materials
- Define institution dashboard contracts.

### Phase 3
- Add assessment domain:
  - exam containers
  - MCQ items
  - theory submissions
  - grading foundations

### Phase 4
- Add structured education broadcast entities and contracts.
- Support broadcasting:
  - course
  - lesson
  - class
  - training session
  - event

### Phase 5
- Add student membership application, approval, enrollment, and booking flows.
- Add paid access and reservation support.

### Phase 6
- Add analytics, attendance, certificates, progress tracking, notifications, and reporting.

## Phase 1 Completed Tasks
- Audited existing education architecture.
- Identified the legacy-vs-table-backed duplication.
- Established institution-rooted direction as the target source of truth.
- Added `EducationInstitution` model.
- Added `EducationInstitutionMembership` model.
- Added institution API endpoints for:
  - list/create institutions
  - get/update institution details
  - list/apply/invite memberships
  - approve/reject/remove memberships
- Generated migration `0020_educationinstitution_educationinstitutionmembership_and_more.py`.

## Pending Tasks
- Link `EducationProfile` to `EducationInstitution`.
- Add enrollment and payment models tied to institution-owned offerings.
- Add dashboards and analytics based on real education data.
- Deprecate frontend reliance on legacy `profiles["education"]`.

## Phase 2 Completed Tasks
- Added institution-owned academic entities:
  - `EducationInstitutionProgram`
  - `EducationInstitutionCourse`
  - `EducationInstitutionLesson`
  - `EducationInstitutionClassSession`
  - `EducationInstitutionMaterial`
- Added management APIs for:
  - programs
  - courses
  - lessons
  - class sessions
  - materials
- Added institution relationship validation for:
  - course-to-lesson consistency
  - course/lesson links on materials
  - course/lesson links on class sessions
- Generated migration `0021_educationinstitutioncourse_and_more.py`.

## Phase 3 Completed Tasks
- Added institution-owned assessment entities:
  - `EducationInstitutionAssessment`
  - `EducationInstitutionAssessmentQuestion`
  - `EducationInstitutionAssessmentOption`
  - `EducationInstitutionAssessmentSubmission`
  - `EducationInstitutionAssessmentResponse`
  - `EducationInstitutionAssessmentResponseOption`
- Added assessment management APIs for:
  - assessments
  - questions
  - options
- Added learner submission APIs for:
  - create attempt
  - save answers
  - submit attempt
  - list/view own attempts
- Added manager grading flow for:
  - theory/manual grading
  - automatic MCQ scoring on submit
  - total score recomputation
- Generated migration `0022_educationinstitutionassessment_and_more.py`.

## Phase 4 Completed Tasks
- Added institution-owned event/training-session entity:
  - `EducationInstitutionEvent`
- Added structured education broadcast entity:
  - `EducationInstitutionBroadcast`
- Added structured broadcast support for:
  - course
  - lesson
  - class session
  - training session
  - event
- Added institution event APIs.
- Added institution broadcast APIs.
- Added education broadcast catalog endpoint for the education tab/discovery surface.
- Wired structured education broadcasts into the general `BroadcastItem` feed index using `source_type=education_broadcast`.
- Generated migration `0023_alter_broadcastitem_source_type_and_more.py`.

## Phase 5 Completed Tasks
- Added institution-owned enrollment entity:
  - `EducationInstitutionEnrollment`
- Added institution-owned booking and payment-tracking entity:
  - `EducationInstitutionBooking`
- Added broadcast-card interaction support for:
  - membership application/join from the card
  - enrollment from the card
  - booking/reservation from the card
  - payment initiation from the card
- Implemented membership-policy-aware access behavior:
  - `open` auto-joins active student membership
  - `application` creates pending membership application
  - `closed` rejects self-service join/enroll/book flows
- Added viewer-state hydration to broadcast detail and education catalog responses so the client can render:
  - current membership state
  - current enrollment state
  - current booking/payment state
- Added booking payment flows for:
  - wallet balance
  - mocked card payment
  - Flutterwave payment-link initiation
- Added waitlist behavior for:
  - enrollment seat limits
  - booking seat limits
- Generated migration `0024_educationinstitutionenrollment_and_more.py`.

## Additional Architecture Decision
- Do not make the Education profile page a monolithic management surface.
- Follow the marketplace system structure instead:
  - the Education profile page should act as a discovery and entry hub
  - institution creation/listing should live at the hub level
  - each institution should open into its own dedicated dashboard/module
  - institution dashboard sections should be split into focused management areas such as:
    - overview
    - courses
    - lessons
    - classes
    - exams
    - students
    - staff
    - memberships
    - broadcasts
    - bookings/payments
    - analytics
- New education work should be added to institution-scoped modules, not piled into a single Education profile screen.
- Added shared backend contracts to support this split:
  - `GET /broadcasts/education/hub/`
  - `GET /broadcasts/education/institutions/<institution_id>/dashboard/`
- Frontend correction applied in the app workspace:
  - the main Education profile page should show:
    - create institution entry
    - institution list
    - institution dashboard preview
  - institution creation/editing should happen on a separate in-flow screen
  - landing-page visibility should default to private
  - institution-level controls should include:
    - landing page
    - public/private toggle
    - edit
    - delete
    - logo/image fields

## API Contracts

### `GET /broadcasts/education/institutions/`
- Returns all institutions the authenticated user belongs to.
- Response:
  - `institutions: EducationInstitution[]`

### `GET /broadcasts/education/hub/`
- Returns the marketplace-style education hub payload for the authenticated user.
- Response:
  - `institutions: EducationInstitution[]`
  - `quick_stats`
    - `institution_count`
    - `active_member_count`
    - `pending_application_count`
    - `published_broadcast_count`
  - `recent_broadcasts: EducationInstitutionBroadcast[]`

### `POST /broadcasts/education/institutions/`
- Creates a new institution.
- Request:
  - `name: string` required
  - `description?: string`
  - `institution_type?: string`
  - `membership_policy?: open|application|closed`
  - `contact_email?: string`
  - `contact_phone?: string`
  - `branding?: object`
  - `settings?: object`
  - `metadata?: object`
- Side effect:
  - creator becomes active `owner`

### `GET /broadcasts/education/institutions/<institution_id>/`
- Returns one accessible institution with membership context.

### `GET /broadcasts/education/institutions/<institution_id>/dashboard/`
- Returns the dedicated institution dashboard payload.
- Response:
  - `institution: EducationInstitution`
  - `current_membership: EducationInstitutionMembership | null`
  - `metrics`
    - `program_count`
    - `course_count`
    - `lesson_count`
    - `class_session_count`
    - `material_count`
    - `assessment_count`
    - `event_count`
    - `broadcast_count`
    - `enrollment_count`
    - `booking_count`
    - `active_student_count`
    - `staff_count`
    - `pending_application_count`
  - `modules`
  - `recent_courses`
  - `recent_broadcasts`

### `PATCH /broadcasts/education/institutions/<institution_id>/`
- Allowed for active:
  - `owner`
  - `manager`
  - `administrator`
- Supports updating:
  - name
  - description
  - institution_type
  - membership_policy
  - contact data
  - branding/settings/metadata
  - `is_active`

### `GET /broadcasts/education/institutions/<institution_id>/memberships/`
- Managers see all memberships.
- Non-managers see active memberships only.

### `POST /broadcasts/education/institutions/<institution_id>/memberships/`
- Self-join behavior:
  - `open` -> active student membership
  - `application` -> pending student membership
  - `closed` -> rejected at API level
- Manager behavior:
  - may add/update another user membership with explicit role/status

### `POST /broadcasts/education/institutions/<institution_id>/memberships/<membership_id>/action/`
- Request:
  - `action: approve|reject|remove`
- Allowed for active:
  - `owner`
  - `manager`
  - `administrator`

### `GET|POST /broadcasts/education/institutions/<institution_id>/programs/`
- `GET` lists institution programs.
- `POST` creates a program with:
  - `title`
  - `code`
  - `summary`
  - `description`
  - `status`
  - `metadata`

### `GET|PATCH|DELETE /broadcasts/education/institutions/<institution_id>/programs/<program_id>/`
- Manages a single institution program.

### `GET|POST /broadcasts/education/institutions/<institution_id>/courses/`
- `GET` lists institution-owned courses.
- `POST` creates a course with:
  - `program_id?`
  - `title`
  - `code`
  - `summary`
  - `description`
  - `status`
  - `duration_minutes`
  - `seat_limit`
  - `metadata`
  - `settings`

### `GET|PATCH|DELETE /broadcasts/education/institutions/<institution_id>/courses/<course_id>/`
- Manages a single institution course.

### `GET|POST /broadcasts/education/institutions/<institution_id>/lessons/`
- `GET` lists lessons and supports optional `course_id` filtering.
- `POST` creates a lesson with:
  - `course_id`
  - `title`
  - `summary`
  - `content`
  - `lesson_order`
  - `duration_minutes`
  - `is_preview`
  - `status`
  - `metadata`

### `GET|PATCH|DELETE /broadcasts/education/institutions/<institution_id>/lessons/<lesson_id>/`
- Manages a single institution lesson.

### `GET|POST /broadcasts/education/institutions/<institution_id>/class-sessions/`
- `GET` lists class sessions and supports optional `course_id` filtering.
- `POST` creates a class session with:
  - `course_id?`
  - `lesson_id?`
  - `title`
  - `summary`
  - `starts_at`
  - `ends_at`
  - `timezone_name`
  - `delivery_mode`
  - `location_text`
  - `meeting_url`
  - `seat_limit`
  - `status`
  - `metadata`

### `GET|PATCH|DELETE /broadcasts/education/institutions/<institution_id>/class-sessions/<session_id>/`
- Manages a single institution class session.

### `GET|POST /broadcasts/education/institutions/<institution_id>/materials/`
- `GET` lists materials and supports optional `course_id` and `lesson_id` filtering.
- `POST` creates a material with:
  - `course_id?`
  - `lesson_id?`
  - `title`
  - `summary`
  - `kind`
  - `resource_url`
  - `storage_path`
  - `is_downloadable`
  - `status`
  - `metadata`

### `GET|PATCH|DELETE /broadcasts/education/institutions/<institution_id>/materials/<material_id>/`
- Manages a single institution material.

### `GET|POST /broadcasts/education/institutions/<institution_id>/assessments/`
- `GET` lists institution assessments.
- `POST` creates an assessment with:
  - `course_id?`
  - `lesson_id?`
  - `class_session_id?`
  - `title`
  - `summary`
  - `instructions`
  - `assessment_type`
  - `status`
  - `starts_at?`
  - `ends_at?`
  - `duration_minutes`
  - `max_attempts`
  - `passing_score_percent`
  - `metadata`
  - `settings`

### `GET|PATCH|DELETE /broadcasts/education/institutions/<institution_id>/assessments/<assessment_id>/`
- Manages a single institution assessment.

### `GET|POST /broadcasts/education/institutions/<institution_id>/assessments/<assessment_id>/questions/`
- `GET` lists assessment questions.
- `POST` creates a question with:
  - `prompt`
  - `question_type`
  - `question_order`
  - `points`
  - `is_required`
  - `metadata`

### `GET|PATCH|DELETE /broadcasts/education/institutions/<institution_id>/assessments/<assessment_id>/questions/<question_id>/`
- Manages a single assessment question.

### `GET|POST /broadcasts/education/institutions/<institution_id>/assessments/<assessment_id>/questions/<question_id>/options/`
- `GET` lists question options.
- `POST` creates an option with:
  - `option_text`
  - `option_order`
  - `is_correct`
  - `explanation`

### `GET|PATCH|DELETE /broadcasts/education/institutions/<institution_id>/assessments/<assessment_id>/questions/<question_id>/options/<option_id>/`
- Manages a single question option.

### `GET|POST /broadcasts/education/institutions/<institution_id>/assessments/<assessment_id>/submissions/`
- `GET` lists submissions.
- Managers see all submissions.
- Non-managers see only their own submissions.
- `POST` creates a new attempt if the learner is eligible and has remaining attempts.

### `GET|PATCH /broadcasts/education/institutions/<institution_id>/assessments/<assessment_id>/submissions/<submission_id>/`
- `GET` returns one submission.
- `PATCH` lets the learner save draft answers while the submission is still `started`.
- Supports response rows with:
  - `question_id`
  - `answer_text?`
  - `selected_option_ids?`
  - `metadata?`

### `POST /broadcasts/education/institutions/<institution_id>/assessments/<assessment_id>/submissions/<submission_id>/action/`
- Supports:
  - `action=submit`
  - `action=grade`
- `submit`
  - learner-only
  - auto-scores MCQ-only assessments
- `grade`
  - manager-only
  - supports manual scoring/feedback for responses
  - recalculates total result

### `GET|POST /broadcasts/education/institutions/<institution_id>/events/`
- `GET` lists institution events and training sessions.
- `POST` creates an event/training session with:
  - `event_type`
  - `title`
  - `summary`
  - `description`
  - `starts_at`
  - `ends_at`
  - `timezone_name`
  - `delivery_mode`
  - `location_text`
  - `meeting_url`
  - `seat_limit`
  - `status`
  - `metadata`

### `GET|PATCH|DELETE /broadcasts/education/institutions/<institution_id>/events/<event_id>/`
- Manages a single institution event or training session.

### `GET|POST /broadcasts/education/institutions/<institution_id>/broadcasts/`
- `GET` lists structured education broadcasts for the institution.
- `POST` creates a structured education broadcast with:
  - `broadcast_kind`
  - `course_id?`
  - `lesson_id?`
  - `class_session_id?`
  - `event_id?`
  - `title?`
  - `summary?`
  - `description?`
  - `cover_image_url?`
  - `seat_limit?`
  - `booking_enabled?`
  - `price_amount?`
  - `price_currency?`
  - `status?`
  - `expires_at?`
  - `metadata?`

### `GET|PATCH|DELETE /broadcasts/education/institutions/<institution_id>/broadcasts/<broadcast_id>/`
- Manages one structured education broadcast.
- Updates also resync the underlying `BroadcastItem` feed index.

### `GET /broadcasts/education/catalog/`
- Returns published structured education broadcast cards across institutions for the education tab/discovery view.
- Each card now also returns `viewer_state` for the authenticated user.

### `POST /broadcasts/education/institutions/<institution_id>/broadcasts/<broadcast_id>/membership/`
- Public card action for authenticated users.
- Behavior:
  - `open` institution -> active student membership
  - `application` institution -> pending student membership
  - `closed` institution -> rejected
- Response:
  - `membership: EducationInstitutionMembership`

### `GET|POST /broadcasts/education/institutions/<institution_id>/broadcasts/<broadcast_id>/enrollments/`
- `GET`
  - managers see all enrollments
  - non-managers see only their own enrollment rows
- `POST`
  - creates an enrollment from the broadcast card
  - requires membership access according to institution policy
  - auto-links to the broadcast's course, lesson, class session, or event
  - can return `waitlisted` if the enrollment seat limit is exhausted

### `GET|POST /broadcasts/education/institutions/<institution_id>/broadcasts/<broadcast_id>/bookings/`
- `GET`
  - managers see all bookings
  - non-managers see only their own booking rows
- `POST`
  - creates a booking/reservation from the broadcast card
  - requires membership access according to institution policy
  - respects `booking_enabled`
  - computes booking amount from `price_amount * seat_count`
  - returns:
    - `confirmed` for free broadcasts
    - `pending` for payable broadcasts
    - `waitlisted` when seat capacity is exhausted

### `POST /broadcasts/education/institutions/<institution_id>/broadcasts/<broadcast_id>/bookings/<booking_id>/pay/`
- Initiates or completes payment for a booking.
- Request:
  - `payment_method: wallet|card`
  - `mock?: boolean`
- Behavior:
  - `wallet` debits internal wallet balance and confirms the booking immediately
  - `card + mock=true` confirms immediately for dev/test
  - `card` creates a Flutterwave payment link and marks the booking `payment_pending`
- Response:
  - `booking: EducationInstitutionBooking`
  - `tx_ref: string`
  - `status: success|pending`
  - `payment_url?: string`

## Database Relationships

### New
- `EducationInstitution.owner -> User`
- `EducationInstitution.profile -> Profile` nullable
- `EducationInstitutionMembership.institution -> EducationInstitution`
- `EducationInstitutionMembership.user -> User`
- Unique membership constraint:
  - `(institution, user)`

### Phase 2 additions
- `EducationInstitutionProgram.institution -> EducationInstitution`
- `EducationInstitutionCourse.institution -> EducationInstitution`
- `EducationInstitutionCourse.program -> EducationInstitutionProgram` nullable
- `EducationInstitutionLesson.institution -> EducationInstitution`
- `EducationInstitutionLesson.course -> EducationInstitutionCourse`
- `EducationInstitutionClassSession.institution -> EducationInstitution`
- `EducationInstitutionClassSession.course -> EducationInstitutionCourse` nullable
- `EducationInstitutionClassSession.lesson -> EducationInstitutionLesson` nullable
- `EducationInstitutionMaterial.institution -> EducationInstitution`
- `EducationInstitutionMaterial.course -> EducationInstitutionCourse` nullable
- `EducationInstitutionMaterial.lesson -> EducationInstitutionLesson` nullable

### Phase 3 additions
- `EducationInstitutionAssessment.institution -> EducationInstitution`
- `EducationInstitutionAssessment.course -> EducationInstitutionCourse` nullable
- `EducationInstitutionAssessment.lesson -> EducationInstitutionLesson` nullable
- `EducationInstitutionAssessment.class_session -> EducationInstitutionClassSession` nullable
- `EducationInstitutionAssessmentQuestion.assessment -> EducationInstitutionAssessment`
- `EducationInstitutionAssessmentOption.question -> EducationInstitutionAssessmentQuestion`
- `EducationInstitutionAssessmentSubmission.assessment -> EducationInstitutionAssessment`
- `EducationInstitutionAssessmentSubmission.user -> User`
- `EducationInstitutionAssessmentSubmission.grader -> User` nullable
- `EducationInstitutionAssessmentResponse.submission -> EducationInstitutionAssessmentSubmission`
- `EducationInstitutionAssessmentResponse.question -> EducationInstitutionAssessmentQuestion`
- `EducationInstitutionAssessmentResponseOption.response -> EducationInstitutionAssessmentResponse`
- `EducationInstitutionAssessmentResponseOption.option -> EducationInstitutionAssessmentOption`

### Phase 4 additions
- `EducationInstitutionEvent.institution -> EducationInstitution`
- `EducationInstitutionBroadcast.institution -> EducationInstitution`
- `EducationInstitutionBroadcast.created_by -> User`
- `EducationInstitutionBroadcast.broadcast_item -> BroadcastItem` nullable
- `EducationInstitutionBroadcast.course -> EducationInstitutionCourse` nullable
- `EducationInstitutionBroadcast.lesson -> EducationInstitutionLesson` nullable
- `EducationInstitutionBroadcast.class_session -> EducationInstitutionClassSession` nullable
- `EducationInstitutionBroadcast.event -> EducationInstitutionEvent` nullable
- `BroadcastItem.source_type` now supports `education_broadcast`

### Phase 5 additions
- `EducationInstitutionEnrollment.institution -> EducationInstitution`
- `EducationInstitutionEnrollment.broadcast -> EducationInstitutionBroadcast`
- `EducationInstitutionEnrollment.user -> User`
- `EducationInstitutionEnrollment.course -> EducationInstitutionCourse` nullable
- `EducationInstitutionEnrollment.lesson -> EducationInstitutionLesson` nullable
- `EducationInstitutionEnrollment.class_session -> EducationInstitutionClassSession` nullable
- `EducationInstitutionEnrollment.event -> EducationInstitutionEvent` nullable
- Unique enrollment constraint:
  - `(broadcast, user)`
- `EducationInstitutionBooking.institution -> EducationInstitution`
- `EducationInstitutionBooking.broadcast -> EducationInstitutionBroadcast`
- `EducationInstitutionBooking.user -> User`
- `EducationInstitutionBooking.wallet_transaction -> WalletTransaction` nullable
- Unique booking constraint:
  - `(broadcast, user)`

### Existing education models retained
- `EducationProfile.user -> User`
- `EducationProfile.profile -> Profile`
- `EducationProfileCourse.profile -> EducationProfile`
- `EducationProfileModule.profile -> EducationProfile`
- `EducationProfileRole.profile -> EducationProfile`
- `EducationProfileRoleAssignment.role -> EducationProfileRole`
- `EducationProfileRoleAssignment.user -> User`

## Files Changed
- `apps/broadcasts/models.py`
- `apps/broadcasts/serializers.py`
- `apps/broadcasts/views.py`
- `apps/broadcasts/urls.py`
- `apps/broadcasts/migrations/0020_educationinstitution_educationinstitutionmembership_and_more.py`
- `apps/broadcasts/migrations/0021_educationinstitutioncourse_and_more.py`
- `apps/broadcasts/migrations/0022_educationinstitutionassessment_and_more.py`
- `apps/broadcasts/migrations/0023_alter_broadcastitem_source_type_and_more.py`
- `apps/broadcasts/migrations/0024_educationinstitutionenrollment_and_more.py`
- `docs/education-system-progress.md`
- `/Users/nigel/dev/KIS/src/network/routes/broadcastRoutes.ts`
- `/Users/nigel/dev/KIS/src/navigation/types.ts`
- `/Users/nigel/dev/KIS/src/screens/profile/ProfileLandingEditorScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/ProfileScreen.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/EducationManagementModal.tsx`

## Risks
- Frontend still likely relies on the legacy `education` profile summary path.
- `EducationProfile` and `EducationInstitution` are not linked yet, so there is still temporary architectural duality.
- The frontend information architecture still needs to be migrated from a profile-page-centric flow to a marketplace-style module flow.
- The mobile/frontend repo still needs to consume the new hub/dashboard contracts; this repo only provides the backend structure for that migration.
- Payment confirmation webhooks/reconciliation are not yet wired back into education bookings, so non-mock card payments are only initiated here, not fully settled.
- Refunds, cancellations, attendance, grading analytics, certificates, and timetable/calendar views are still pending.
- No dedicated moderation/audit trail has been added yet for institution actions such as approval, rejection, grading, or booking overrides.
- Assessment analytics, pass/fail progression rules, and certificate issuance still need to be derived from the new submission records.
- `manage.py check` did not complete cleanly in this session because local Postgres access is restricted here, so Python compile verification and migration generation are confirmed, but database-backed runtime verification is still pending.
- The frontend dashboard module workspace is now being wired to the institution-rooted APIs, but app-side TypeScript verification was still pending when this phase closed.

## Next Recommended Step
- Complete the institution dashboard workspace end-to-end with:
  - clickable institution modules backed by live institution endpoints
  - CRUD for programs, courses, lessons, classes, materials, events, assessments, and broadcasts
  - institution-wide membership, enrollment, and booking management panels
  - attendance tracking
  - result publication and transcript logic
  - certificate/completion records
  - payment confirmation webhook handling and refunds
  - institution analytics based on real enrollments, bookings, assessments, and attendance
