# Phase 5 - Device Integrations Insights And Trends

Phase goal:
- Add the device, wellness, and longitudinal insight layer required for consumer-grade health app parity.

Why this phase exists:
- Without device ingestion and trends, the app remains mostly a clinical and institutional platform rather than a daily health companion.

Frontend targets:
- `/Users/nigel/dev/KIS/src/services`
- `/Users/nigel/dev/KIS/src/screens`

Backend targets:
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/core`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/apps/analytics`

Deliverables:
- wellness metric ingestion model
- source-aware time-series storage
- trend and alert summarization
- Apple Health and Android Health Connect integration contracts
- patient-facing charts and insight summaries

Implemented in this phase:
- added `WellnessMetric` in `apps.core` as the canonical patient-owned metric store
- added normalized metric handling for:
  - steps
  - sleep
  - heart rate
  - weight
  - blood pressure
  - blood glucose
  - workout duration
- added provenance fields for source, source label, measurement window, and clinical verification
- extended the canonical health profile and health summary serializers with:
  - `wellness_metrics`
  - `wellness_trends`
  - `wellness.recent_metrics`
  - `wellness.trends`
- added the `/api/v1/patients/wellness-metrics/` API for ingestion and review
- updated the React Native healthcare screen with:
  - wellness trend summary cards
  - quick wellness metric logging form
- added regression tests for:
  - trend summary presence
  - unit normalization for weight entries

Priority metrics:
- steps
- sleep
- heart rate
- weight
- blood glucose if product scope supports it
- blood pressure
- activity and workout summaries

Implementation tasks:
- add metric source and provenance fields
- normalize units and measurement windows
- build summary aggregations for daily, weekly, and monthly views
- add alerts for meaningful changes only after unit and source normalization is reliable
- separate verified clinical measurements from consumer device measurements

Verification:
- ingestion tests
- unit normalization tests
- trend summary tests
- frontend rendering tests if local tooling allows

Exit criteria:
- The app can show time-series health trends with clear source provenance and meaningful summaries.

Phase result:
- complete

Remaining boundary before native device sync:
- Apple Health and Android Health Connect are now represented by backend/source contracts, but native SDK ingestion and background sync are still Phase 6+ integration work rather than missing data model work.
