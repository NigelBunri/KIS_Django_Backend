# Phase 6 - UX Split Privacy Sharing And Hardening

Phase goal:
- Finalize the product by separating patient and operator experiences while adding privacy, sharing, and operational hardening.

Why this phase exists:
- The current surface blends institution operations with personal health use. That limits clarity and trust.

Primary targets:
- `/Users/nigel/dev/KIS/src/screens`
- `/Users/nigel/dev/KIS/src/services`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/core`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/health_dashboard`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/health_ops`

Deliverables:
- a dedicated patient health hub
- a dedicated institution and operations hub
- privacy and sharing controls
- caregiver or family delegation
- access audit views
- retention and export policy checks

Required product hardening:
- explicit sharing scopes
- time-bound shared access
- caregiver delegation and revocation
- access history for sensitive health data
- policy for emergency override access
- clear distinction between patient-entered and provider-entered data

Implemented in this phase:
- added `HealthDataAccessGrant` in `apps.core` for explicit delegated health-record access
- added sharing scopes for:
  - `summary`
  - `emergency`
  - `records`
  - `full`
- added delegated roles for:
  - caregiver
  - family
  - guardian
  - clinician
- hardened patient health endpoints so access is now checked against:
  - patient ownership
  - provider organization membership
  - active delegated access grant
- added access-history responses backed by `ComplianceAuditLog`
- added patient and current-user sharing summary endpoints
- updated the React Native healthcare screen with:
  - sharing summary
  - access grant creation
  - grant revocation
  - access history preview
- added regression tests for:
  - denying unrelated users
  - allowing explicitly delegated users

Implementation tasks:
- redesign frontend IA so personal health does not depend on institution management screens
- add sharing and delegation models if missing
- add access-log views and audit endpoints
- add sensitive-field permission tests
- add operational monitoring for import failures and stale device syncs

Verification:
- permission and audit tests
- sharing and revocation tests
- smoke validation across patient and operator app flows

Exit criteria:
- The system feels like one coherent health platform with two intentional surfaces:
  - personal health
  - provider and institution operations

Phase result:
- complete

Remaining follow-up work beyond the six-phase program:
- move the patient health surface out of the mixed `HealthcareScreen` into a dedicated personal health hub screen tree
- add native HealthKit and Health Connect sync clients on top of the Phase 5 metric contracts
- clean unrelated project-wide TypeScript issues so frontend verification becomes green
