# Phase 2 - Personal Medical Summary And Emergency Card

Phase goal:
- Build the patient-facing health summary and emergency-ready profile that make the system behave like a serious personal health app.

Why this phase exists:
- The backend already has medications, allergies, vitals, encounters, family, and consent foundations in `apps.core`, but they are not assembled into one polished personal summary.

Primary backend targets:
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/core/models.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/core/serializers.py`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/core/views.py`

Primary frontend targets:
- `/Users/nigel/dev/KIS/src/services/healthcareService.ts`
- `/Users/nigel/dev/KIS/src/services/healthProfileService.ts`
- `/Users/nigel/dev/KIS/src/screens/tabs/HealthcareScreen.tsx`

Deliverables:
- A single patient summary endpoint
- A patient summary screen focused on the individual, not the institution
- Emergency card or Medical ID contract
- Share-safe emergency view payload
- Clear last-updated timestamps and provenance on each section

Required summary sections:
- identity and demographics
- emergency contacts
- blood type
- allergies
- medications
- diagnoses or condition summary
- recent vitals and trend summary
- appointments and recent encounters
- care team or primary institution
- consents and sharing state

Emergency card requirements:
- fast readable card format
- minimal but critical medical data
- emergency contacts
- current medications
- severe allergies
- chronic conditions
- blood type
- optional insurance and provider contacts
- explicit field-level visibility controls

Implementation tasks:
- Extend `PatientMasterRecordDetailSerializer` or add a new patient summary serializer
- Add emergency profile fields and serializer contract
- Add audit-safe share endpoint or signed read path for emergency access if product rules allow
- Build a dedicated patient summary section in the React Native app
- Keep emergency card reads lightweight and stable for offline caching if the frontend supports it

Implementation notes:
- Completed on 2026-04-25.
- Backend:
  - added `PatientHealthSummarySerializer`
  - added `PatientEmergencyCardSerializer`
  - added patient-level and current-user endpoints for:
    - `health-summary`
    - `emergency-card`
    - `my-health-summary`
    - `my-emergency-card`
- Frontend:
  - added route bindings and service helpers for Phase 2 endpoints
  - updated `HealthcareScreen` to render:
    - patient identity summary
    - care snapshot
    - affiliations
    - top allergies
    - active medications
    - recent vitals
    - emergency card

Verification:
- `python3 manage.py check` passed
- backend tests remain blocked by the existing SQLite test-db migration issue outside this phase
- React Native full typecheck remains blocked by unrelated project-wide TypeScript errors

Exit note:
- Phase 2 is complete enough to begin Phase 3.
- The app now has a real patient-facing summary and an emergency card layer on top of the canonical Phase 1 contract.

Verification:
- serializer and API tests for full summary payload
- permission tests for emergency and normal views
- `python3 manage.py check`

Exit criteria:
- A user can open one screen and see a coherent personal health summary.
- Emergency-ready data can be rendered without navigating institution management flows.
