# Health Profile 200 Apple

This folder is the single source of truth for upgrading the KIS health profile system into a world-class health platform that can compete with or exceed Apple Health while preserving the current KIS structure.

Use this folder when:
- continuing implementation after chat context is lost
- switching to another model
- handing work between user and assistant
- deciding what to build next

Rules for maintaining this folder:
- Update `STATUS.md` before and after meaningful work.
- Record exact files touched for each completed slice.
- Do not store brainstorming here. Store decisions, scope, tasks, blockers, and verification only.
- If a phase is partially implemented, note the verified part and the unverified part separately.

Benchmark note:
- There is no clean official source for a single "most popular health app in the world".
- This program uses Apple Health as the practical benchmark because of its installed base and official feature depth in unified health records, Medical ID, medications, trends, device ingestion, and sharing.

Current verdict:
- KIS is already strong as a healthcare operations and institution platform.
- KIS is not yet complete as a unified personal health app.
- The main gap is architectural coherence around one canonical person-centered health profile.

Current system shape:
- `apps.broadcasts` contains the broadcast health profile and institution membership layer.
- `apps.core` contains the patient and clinical record layer.
- `apps.health_dashboard` and `apps.health_ops` contain institution, workflow, and operational layers.
- `/Users/nigel/dev/KIS` contains the React Native health profile, healthcare, and health institution frontend flows.

Phase map:
- [Phase 1 - Canonical Profile And Data Contract](</Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/health-profile-200-apple/phase-01-canonical-profile-and-data-contract.md>)
- [Phase 2 - Personal Medical Summary And Emergency Card](</Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/health-profile-200-apple/phase-02-personal-medical-summary-and-emergency-card.md>)
- [Phase 3 - Problem List Immunizations And Records Vault](</Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/health-profile-200-apple/phase-03-problem-list-immunizations-and-records-vault.md>)
- [Phase 4 - Interoperability Provider Sync And Import Export](</Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/health-profile-200-apple/phase-04-interoperability-provider-sync-and-import-export.md>)
- [Phase 5 - Device Integrations Insights And Trends](</Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/health-profile-200-apple/phase-05-device-integrations-insights-and-trends.md>)
- [Phase 6 - UX Split Privacy Sharing And Hardening](</Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/health-profile-200-apple/phase-06-ux-split-privacy-sharing-and-hardening.md>)

Execution order:
1. Finish Phase 1 before expanding feature breadth.
2. Start Phase 2 only after the canonical profile contract is stable.
3. Start Phase 3 only after the Phase 2 summary payload and emergency contract are in place.
4. Start Phase 4 only after the internal data model is strong enough to map externally.
5. Start Phase 5 only after the canonical profile can safely store time-series and source metadata.
6. Start Phase 6 after the product surface is functionally complete enough to consolidate and harden.

Resume protocol:
1. Open this file.
2. Open [STATUS.md](</Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/health-profile-200-apple/STATUS.md>).
3. Open the active phase file.
4. Start with the first unchecked task.
5. Run the verification items listed in the active phase before moving to the next phase.
