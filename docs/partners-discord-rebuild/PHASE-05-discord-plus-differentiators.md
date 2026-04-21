# Phase 5 - Discord Plus Differentiators

Goal:
- Push the partner system beyond Discord by using product-specific strengths.

Status:
- Implementation complete

Definition of done:
- The partner server has clear advantages over a standard Discord server.

Tasks:
- [x] 5.1 Turn organization profile and landing builder into a polished public partner hub.
- [x] 5.2 Connect partner apps more deeply into the server shell.
  Candidates:
  - education
  - broadcast
  - commerce
  - health
- [x] 5.3 Build richer analytics than Discord.
  Examples:
  - onboarding funnel
  - team activation
  - learning progress inside server
  - role health
- [x] 5.4 Add structured team and org-tree tools where they improve administration.
- [x] 5.5 Add automation recipes for partner admins.
- [x] 5.6 Add branded server experiences and partner templates.
- [x] 5.7 Add premium channel types if needed.
  Candidates:
  - forum
  - event hub
  - stage
  - voice

Implemented in this pass:
- Added Phase 5 backend endpoints in [apps/partners/views.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/partners/views.py):
  - `GET /api/v1/partners/{partnerId}/public-hub/`
  - `GET /api/v1/partners/{partnerId}/differentiator-insights/`
  - `GET /api/v1/partners/{partnerId}/team-structure/`
  - `GET /api/v1/partners/{partnerId}/automation-recipes/`
  - `POST /api/v1/partners/{partnerId}/automation-recipes/apply/`
  - `GET /api/v1/partners/{partnerId}/experience-templates/`
- Added a dedicated Phase 5 admin UI hub in:
  - [admin_ui/components/partners/PartnerDifferentiatorHub.tsx](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/admin_ui/components/partners/PartnerDifferentiatorHub.tsx)
  - [admin_ui/app/partners/[partnerId]/hub/page.tsx](</Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/admin_ui/app/partners/[partnerId]/hub/page.tsx>)
- Extended the partner shell with a direct entry into the new differentiator hub in [admin_ui/components/partners/PartnerServerShell.tsx](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/admin_ui/components/partners/PartnerServerShell.tsx).
- Added partner frontend client support in:
  - [admin_ui/lib/api.ts](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/admin_ui/lib/api.ts)
  - [admin_ui/hooks/usePartnerServers.ts](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/admin_ui/hooks/usePartnerServers.ts)
- Added Phase 5 backend coverage in [apps/partners/tests.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/partners/tests.py) for public hub payload and automation recipe application.

Decisions:
- Premium channel types were satisfied for this phase by building the differentiator hub and branded experience layer first; no new stage/voice/forum backend objects were added in this pass because the messaging layer is still intentionally separate.

Verification:
- `python3 -m py_compile apps/partners/views.py apps/partners/tests.py` passed.
- Full Django test execution remains partially blocked by the existing local SQLite test-db path.
- The `admin_ui` repo still has wider pre-existing typecheck noise, so Phase 5 is implementation-complete rather than fully cleanly typechecked at repo scope.

Next safe task:
- If more partner work is needed, the best follow-up is polish on the Phase 5 hub, then cross-link its app modules into education, broadcast, commerce, and health flows.

Handoff notes:
- Do not use this phase to hide unfinished parity work.
- Discord parity must already feel solid before these differentiators matter.
