# Partners Discord Rebuild

This folder is the single source of truth for turning the partner system into a Discord-class product.

Use this folder when:
- continuing implementation after chat context is lost
- switching to another model
- handing work between user and assistant
- deciding what to build next

Rules for maintaining this folder:
- Update the current phase status before and after meaningful work.
- Do not store brainstorming here. Store only decisions, tasks, status, and implementation notes.
- If a task is started, note the exact files touched.
- If a task is blocked, write the blocker and the next safe fallback task.

Current target:
- Build a partner server system that feels like Discord or better.
- Exclude the message-system internals unless a phase explicitly requires integration points.

Current status:
- Active phase: Phase 5
- Phase 1 status: complete
- Phase 2 status: implementation-complete, verification path partially blocked by unrelated SQLite migration issues
- Phase 3 status: shell foundation complete, deeper UX still pending
- Phase 4 status: implementation-complete, verification still partially blocked by the local SQLite test-db path
- Phase 5 status: implementation-complete, verification still partially blocked by the existing local test and frontend baseline noise
- Next action: polish or expand the Phase 5 differentiator hub, or move into the next system area

Phase map:
- [Phase 1 - Foundation And Hardening](</Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/partners-discord-rebuild/PHASE-01-foundation-and-hardening.md>)
- [Phase 2 - Server IA And Permission Overwrites](</Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/partners-discord-rebuild/PHASE-02-server-ia-and-permission-overwrites.md>)
- [Phase 3 - Frontend Server Shell](</Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/partners-discord-rebuild/PHASE-03-frontend-server-shell.md>)
- [Phase 4 - Invites Onboarding Moderation And Presence](</Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/partners-discord-rebuild/PHASE-04-invites-onboarding-moderation-and-presence.md>)
- [Phase 5 - Discord Plus Differentiators](</Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/docs/partners-discord-rebuild/PHASE-05-discord-plus-differentiators.md>)

Execution order:
1. Finish all critical items in Phase 1.
2. Start Phase 2 only after Phase 1 verification passes.
3. Start Phase 3 after the Phase 2 backend contract is stable.
4. Phase 4 operational systems are now implemented in backend and frontend shell.
5. Start Phase 5 only after Discord-parity foundations work.

Resume protocol:
1. Open this file.
2. Open the active phase file.
3. Start with the first unchecked task in that phase.
4. If code changes were made but not verified, run the verification items listed in that phase before moving on.
