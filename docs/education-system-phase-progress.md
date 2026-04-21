# Education System Phase Progress

## Phase 1

### Completed
- audited current backend and frontend education implementations
- identified the institution-rooted backend as the operational source of truth
- identified legacy education-profile frontend/backend flows as compatibility layers, not the target architecture
- added foundational relationship fields:
  - materials -> program / class session / assessment
  - events -> program / course / class session
  - broadcasts -> program
  - enrollments -> program context
  - bookings -> program / course / class session / event context
- added new broadcast kinds:
  - `program`
  - `institution_notice`
- added `EducationInstitutionStaffAssignment` for explicit staff-to-academic-target relationships
- aligned backend validation rules for:
  - program -> course consistency
  - course -> lesson consistency
  - event target consistency
  - material attachment consistency
- aligned broadcast-card enrollment and booking creation so rows keep academic context from the broadcast
- aligned the mobile institution dashboard forms so they can send the new relationship fields
- aligned education material file handling so files are picked from device and uploaded only on save

### Pending
- frontend detail pages that expose nested relationships instead of flat lists
- direct staff-assignment management UI
- direct program/course/class enrollment flows beyond broadcast-driven entry
- appointment-specific booking flows
- analytics aggregation layer
- legacy `EducationProfile` retirement plan

### Architecture assumptions
- current broadcast-driven enrollment and booking flows remain the compatibility path for now
- explicit nullable foreign keys are preferred over polymorphic generic relations
- membership remains the access root; staff assignment is responsibility mapping on top of membership
- institution notices should not target a narrower academic record

### API contract changes in this phase
- institution materials now accept:
  - `program_id`
  - `class_session_id`
  - `assessment_id`
  - `resource_name`
  - `resource_mime_type`
- institution events now accept:
  - `program_id`
  - `course_id`
  - `class_session_id`
- institution broadcasts now accept:
  - `program_id`
  - new `broadcast_kind` values:
    - `program`
    - `institution_notice`
- new endpoints:
  - `GET|POST /api/v1/broadcasts/education/institutions/<institution_id>/staff-assignments/`
  - `GET|PATCH|DELETE /api/v1/broadcasts/education/institutions/<institution_id>/staff-assignments/<assignment_id>/`

### Database relationship changes
- `EducationInstitutionMaterial`
  - added `program`
  - added `class_session`
  - added `assessment`
  - added `resource_name`
  - added `resource_mime_type`
- `EducationInstitutionEvent`
  - added `program`
  - added `course`
  - added `class_session`
- `EducationInstitutionBroadcast`
  - added `program`
- `EducationInstitutionEnrollment`
  - added `program`
- `EducationInstitutionBooking`
  - added `program`
  - added `course`
  - added `class_session`
  - added `event`
- added `EducationInstitutionStaffAssignment`

### Files changed
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/models.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/serializers.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/views.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/urls.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/migrations/0025_educationinstitutionstaffassignment_and_more.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-integration-plan.md`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-phase-progress.md`
- `/Users/nigel/dev/KIS/src/network/routes/broadcastRoutes.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/EducationManagementModal.tsx`

### Risks
- old app surfaces that still depend on `EducationProfile` can continue to diverge if they are not retired in later phases
- current mobile module forms still use raw IDs for related record selection and need proper selector UX
- `pnpm tsc --noEmit` remains slow/hanging in this environment, so app-wide type verification is still incomplete

### Verification
- backend syntax check passed:
  - `python3 -m py_compile apps/broadcasts/models.py apps/broadcasts/serializers.py apps/broadcasts/views.py apps/broadcasts/urls.py`
- migration generated:
  - `0025_educationinstitutionstaffassignment_and_more.py`
- live Django app registry confirmed the new education schema fields and enums

### Next phase
- Phase 2: service and detail workspace alignment

## Phase 2

### Completed
- added relationship-aware detail payload builders for:
  - program
  - course
  - lesson
  - class session
  - student membership
  - staff membership
- upgraded existing detail endpoints for:
  - program
  - course
  - lesson
  - class session
  so they now return integrated workspace payloads instead of only the single row
- added dedicated member detail endpoints for:
  - student membership detail
  - staff membership detail
- aligned app route contracts so frontend detail screens can consume these payloads directly
- wired the institution dashboard module cards/lists so these modules can now open connected detail workspaces in the mobile UI for:
  - programs
  - courses
  - lessons
  - class sessions
  - students
  - staff

### Pending
- deeper frontend workspaces for assessments, events, memberships, broadcasts, enrollments, and bookings
- drill-down navigation from institution dashboard module lists into the new detail workspaces
- direct staff-assignment management UI
- direct program/course/class enrollment flows beyond broadcast-driven entry
- appointment-specific booking flows
- analytics aggregation layer
- legacy `EducationProfile` retirement plan

### API contract changes in this phase
- upgraded existing endpoints:
  - `GET /api/v1/broadcasts/education/institutions/<institution_id>/programs/<program_id>/`
  - `GET /api/v1/broadcasts/education/institutions/<institution_id>/courses/<course_id>/`
  - `GET /api/v1/broadcasts/education/institutions/<institution_id>/lessons/<lesson_id>/`
  - `GET /api/v1/broadcasts/education/institutions/<institution_id>/class-sessions/<session_id>/`
  so they now return full detail workspace payloads with metrics and related entity lists
