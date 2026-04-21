# Phase 1 - Foundation And Hardening

Goal:
- Make the current partner system safe to build on.
- Remove obvious bugs and permission leaks.
- Establish one clear partner-server architecture before adding Discord-style features.

Status:
- Complete

Definition of done:
- Critical partner bugs fixed.
- Permission model clarified and enforced consistently.
- Core tests added for partners.
- Partner server architecture decision written and reflected in code comments or docs where needed.
- Phase 1 verification passes.

Primary code areas:
- [apps/partners/views.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/partners/views.py)
- [apps/partners/services.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/partners/services.py)
- [apps/partners/models.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/partners/models.py)
- [apps/partners/serializers.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/partners/serializers.py)
- [apps/partners/tests.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/partners/tests.py)
- [apps/communities/views.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/communities/views.py)
- [apps/groups/views.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/groups/views.py)
- [apps/channels/views.py](/Users/nigel/All%20other%20files/CC/KIS/main_kis_bakend/backend/kis/apps/channels/views.py)

Tasks:
- [x] 1.1 Fix critical runtime bugs in partner services.
  Files:
  - `apps/partners/services.py`
  Work:
  - Replace invalid `datetime.timedelta` usage with correct imports/usages.
  - Check export scheduling, webhook retry logic, and analytics series generation.
  Done when:
  - Service code runs without `NameError` on those paths.

- [x] 1.2 Close the partner settings permission leak.
  Files:
  - `apps/partners/views.py`
  Work:
  - Restrict `PATCH /settings` so ordinary accessible users cannot change partner feature flags.
  - Require owner/admin/manager or explicit permission.
  Done when:
  - Non-admin members cannot toggle partner settings.

- [x] 1.3 Unify partner access rules.
  Files:
  - `apps/partners/views.py`
  - `apps/partners/services.py`
  - optionally `apps/communities/views.py`, `apps/groups/views.py`, `apps/channels/views.py`
  Work:
  - Decide the canonical access source for partner server membership.
  - Reduce drift between partner membership, partner main conversation membership, and downstream space access.
  - Document the rule in code comments where helpful.
  Decision to adopt:
  - `PartnerMembership` is business membership.
  - conversation membership is transport/chat membership derived from business membership.
  Done when:
  - access-sensitive partner endpoints rely on one clear rule set.

- [x] 1.4 Resolve partner main conversation inconsistency.
  Files:
  - `apps/partners/serializers.py`
  - `apps/partners/services.py`
  Work:
  - Choose whether partner main conversation is `POST` or `GROUP`.
  - Make serializer and service agree.
  - Keep downstream assumptions consistent.
  Recommended choice:
  - Keep partner main conversation as `POST` if the partner home is feed-first.
  Done when:
  - no conflicting creation paths remain.

- [x] 1.5 Add partner test coverage for critical flows.
  Files:
  - `apps/partners/tests.py`
  - add helper test modules if needed
  Work:
  - test partner create
  - test settings permission enforcement
  - test apply/subscribe paths
  - test organization apps visibility
  - test deactivation/reactivation
  Done when:
  - `manage.py test apps.partners` runs meaningful tests instead of zero tests.

- [x] 1.6 Write the partner server architecture decision.
  Files:
  - this file
  - optionally `docs/decisions/` if a formal ADR is preferred later
  Work:
  - Confirm the hierarchy for now:
    `Partner -> categories -> channels`
  - Clarify how current `Community` and `Group` fit:
    `Community` = large sub-organization or public cluster
    `Group` = private team room / subgroup
  - Do not introduce new schema yet unless needed for Phase 2.
  Done when:
  - future work has a stable direction and no one is guessing the model.

Architecture decision for Phase 1:
- Treat `Partner` as the Discord-like server.
- Treat `Channel` as the closest current equivalent to a server channel.
- Treat `Group` as a private room or subgroup under a channel or community.
- Treat `Community` as a large partner subdivision, not the top-level server.
- Phase 2 will formalize category and overwrite structure around this.

Verification:
- Run `../env/bin/python manage.py test apps.partners`
- Run any targeted tests added for communities/groups/channels if access logic changes there.
- Manually verify:
  - partner creation
  - settings update permissions
  - subscribe/apply flow
  - organization apps visibility

Verification result:
- `../env/bin/python manage.py test apps.partners` passed with 6 tests on April 20, 2026.

Implementation notes:
- Fixed invalid `datetime.timedelta` calls in `apps/partners/services.py`.
- Standardized partner main conversation creation on `ConversationType.POST`.
- Added shared partner access helpers in `apps/partners/services.py` and wired partner viewsets to use them.
- Allowed public `apply` and `subscribe` detail actions to resolve active partners before membership exists.
- Restricted `PATCH /api/v1/partners/{id}/settings/` to partner managers/admins/owners.
- Added API coverage for create, apply, subscribe, organization app visibility, settings permission, and deactivate/reactivate flows.

Handoff notes:
- Do not start category or overwrite schema work in this phase.
- If Phase 1 runs long, still finish tasks 1.1 to 1.5 before moving on.
- Phase 1 is complete.
- The next implementation task is Phase 2 Task 2.1.
