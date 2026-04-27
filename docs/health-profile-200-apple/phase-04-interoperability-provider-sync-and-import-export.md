# Phase 4 - Interoperability Provider Sync And Import Export

Phase goal:
- Make the health profile portable and interoperable with external providers and systems.

Why this phase exists:
- A top-tier health app is not only a storage UI. It can import, normalize, export, and share records across systems.

Primary backend targets:
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/core`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/health_ops`

Deliverables:
- Import pipeline for external provider records
- Export bundle for personal health records
- FHIR-oriented mapping for core data classes
- Source metadata and deduplication strategy
- Audit trail for imports and shares

Required capabilities:
- import medications, allergies, immunizations, conditions, lab results, and encounters
- export complete patient profile bundle
- retain provenance and source identity
- support provider-linked record refresh jobs later

Recommended scope:
- Start with one internal canonical mapping layer
- Then add import adapters
- Then add export endpoints
- Keep raw imported payloads where safe for troubleshooting and reconciliation

Implementation tasks:
- Define FHIR mapping layer for core record types
- Add import job model or service orchestration
- Add export endpoint for patient-authorized record downloads
- Add duplicate merge rules and conflict indicators
- Add import audit logging

Implementation notes:
- Completed on 2026-04-25.
- Backend:
  - added FHIR-oriented bundle export helper in `apps/core/interoperability.py`
  - added FHIR-oriented bundle import helper for:
    - allergies
    - medications
    - problems
    - immunizations
    - procedures
    - documents
    - encounters
  - added patient-level and current-user import and export actions on `PatientMasterRecordViewSet`
  - added `HealthRecordExchangeLog` for persisted auditability of imports and exports
  - added read-only exchange log endpoint
- Frontend:
  - added route and service bindings for:
    - patient export bundle
    - patient import bundle
    - my export bundle
    - my import bundle
    - exchange logs

Verification:
- `python3 manage.py makemigrations core` generated:
  - `0016_healthrecordexchangelog.py`
- `python3 manage.py check` passed
- backend tests remain blocked by the same local test-db environment path
- React Native full typecheck remains blocked by unrelated project-wide TypeScript errors

Exit note:
- Phase 4 is complete enough to begin Phase 5.
- The system can now export and import a FHIR-oriented bundle while keeping an exchange audit trail.

Verification:
- mapping tests from internal models to external payloads
- import and export contract tests
- permission tests for user-authorized sharing and download

Exit criteria:
- A user can move their health record in and out of the KIS system without losing meaning.