- added new endpoints:
  - `GET /api/v1/broadcasts/education/institutions/<institution_id>/memberships/<membership_id>/student-detail/`
  - `GET /api/v1/broadcasts/education/institutions/<institution_id>/memberships/<membership_id>/staff-detail/`

### Files changed
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/views.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/urls.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-phase-progress.md`
- `/Users/nigel/dev/KIS/src/network/routes/broadcastRoutes.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/EducationManagementModal.tsx`

### Assumptions
- returning richer payloads from existing detail endpoints is safer than creating another parallel set of detail routes
- frontend can adopt these payloads incrementally while legacy flat-list views continue to function

### Verification
- backend syntax check passed after the Phase 2 changes:
  - `python3 -m py_compile apps/broadcasts/models.py apps/broadcasts/serializers.py apps/broadcasts/views.py apps/broadcasts/urls.py`
- app route/detail wiring verified by source inspection
- `pnpm tsc --noEmit` still did not finish in this environment

### Next phase
- build frontend detail workspaces for:
  - course
  - lesson
  - class session
  - student
  - staff
- wire taps from institution module lists into those workspaces

## Phase 3

### Completed
- extended relationship-aware detail workspaces to the remaining major institution modules:
  - assessments
  - events
  - broadcasts
  - enrollments
  - bookings
  - memberships now route to student or staff detail appropriately
- upgraded existing detail endpoints so these modules now return integrated payloads instead of flat row serializers for:
  - assessment
  - event
  - broadcast
- added new institution-scoped detail endpoints for:
  - enrollment detail
  - booking detail
- aligned the mobile Education institution dashboard so `View details` is available from:
  - memberships
  - exams
  - events
  - broadcasts
  - enrollments
  - bookings
- extended the connected detail screen in the mobile app to render:
  - assessment questions, materials, submissions, and staff assignments
  - event broadcasts, enrollments, bookings, staff assignments, and related materials
  - broadcast enrollments, bookings, and target staff assignments
  - enrollment-linked bookings and assessment submissions
  - booking-linked enrollments

### Pending
- direct nested drill-down from detail collections into deeper detail workspaces
- dedicated management UI for staff assignments inside the dashboard
- smart related-entity selectors in forms instead of raw ID entry
- direct program/course/class enrollment management flows outside broadcast-card entry
- appointment-specific booking flows
- analytics aggregation and institution insight dashboards
- legacy `EducationProfile` retirement plan

### API contract changes in this phase
- upgraded existing endpoints:
  - `GET /api/v1/broadcasts/education/institutions/<institution_id>/assessments/<assessment_id>/`
  - `GET /api/v1/broadcasts/education/institutions/<institution_id>/events/<event_id>/`
  - `GET /api/v1/broadcasts/education/institutions/<institution_id>/broadcasts/<broadcast_id>/`
  so they now return full detail workspace payloads with metrics and related entity lists
- added new endpoints:
  - `GET /api/v1/broadcasts/education/institutions/<institution_id>/enrollments/<enrollment_id>/`
  - `GET /api/v1/broadcasts/education/institutions/<institution_id>/bookings/<booking_id>/`

### Files changed
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/views.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/urls.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-phase-progress.md`
- `/Users/nigel/dev/KIS/src/network/routes/broadcastRoutes.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/EducationManagementModal.tsx`

### Assumptions
- richer detail payloads on the existing detail endpoints are still safer than introducing a second parallel detail API surface
- membership rows should route to student vs staff detail based on role rather than adding another generic membership detail schema
- event details should surface related materials via linked program/course/class context, because events do not own materials directly

### Verification
- backend syntax check passed after the Phase 3 changes:
  - `python3 -m py_compile apps/broadcasts/views.py apps/broadcasts/urls.py`
- frontend detail routing and rendering were verified by source inspection
- `pnpm tsc --noEmit` still did not produce a result in this environment before timing out/being stopped

### Next phase
- add nested navigation from detail collections into deeper entity workspaces
- add staff-assignment management UI and selectors
- add analytics-oriented summaries for institution, program, course, and student workspaces

## Phase 4

### Completed
- added nested drill-down support inside the mobile education detail workspaces so related collections can open deeper connected workspaces instead of staying as dead lists
- implemented a detail navigation stack in the mobile Education modal so `Back` returns to the previous detail workspace when drilling from:
  - program -> course
  - course -> lesson / class / exam / event / broadcast / enrollment / booking
  - lesson -> class / exam / broadcast / enrollment
  - class -> exam / event / broadcast / enrollment / booking
  - event -> broadcast / enrollment / booking
  - broadcast -> enrollment / booking
  - enrollment -> booking
  - booking -> enrollment
- aligned membership-related drill-down so:
  - membership list rows route to student vs staff detail by role
  - staff-assignment related rows route into the linked staff membership workspace
- kept unsupported nested records read-only for now where a proper detail workspace does not yet exist:
  - materials
  - assessment questions
  - assessment submissions

### Pending
- direct staff-assignment management UI
- dedicated staff-assignment detail workspace
- smart related-entity selectors in forms instead of raw ID entry
- analytics aggregation and dashboard refinement
- direct management flows for enrollments and bookings beyond current detail visibility
- legacy `EducationProfile` retirement plan

### Files changed
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-phase-progress.md`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/EducationManagementModal.tsx`

### Assumptions
- nested workspaces should use the same detail screen rather than introducing a separate navigator for each module
- when staff assignments appear as related records, the most useful drill-down target is the linked staff membership workspace, not the raw assignment row

