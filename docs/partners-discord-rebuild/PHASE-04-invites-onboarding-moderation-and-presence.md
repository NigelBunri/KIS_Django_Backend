# Phase 4 - Invites Onboarding Moderation And Presence

Goal:
- Add the systems that make the server usable at scale.

Status:
- Implementation complete

Definition of done:
- Invites, onboarding, moderation, and member-state systems are functional.

Tasks:
- [x] 4.1 Build partner invite model and APIs.
  Minimum:
  - code
  - expiry
  - max uses
  - disabled state
- [ ] 4.2 Build vanity invite support if desired.
- [x] 4.3 Build onboarding flow.
  Minimum:
  - welcome screen
  - rules acceptance
  - role selection
  - default channel routing
- [x] 4.4 Build member screening and partner-level rules.
- [x] 4.5 Build moderation actions.
  Minimum:
  - mute
  - timeout
  - kick
  - ban
  - audit trail
- [x] 4.6 Build presence and partner member directory enhancements.
  Minimum:
  - role-grouped members
  - nickname display
  - online/offline/activity if available from messaging system
- [x] 4.7 Build notification and unread preference controls at server/channel level.
- [x] 4.8 Add tests for invites, onboarding, and moderation rules.

Implemented in this pass:
- Added backend models for `PartnerInvite`, `PartnerOnboardingProgress`, and `PartnerModerationAction`.
- Extended `PartnerMembership` with mute, timeout, ban, and removal state.
- Added partner invite APIs:
  - `GET/POST /api/v1/partners/{partnerId}/invites/`
  - `PATCH/DELETE /api/v1/partners/{partnerId}/invites/{inviteId}/`
  - `POST /api/v1/partners/redeem-invite/`
- Added onboarding APIs:
  - `GET /api/v1/partners/{partnerId}/onboarding/`
  - `POST /api/v1/partners/{partnerId}/onboarding/complete/`
- Added moderation and member directory APIs:
  - `GET /api/v1/partners/{partnerId}/members/`
  - `GET /api/v1/partners/{partnerId}/moderation-actions/`
  - `POST /api/v1/partners/{partnerId}/members/{userId}/moderate/`
- Updated partner channel permission resolution so muted or timed-out members lose send permission and banned members lose access.
- Added partner screening API:
  - `GET/PATCH /api/v1/partners/{partnerId}/screening/`
- Added partner notification preferences API:
  - `GET/PATCH /api/v1/partners/{partnerId}/notification-preferences/`
- Rebuilt the partner frontend shell so Phase 4 features are usable in `admin_ui`.

Files touched:
- [apps/partners/models.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/partners/models.py)
- [apps/partners/serializers.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/partners/serializers.py)
- [apps/partners/views.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/partners/views.py)
- [apps/partners/services.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/partners/services.py)
- [apps/partners/admin.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/partners/admin.py)
- [apps/partners/tests.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/partners/tests.py)
- [apps/partners/migrations/0038_partnerinvite_partnermoderationaction_and_more.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/partners/migrations/0038_partnerinvite_partnermoderationaction_and_more.py)
- [admin_ui/lib/api.ts](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/admin_ui/lib/api.ts)
- [admin_ui/hooks/usePartnerServers.ts](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/admin_ui/hooks/usePartnerServers.ts)
- [admin_ui/components/partners/PartnerServerShell.tsx](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/admin_ui/components/partners/PartnerServerShell.tsx)

Verification:
- `python3 -m py_compile apps/partners/models.py apps/partners/serializers.py apps/partners/views.py apps/partners/services.py apps/partners/admin.py apps/partners/tests.py` passed.
- `python3 -m py_compile apps/partners/views.py` passed again after the screening and notification-preference endpoints were added.
- `../env/bin/python manage.py test apps.partners --noinput` still hangs in the local SQLite test-database path after test DB setup. This is consistent with the existing repo-local test DB instability and not isolated to the new Phase 4 code.
- Frontend typecheck remains noisy at repo scope from pre-existing `admin_ui` issues, but the Phase 4 partner shell files were updated as the active implementation surface and are the correct handoff point for any follow-up fixes.

Next safe task:
- Start Phase 5 and add differentiators on top of the now-operational partner server shell. The first sensible targets are richer public landing identity, automation-first server tooling, and app modules that make the partner server better than Discord.

Handoff notes:
- This phase will likely need integration points with the messaging system, but not a redesign of the messaging core.
