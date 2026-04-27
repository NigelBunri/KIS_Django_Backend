# Phase 1 - Canonical Profile And Data Contract

Phase goal:
- Establish one canonical person-centered health profile contract that unifies the current broadcast health profile, patient master record, medical profile, and frontend health profile flows.

Why this phase exists:
- The current system is split across institution profile payloads in `apps.broadcasts`, clinical records in `apps.core`, and operations data in `apps.health_dashboard` and `apps.health_ops`.
- Without a canonical contract, later features will remain fragmented.

Current system anchors:
- Backend:
  - `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/models.py`
  - `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/broadcasts/views.py`
  - `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/core/models.py`
  - `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/core/serializers.py`
- Frontend:
  - `/Users/nigel/dev/KIS/src/services/healthProfileService.ts`
  - `/Users/nigel/dev/KIS/src/services/healthcareService.ts`
  - `/Users/nigel/dev/KIS/src/screens/tabs/HealthcareScreen.tsx`

Required decisions:
- Define the canonical health profile owner:
  - likely `Profile` plus a dedicated canonical health profile model
- Decide which existing models become source-of-truth for:
  - demographics
  - institution affiliations
  - medical summary
  - emergency data
  - coverage
  - source metadata
- Decide which layers become projections only:
  - broadcast health profile should become a public or institution-facing projection, not the master health profile
  - health dashboard and health ops should remain operational layers, not the profile source of truth

Deliverables:
- A canonical backend model or contract object for health profile data
- A normalized serializer shape for all health profile reads
- A documented field ownership table
- A frontend typed contract that matches the backend canonical shape
- Backward compatibility mapping for legacy broadcast health profile payloads

Must-have canonical fields:
- profile identity
- legal and preferred name
- date of birth
- sex and gender fields if required by current product rules
- blood type
- emergency contacts
- allergies summary
- active medications summary
- diagnoses or problem list summary placeholder
- immunization summary placeholder
- institution affiliations
- primary care provider or care team placeholder
- insurance or coverage placeholder
- consent and sharing summary
- provenance metadata for each section

Implementation tasks:
- Audit overlapping fields in `BroadcastHealthProfile`, `MedicalProfile`, and `PatientMasterRecord`
- Introduce canonical serializer or model in `apps.core`
- Add compatibility layer so old broadcast payload writes are mapped into the canonical profile shape
- Refactor React Native health profile service to read the canonical endpoint first
- Keep institution-specific editing screens separate from personal medical editing screens

Implementation notes:
- Started on 2026-04-25.
- Completed first slice:
  - canonical read serializer in `apps.core`
  - patient detail canonical endpoint
  - current-user canonical endpoint resolved from `primary_contact`
  - React Native health profile service now prefers the canonical endpoint before falling back to legacy broadcast profile reads
- Completed second slice:
  - legacy `health_profile` broadcast saves now synchronize canonical patient fields into `apps.core`
  - canonical patient fields are rehydrated back into the health profile payload after save
  - the save path now treats institution rows and person health fields as separate concerns
- Verification:
  - `python3 manage.py check` passed
  - targeted Django tests are still blocked by the existing SQLite test-db migration error outside this phase
- Field ownership table:
  - `apps.core.PatientMasterRecord` owns:
    - first name
    - last name
    - date of birth
    - gender
    - primary contact
    - emergency contact
    - blood type via patient metadata
    - medical notes via patient metadata
  - `apps.broadcasts.BroadcastHealthProfile` owns:
    - institution payload shell
    - profile name
    - institution attachments and notes
    - institution rows and service rows
  - `apps.broadcasts` is now a projection layer for personal health fields:
    - `identity`
    - `emergency`
    - `primary_contact`
    - `emergency_contact`
    - `blood_type`
    - `medical_notes`
    These values are synchronized from and to the canonical patient record during save and load.

Exit note:
- Phase 1 is complete enough to begin Phase 2.
- The system now has one canonical read contract and one synchronized write bridge, even though broader clinical sections like immunizations and diagnoses still belong to later phases.

Non-goals:
- Do not build device sync in this phase
- Do not build full FHIR import in this phase
- Do not build large new patient UI flows before the contract is stable

Verification:
- `python3 manage.py check`
- targeted model and serializer tests for the canonical contract
- endpoint tests for legacy payload compatibility
- frontend typecheck for the new health profile contract if the local TypeScript baseline allows it

Exit criteria:
- There is one documented canonical health profile contract.
- Backend and frontend can read that contract consistently.
- Legacy health profile flows do not silently fork the data model.