### Verification
- backend syntax check still passed after the Phase 4 app-side changes:
  - `python3 -m py_compile apps/broadcasts/views.py apps/broadcasts/urls.py`
- `pnpm tsc --noEmit` still did not return in this environment before timing out/being stopped

### Next phase
- add staff-assignment create/edit/remove UI with relationship-aware selectors
- add nested analytics summaries to detail workspaces
- replace raw relation ID fields in forms with institution-scoped pickers

## Phase 5

### Completed
- added staff-assignment management UI inside the staff detail workspace in the mobile app
- staff assignments can now be:
  - created
  - edited
  - deleted
  directly from the selected staff member's workspace
- institution-scoped lookups now include staff memberships so assignment creation does not rely on manual membership IDs
- replaced raw relation ID entry with selector-based institution lookups in the major academic forms for:
  - courses
  - lessons
  - class sessions
  - materials
  - assessments
  - events
  - broadcasts
- selector flows now use institution-owned related records instead of pasted IDs for:
  - program
  - course
  - lesson
  - class session
  - assessment
  - event
  - staff membership

### Pending
- analytics aggregation and dashboard refinement
- direct management flows for enrollments and bookings beyond detail visibility
- dedicated workspaces for materials, assessment questions, and assessment submissions
- stronger selector UX for large institutions with many records
- legacy `EducationProfile` retirement plan

### Files changed
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-phase-progress.md`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/EducationManagementModal.tsx`

### Assumptions
- the staff detail workspace is the safest place to manage staff assignments because assignments are owned by institution staff membership, not by a separate top-level module
- selector-based relation binding is sufficient for current institution sizes; searchable selectors can be added later if option lists become too large

### Verification
- backend syntax check remained valid with no new backend changes required in this phase
- frontend selector and staff-assignment integration were verified by source inspection
- `pnpm tsc --noEmit` still did not return in this environment before timing out/being stopped

### Next phase
- add analytics summaries to institution, program, course, lesson, class, staff, and student workspaces
- improve booking and enrollment operational management
- add richer searchable selectors where record counts become large

## Phase 6

### Completed
- added derived analytics summaries to the institution analytics workspace in the mobile app using real institution dashboard metrics
- added operational summary cards to the institution module workspaces for:
  - enrollments
  - bookings
  - memberships
  - students
  - staff
- added detail-level operational insight summaries to connected workspaces using real related data from:
  - enrollments
  - bookings
  - assessment submissions
  - staff assignments
- kept analytics generation frontend-derived from the existing relationship-aware payloads so no duplicate analytics endpoint was required for this phase

### Pending
- direct management flows for enrollments and bookings beyond visibility and detail drill-down
- searchable selectors for larger institutions
- dedicated workspaces for materials, assessment questions, and assessment submissions
- deeper analytics and trend reporting beyond current snapshot summaries
- legacy `EducationProfile` retirement plan

### Files changed
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-phase-progress.md`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/EducationManagementModal.tsx`

### Assumptions
- current phase focuses on operational snapshot analytics rather than historical trend reporting
- deriving summaries from already-fetched detail payloads is preferable to adding another analytics API surface before trend/history requirements are finalized

### Verification
- backend syntax check remained valid with no new backend changes required in this phase
- `pnpm tsc --noEmit` still did not return in this environment before timing out/being stopped

### Next phase
- add enrollment and booking management actions where institution staff need to intervene
- introduce searchable selectors for large relation sets
- expand analytics from snapshot counts into trend and performance reporting

## Phase 7

### Completed
- added institution-manager action endpoints for:
  - enrollment status management
  - booking status management
- enrollment actions now support:
  - pending
  - enroll
  - waitlist
  - cancel
  - complete
- booking actions now support:
  - pending
  - payment pending
  - confirm
  - waitlist
  - cancel
  - expire
- wired those actions into the mobile app at both levels:
  - module list rows
  - enrollment detail workspace
  - booking detail workspace
- kept permissions institution-manager scoped so operational intervention still happens through explicit backend rules

### Files changed
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/views.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/urls.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-phase-progress.md`
- `/Users/nigel/dev/KIS/src/network/routes/broadcastRoutes.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/EducationManagementModal.tsx`

### Assumptions
- operational status transitions should go through explicit action endpoints instead of direct patch access to status fields
- current management needs are adequately covered by status transitions without yet adding refund, seat rebalancing, or audit-history UI

### Verification
- backend syntax check passed after the Phase 7 changes:
  - `python3 -m py_compile apps/broadcasts/views.py apps/broadcasts/urls.py`
- `pnpm tsc --noEmit` still did not return in this environment before timing out/being stopped

### Pending
- searchable selectors for larger institutions
- richer trend/performance analytics
- dedicated operational flows for refunds and booking payment reconciliation
- dedicated workspaces for materials, assessment questions, and assessment submissions
- legacy `EducationProfile` retirement plan

### Next phase
- add searchable selectors for large institution datasets
- expand analytics from snapshot status summaries into trend and performance reporting
- refine payment and booking reconciliation flows

## Phase 8

### Completed
- added a real public education discovery feed at:
  - `GET /api/v1/education/discovery/`
  backed only by structured `EducationInstitutionBroadcast` records
- added a compatibility action endpoint at:
  - `POST /api/v1/education/contents/<content_id>/enroll/`
  so the mobile education tab can create enrollments or paid bookings from the new structured broadcast cards
- normalized paid education bookings to `KISC`
- replaced immediate provider settlement with a market-style education booking lifecycle:
  - buyer wallet debit locks funds
  - provider marks booking complete
  - buyer confirms satisfaction
  - provider payout releases after satisfaction
  - payout auto-releases after 3 days of no buyer action
- added cancellation refund handling for locked education booking funds before provider payout
- added a payer satisfaction endpoint:
  - `POST /api/v1/broadcasts/education/institutions/<institution_id>/bookings/<booking_id>/satisfy/`
- removed the main broadcast education page fallback to the legacy education discover screen in the mobile app
- wired the existing mobile V2 education sheet to the real backend enrollment/payment path instead of mock timers

### Files changed
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/models.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/serializers.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/views.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/urls.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/tasks.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/migrations/0026_educationinstitutionbooking_payer_satisfied_at_and_more.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-phase-progress.md`
- `/Users/nigel/dev/KIS/src/screens/broadcast/pages/BroadcastEducationPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/EducationV2DiscoverPage.tsx`

### Assumptions
- institution owner is the payout recipient for education bookings in this phase
- the public education broadcast page still uses the existing V2 UI shell, but its source of truth is now the structured education broadcast system
- old education discovery components may remain in the app codebase temporarily, but they are no longer the intended runtime path

### Verification
- backend syntax check passed:
  - `python3 -m py_compile apps/broadcasts/models.py apps/broadcasts/serializers.py apps/broadcasts/views.py apps/broadcasts/urls.py apps/broadcasts/tasks.py`
- Django runtime check passed:
  - `python3 manage.py check`
- migration generated:
  - `0026_educationinstitutionbooking_payer_satisfied_at_and_more.py`

### Pending
- dedicated module-specific broadcast card UI for course / lesson / event / training / institution notice
- mobile actions for provider-complete and buyer-satisfy directly from the public education card/detail flow
- richer payout routing when a staff assignee, not the institution owner, should receive funds
- searchable selectors for larger institutions
- richer trend/performance analytics
- dedicated workspaces for materials, assessment questions, and assessment submissions
- legacy `EducationProfile` retirement plan

### Next phase
- build dedicated module-aware education broadcast cards in the mobile app
- expose provider-complete and buyer-satisfaction actions in the public education flow
- refine payout routing beyond institution owner where staff assignment should govern settlement

## Public Readiness Recovery

### Completed
- audited the current education implementation against a Coursera-style learner flow
- confirmed the current backend is strong on:
  - institution administration
  - broadcast-backed discovery
  - booking and payment operations
- confirmed the current system is still weak on:
  - learner course consumption
  - module-based navigation
  - resume/progress/completion clarity
- formalized a three-surface architecture in:
  - `docs/education-system-integration-plan.md`
    - institution admin workspace
    - public discovery and broadcast entry
    - learner consumption workspace
- defined the public-readiness phase order from learner information architecture through cleanup

### Pending
- create the learner-facing course/module/item hierarchy
- implement course landing pages and module outlines
- implement a true learner course workspace
- add lesson/material/class/exam consumption flows
- add progress, completion, and certificate UX
- retire or hide legacy learner-discovery codepaths that conflict with the new structure

### Architecture assumptions
- broadcasts remain the public entry point, not the source of truth for course consumption
- institutions remain the top-level owner of all education data
- admin UX and learner UX must stay separated going forward
- existing admin CRUD flows remain valid, but new learner work should not be layered back into those screens

### Files changed
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-integration-plan.md`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-phase-progress.md`

### Risks
- if future work continues to mix admin and learner tasks in the same mobile surfaces, navigation complexity will keep increasing
- if module sequencing is implemented as ad hoc frontend grouping instead of a stable backend contract, progress and completion will remain fragile

### Verification
- documentation updated to reflect the new public-readiness architecture and recovery phases

### Next phase
- Public Phase 1:
  - start the learner hierarchy foundation
  - introduce course module sequencing and a learner-facing course outline contract

## Public Phase 1

### Completed
- added the learner-facing course sequencing foundation to the backend
- created explicit course hierarchy models:
  - `EducationInstitutionCourseModule`
  - `EducationInstitutionCourseModuleItem`
- introduced typed learning items for module sequencing:
  - lesson
  - material
  - class session
  - assessment
  - event
  - broadcast
- kept the institution-rooted and broadcast-rooted architecture intact
- kept the existing admin course, lesson, class, material, assessment, event, and broadcast models as the source records
- added a new `course_outline` contract to course detail payloads so learner UX can consume an ordered module/item structure
- added a compatibility fallback outline for existing courses with no explicit modules yet
  - this prevents old courses from appearing empty while the new structure is being adopted
- added the missing public content detail endpoint:
  - `GET /api/v1/education/contents/<content_id>/`
- the new public content detail payload now returns:
  - content summary
  - pricing metadata
  - viewer state
  - module-aware syllabus for course broadcasts
  - a richer course outline for learner-facing screens

### Pending
- admin CRUD endpoints and UI for managing course modules and module items directly
- public mobile detail sheet integration with the new content detail endpoint
- learner course workspace UI built around:
  - course hero
  - module outline
  - current item
  - continue learning
  - progress
- learner item consumption pages for lessons, materials, classes, and assessments

### Architecture assumptions
- explicit course modules are the safest learner-facing grouping layer and should sit under courses, not replace programs or lessons
- `Broadcast` remains the public entry point, not the authoritative source of course structure
- compatibility fallback outlines are acceptable temporarily because they preserve public-readiness while admin module tools are still being built
- explicit nullable foreign keys remain preferable to generic polymorphic relations for learning items

### API contract changes in this phase
- new public detail endpoint:
  - `GET /api/v1/education/contents/<content_id>/`
- upgraded course detail payload:
  - adds `course_outline`
  - adds `modules`
  - adds `metrics.module_count`
- new learner outline shape:
  - module:
    - `id`
    - `title`
    - `summary`
    - `module_order`
    - `is_preview`
    - `duration_minutes`
    - `item_count`
    - `items`
  - item:
    - `id`
    - `type`
    - `title`
    - `summary`
    - `duration_minutes`
    - `is_preview`
    - `target`

### Database relationship changes
- added `EducationInstitutionCourseModule`
  - belongs to institution
  - belongs to course
  - ordered by `module_order`
- added `EducationInstitutionCourseModuleItem`
  - belongs to institution
  - belongs to course
  - belongs to module
  - points to one typed learning record

### Files changed
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/models.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/serializers.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/views.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/urls.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/migrations/0028_educationinstitutioncoursemodule_and_more.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-integration-plan.md`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-phase-progress.md`

### Risks
- if the mobile app continues to show only flat content cards, the learner value of the new course outline will remain hidden until the next phase
- fallback outlines are useful for compatibility, but they should not become a permanent substitute for explicit module authoring

### Verification
- backend syntax check passed:
  - `python3 -m py_compile apps/broadcasts/models.py apps/broadcasts/serializers.py apps/broadcasts/views.py apps/broadcasts/urls.py`
- migration generated:
  - `0028_educationinstitutioncoursemodule_and_more.py`
- migration applied:
  - `python3 manage.py migrate`
- Django runtime check passed:
  - `python3 manage.py check`

### Next phase
- Public Phase 2:
  - add module and module-item management to the institution admin workspace
  - connect the public mobile detail sheet to the new content detail endpoint
  - start rendering the learner-facing course outline in the app

## Public Phase 2

### Completed
- added backend CRUD endpoints for course modules and module items under institution courses
- course modules can now be:
  - listed
  - created
  - viewed
  - updated
  - deleted
- module items can now be:
  - listed
  - created
  - viewed
  - updated
  - deleted
- module items validate against the selected course and support these target types:
  - lesson
  - material
  - class session
  - assessment
  - event
  - broadcast
- connected the public education detail flow to the real backend content-detail endpoint
- public course detail sheets now load the richer backend payload instead of relying only on shallow discovery-card data
- public course detail sheets now render:
  - syllabus
  - course outline
  - outcomes
  - requirements
- institution admin course detail pages now expose the new `course_outline` so creators can at least inspect the learner structure directly from the course workspace

### Pending
- dedicated module/module-item create-edit UI inside the institution admin course workspace
- richer learner detail screen styling and deeper item-level navigation
- learner course workspace with continue/resume/progress tabs
- program and event landing pages that match the new learner/public structure

### API contract changes in this phase
- new institution admin endpoints:
  - `GET|POST /api/v1/broadcasts/education/institutions/<institution_id>/courses/<course_id>/modules/`
  - `GET|PATCH|DELETE /api/v1/broadcasts/education/institutions/<institution_id>/courses/<course_id>/modules/<module_id>/`
  - `GET|POST /api/v1/broadcasts/education/institutions/<institution_id>/courses/<course_id>/modules/<module_id>/items/`
  - `GET|PATCH|DELETE /api/v1/broadcasts/education/institutions/<institution_id>/courses/<course_id>/modules/<module_id>/items/<item_id>/`
- public detail endpoint now serves the mobile learner sheet:
  - `GET /api/v1/education/contents/<content_id>/`

### Files changed
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/views.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/urls.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-phase-progress.md`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/EducationV2DiscoverPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/components/EducationDetailSheet.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/EducationManagementModal.tsx`

### Assumptions
- the first public learner improvement should reuse the existing education detail sheet rather than adding another public screen immediately
- backend module CRUD should land before a larger admin module-builder UI so the contract is stable first

### Verification
- backend syntax check passed:
  - `python3 -m py_compile apps/broadcasts/views.py apps/broadcasts/urls.py`
- Django runtime check passed:
  - `python3 manage.py check`
- app patches were verified by source inspection

### Next phase
- Public Phase 3:
  - build module and module-item editing into the institution admin course workspace
  - create a fuller learner course workspace beyond the current modal/detail sheet

## Public Phase 3

### Completed
- added course-module and module-item CRUD endpoints to the institution admin API surface
- added app route helpers for:
  - course modules
  - course module items
- extended the institution admin course workspace so creators can now manage learner structure directly from the course detail view
- course detail now supports:
  - create module
  - edit module
  - delete module
  - create module item
  - edit module item
  - delete module item
- module item editing is relationship-aware and supports these course-scoped target types:
  - lesson
  - material
  - class session
  - assessment
  - event
  - broadcast
- kept module authoring inside the course workspace instead of adding another disconnected screen
- continued exposing the learner-facing `course_outline` in both:
  - admin course detail
  - public course detail

### Pending
- replace the current free-text item-type entry with a stronger pill/tab selector
- add drag/reorder UX for modules and module items
- build a fuller learner course workspace beyond the current public detail modal
- add continue/resume/progress navigation around the outline
- add item-level learner consumption pages for:
  - lesson
  - material
  - class session
  - assessment

### API contract changes in this phase
- added institution admin endpoints:
  - `GET|POST /api/v1/broadcasts/education/institutions/<institution_id>/courses/<course_id>/modules/`
  - `GET|PATCH|DELETE /api/v1/broadcasts/education/institutions/<institution_id>/courses/<course_id>/modules/<module_id>/`
  - `GET|POST /api/v1/broadcasts/education/institutions/<institution_id>/courses/<course_id>/modules/<module_id>/items/`
  - `GET|PATCH|DELETE /api/v1/broadcasts/education/institutions/<institution_id>/courses/<course_id>/modules/<module_id>/items/<item_id>/`

### Files changed
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/views.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/urls.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-phase-progress.md`
- `/Users/nigel/dev/KIS/src/network/routes/broadcastRoutes.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/profile-screen/EducationManagementModal.tsx`

### Assumptions
- creator-facing module authoring belongs inside the course workspace, not at the institution hub level
- the current public detail modal is still a temporary learner surface and should be replaced later by a fuller learner workspace

### Verification
- backend syntax check passed:
  - `python3 -m py_compile apps/broadcasts/views.py apps/broadcasts/urls.py`
- Django runtime check passed:
  - `python3 manage.py check`
- app route and source integration were verified by source inspection

### Next phase
- Public Phase 4:
  - build the fuller learner course workspace
  - add continue/resume/progress navigation
  - start item-level learner consumption pages

## Public Phase 4

### Completed
- added a real learner progress contract on the backend:
  - `GET /api/v1/education/progress/`
  - `POST /api/v1/education/progress/`
- learner progress is now stored on the existing enrollment record metadata instead of a duplicate progress table
- public discovery `continue_learning` now comes from enrollment-backed progress payloads instead of hard-coded percentages
- public content detail now returns:
  - `progress`
  - `current_item`
  - `current_module`
  - `next_item`
- paid education checkout now also ensures an enrollment record exists so paid learners can enter the same learner-progress flow as free enrollments
- upgraded the public education detail sheet into a learner workspace that now shows:
  - progress bar
  - module path
  - current module
  - current item
  - next item
  - item-level progress actions
- continue-learning cards now display human-readable content titles instead of raw content ids
- discovery resume now opens the learner workspace directly instead of showing an alert

### Pending
- split the learner workspace into dedicated item-consumption pages for:
  - lesson
  - material
  - class session
  - assessment
- add richer learner states:
  - deadlines
  - grades
  - attendance
  - certificate readiness
- improve paid-content access edge cases around booking-only flows beyond the current enrollment bridge
- run a clean app-wide TypeScript pass after broader repo syntax debt is cleared

### API contract changes in this phase
- added learner progress endpoints:
  - `GET /api/v1/education/progress/`
  - `POST /api/v1/education/progress/`
- public content detail now also returns:
  - `progress`
  - `current_item`
  - `current_module`
  - `next_item`

### Files changed
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/views.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/urls.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-phase-progress.md`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/api/education.models.ts`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/EducationV2DiscoverPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/components/EducationContinueLearning.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/components/EducationDetailSheet.tsx`

### Assumptions
- enrollment metadata is the safest short-term source of truth for learner progress because it keeps progress tied to real access records
- the learner workspace can start inside the current public detail surface before being split into dedicated item-consumption pages
- paid education content should enter the same learner flow as free enrollments once checkout succeeds

### Verification
- backend syntax check passed:
  - `python3 -m py_compile apps/broadcasts/views.py apps/broadcasts/urls.py`
- Django runtime check passed:
  - `python3 manage.py check`
- app-wide `pnpm tsc --noEmit` surfaced an unrelated existing syntax break in `EducationManagementModal.tsx`; that concrete syntax error was fixed, but a full repo-wide TypeScript clean pass was not completed in this turn

### Next phase
- Public Phase 5:
  - split learner items into dedicated consumption screens
  - add lesson/material/class/assessment completion semantics
  - start exposing grades, attendance, and certificate progress in the learner workspace

## Public Phase 5

### Completed
- expanded learner module items so each outline item now carries type-specific content payloads instead of only a title/summary row
- lesson items now expose:
  - lesson body content
  - lesson ordering
  - linked lesson materials
- material items now expose:
  - material kind
  - resource URL
  - resource name
  - mime type
  - downloadability
- class session items now expose:
  - schedule
  - delivery mode
  - location
  - meeting URL
- assessment items now expose:
  - instructions
  - question previews
  - timing and grading metadata
- event and broadcast items now expose:
  - schedule
  - location / joining metadata
- upgraded the learner workspace UI so selected items are now consumable in-place:
  - lesson reader
  - material viewer/open-resource action
  - class-session join panel
  - assessment preview panel
  - event/broadcast schedule panel
- kept the new consumption flow inside the existing learner workspace so progress, current item, and completion actions still stay in one place

### Pending
- split item consumption into truly dedicated full-screen lesson/material/class/assessment routes if needed for deeper long-form use
- add assessment submission UX instead of preview-only assessment consumption
- add attendance join/leave tracking for class sessions
- add richer material rendering for embedded video/pdf instead of open-link-only behavior
- add grade, attendance, and certificate readiness widgets into the learner workspace

### API contract changes in this phase
- `courseOutline[].items[]` now also carries a `content` payload for supported learner item types:
  - lesson
  - material
  - class session
  - assessment
  - event
  - broadcast

### Files changed
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/views.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-phase-progress.md`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/api/education.models.ts`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/components/EducationDetailSheet.tsx`

### Assumptions
- the fastest safe path is to make learner items usable inside the current learner workspace before introducing more navigation layers
- open-resource actions are acceptable for materials and live-session links until richer embedded viewers are added
- assessment preview is a valid intermediate phase before full answer submission UX is implemented

### Verification
- backend syntax check passed:
  - `python3 -m py_compile apps/broadcasts/views.py apps/broadcasts/urls.py`
- Django runtime check passed:
  - `python3 manage.py check`
- app-wide `pnpm tsc --noEmit` still did not return a clean completion in this larger repo during this turn, so this phase is verified by targeted source inspection plus the backend/runtime checks above

### Next phase
- Public Phase 6:
  - assessment submission and grading UX
  - class attendance tracking
  - richer embedded material consumption
  - learner grades, attendance, and certificate-readiness panels

## Public Phase 6

### Completed
- added a public learner item-action endpoint:
  - `POST /api/v1/education/contents/<content_id>/items/<item_id>/action/`
- wired learner assessment actions into the public course flow:
  - start assessment attempt
  - save draft responses
  - submit assessment
- reused the existing backend assessment submission/grading foundation instead of creating a duplicate learner-only assessment model
- added learner attendance tracking for class-session items through the public learner flow
- attendance is now stored on the real enrollment metadata so it stays aligned with learner progress
- learner detail payloads now return `insights` with:
  - attendance count
  - assessment submission count
  - graded assessment count
  - average score percent
  - certificate progress percent
  - certificate readiness
- upgraded the learner workspace UI so it now supports:
  - starting, saving, and submitting assessments
  - marking class attendance
  - showing learner insights for grades, attendance, and certificate readiness
- kept all of this inside the existing learner workspace instead of spawning another disconnected learner screen

### Pending
- richer assessment UX:
  - timed countdown behavior
  - attempt history
  - detailed grading feedback rendering
- richer material consumption:
  - embedded pdf/video/image viewers instead of open-link only
- stronger class-session attendance lifecycle:
  - join
  - leave
  - attendance proof / duration
- dedicated certificate page and issuance UX once readiness becomes true

### API contract changes in this phase
- added learner item-action endpoint:
  - `POST /api/v1/education/contents/<content_id>/items/<item_id>/action/`
- public content detail now also returns:
  - `insights`

### Files changed
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/views.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/urls.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-phase-progress.md`
- `/Users/nigel/dev/KIS/src/network/routes/broadcastRoutes.ts`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/api/education.models.ts`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/EducationV2DiscoverPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/components/EducationDetailSheet.tsx`

### Assumptions
- attendance is best tracked on enrollment metadata for now because it keeps learner engagement tied to access and progress without adding another shadow attendance table
- the existing assessment submission system is production-stronger than inventing a second public-only submission flow
- certificate readiness can safely start as a derived signal based on completion progress and passing graded assessments

### Verification
- backend syntax check passed:
  - `python3 -m py_compile apps/broadcasts/views.py apps/broadcasts/urls.py`
- Django runtime check passed:
  - `python3 manage.py check`
- app-wide `pnpm tsc --noEmit` again did not complete cleanly in the larger repo during this turn, so this phase is verified by targeted source inspection plus the backend/runtime checks above

### Next phase
- Public Phase 7:
  - embedded media/document consumption
  - fuller grading feedback views
  - dedicated certificate view / issuance flow
  - learner navigation and polish for public readiness

## Public Phase 7

### Completed
- added a protected learner certificate endpoint:
  - `GET /api/v1/education/contents/<content_id>/certificate/`
- the certificate endpoint now:
  - validates enrollment access
  - reuses derived certificate readiness rules
  - generates a PDF certificate using the existing certificate renderer
  - stores certificate metadata back on the real enrollment record
  - can return JSON metadata with `?format=json`
- public content detail now also returns a lightweight `certificate` block so the learner UI knows when a certificate is unlocked
- upgraded the learner workspace material experience:
  - inline video playback
  - inline audio playback
  - inline PDF viewing
  - inline image viewing
  - graceful fallback for unsupported file types
- upgraded the learner workspace assessment experience:
  - instructor/grader feedback rendering
  - per-response grading feedback rendering when available
- added certificate UX to the learner workspace:
  - unlocked-certificate status card
  - in-workspace certificate preview
  - open/download flow from the generated PDF
- kept these enhancements inside the current learner workspace instead of fragmenting education into more disconnected surfaces

### Pending
- dedicated full-screen learner routes for long-form consumption if the single-sheet experience becomes too dense
- stronger certificate verification/share flow beyond local preview and PDF open
- navigation polish and accessibility cleanup across the public learner workspace
- repo-wide TypeScript cleanup so the education app changes can be verified in a fully clean app check

### API contract changes in this phase
- added learner certificate endpoint:
  - `GET /api/v1/education/contents/<content_id>/certificate/`
- public content detail now also returns:
  - `certificate.ready`
  - `certificate.certificateId`
  - `certificate.issuedAt`

### Files changed
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/views.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/urls.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-phase-progress.md`
- `/Users/nigel/dev/KIS/src/network/routes/broadcastRoutes.ts`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/EducationV2DiscoverPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/components/EducationDetailSheet.tsx`

### Assumptions
- reusing the existing PDF certificate renderer is safer and more maintainable than inventing a second certificate implementation for education
- certificate issuance should stay tied to the real enrollment record so readiness, completion, and artifact generation do not drift apart
- richer inline material viewing is a higher-value public-readiness improvement than adding more separate learner screens immediately

### Verification
- backend syntax check passed:
  - `python3 -m py_compile apps/broadcasts/views.py apps/broadcasts/urls.py`
- repo-wide `python3 manage.py check` did not return promptly in this environment during this turn, so this phase is verified by backend parse checks plus targeted source inspection
- repo-wide `pnpm tsc --noEmit` again did not produce a clean quick completion in the larger app workspace during this turn

### Next phase
- Public Phase 8:
  - learner navigation polish
  - stronger breadcrumbs and progress pathing
  - accessibility and readability cleanup
  - certificate verification/share refinement

## Public Phase 8

### Completed
- upgraded the learner workspace navigation so it now behaves like a guided flow instead of one long undifferentiated sheet
- added learner workspace sections:
  - `Overview`
  - `Path`
  - `Continue`
  - `Certificate` when unlocked
- added a breadcrumb-style learning path strip so learners can see where they are in:
  - the course
  - the current module
  - the current item or certificate step
- added clearer progress guidance text so learners understand the intended flow:
  - overview
  - path
  - active learning item
  - certificate
- improved interaction accessibility on core learner navigation controls:
  - section chips now expose selected state
  - module and item rows now expose button semantics and clearer labels
- refined certificate UX further:
  - certificate ID is now surfaced in the learner workspace
  - share action is now available for certificate metadata
  - certificate remains inside the learner workspace rather than sending users into a disconnected screen

### Pending
- true public verification/share endpoint for education certificates if external third-party verification is required
- deeper accessibility pass across the broader education discovery surface, not only the learner sheet
- app-wide TypeScript cleanup so the education learner flow can be verified under a clean repo-wide app check
- long-form route splitting only if the single learner sheet becomes too dense in real usage

### API contract changes in this phase
- no new backend contract was required for this phase
- the phase uses the already-added learner detail and certificate endpoints

### Files changed
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-phase-progress.md`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/components/EducationDetailSheet.tsx`

### Assumptions
- the fastest path to public-readiness is to make the existing learner workspace far easier to understand before splitting it into more screens
- breadcrumbing and sectioned navigation reduce confusion without changing the education data model again
- certificate sharing can safely begin as metadata sharing until a full external verification endpoint is needed

### Verification
- verified by targeted source inspection of the updated learner workspace file
- repo-wide `pnpm tsc --noEmit` was not relied on here because the larger app workspace has not been returning a clean quick completion in this environment

### Next phase
- Public Phase 9:
  - external certificate verification/share endpoint if needed
  - broader accessibility pass across discovery and enrollment flows
  - visual polish and cleanup on the remaining public education surfaces

## Public Phase 9

### Completed
- added a public certificate verification endpoint:
  - `GET /api/v1/education/certificates/share/<token>/`
- upgraded the authenticated learner certificate endpoint so it now also issues and returns:
  - `certificate_share_token`
  - `share_url`
- kept certificate identity tied to the real enrollment metadata so:
  - certificate readiness
  - certificate PDF generation
  - public verification
  all resolve from the same enrollment record instead of separate shadow storage
- updated the learner workspace to share a real verification link instead of only local certificate metadata
- improved discovery/enrollment public UX:
  - education card accessibility labels now include content type, title, and progress when available
  - enrollment sheet now clearly communicates the KISC held-funds payment model
  - default public price wording now prefers `KISC`

### Pending
- repo-wide app TypeScript cleanup so the public education app can be verified in a full clean compile
- broader visual polish beyond the main learner/discovery/enrollment surfaces if needed after public testing
- external branded verification landing page if a richer non-JSON share page is later required
- learner-home and course-landing refinement based on real user testing, not missing architecture

### API contract changes in this phase
- added public certificate verification endpoint:
  - `GET /api/v1/education/certificates/share/<token>/`
- authenticated certificate JSON now also returns:
  - `certificate_share_token`
  - `share_url`

### Files changed
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/views.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/urls.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/education-system-phase-progress.md`
- `/Users/nigel/dev/KIS/src/network/routes/broadcastRoutes.ts`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/api/education.models.ts`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/hooks/useEducationDiscovery.ts`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/EducationV2DiscoverPage.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/components/EducationContentCard.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/components/EducationEnrollmentSheet.tsx`
- `/Users/nigel/dev/KIS/src/screens/broadcast/education/components/EducationDetailSheet.tsx`

### Assumptions
- a public JSON verification endpoint is a safe first production-ready share contract because it is stable, explicit, and easy to consume by the app or future public landing pages
- public readiness is better served by finishing certificate verification and payment clarity now rather than inventing more new screens
- the remaining work is QA/polish-oriented, not foundational architecture work

### Verification
- backend syntax check passed:
  - `python3 -m py_compile apps/broadcasts/views.py apps/broadcasts/urls.py`
- targeted source inspection completed for the patched app files
- backend discovery/detail contract expansion verified by the same parse check after the institution-summary and trust-signal additions
- repo-wide `pnpm tsc --noEmit` was not used as the final verifier because the larger app workspace still does not return a clean quick completion in this environment

### Next phase
- no further foundational public-readiness phase is required
- next work, if needed, should be targeted QA fixes and visual refinement based on real testing feedback
