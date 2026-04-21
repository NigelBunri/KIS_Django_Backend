# Phase 2 - Server IA And Permission Overwrites

Goal:
- Turn the partner model into a true server structure.
- Add category grouping and Discord-style permission overwrites.

Status:
- In progress
- Tasks 2.1 to 2.3 are implemented
- Tasks 2.4 to 2.6 are implemented
- Task 2.7 test coverage was added, but full runner verification is still partially blocked by an older unrelated SQLite migration path
- Next action: stabilize Phase 2 verification path, then decide whether to finish any remaining permission edge cases or move into Phase 3

Definition of done:
- Partner sidebar hierarchy is representable by backend data.
- Categories exist and channels can belong to them.
- Role and member overwrites can allow/deny visibility and actions per channel.
- Effective permissions can be resolved deterministically.

Primary code areas:
- `apps/partners`
- `apps/channels`
- `apps/chat`
- any new migration files created in those apps

Tasks:
- [x] 2.1 Add partner server category model.
- [x] 2.2 Add explicit ordering for categories and channels.
- [x] 2.3 Add channel type system needed for server IA.
  Minimum:
  - text
  - announcement
  - private
  Future candidates:
  - forum
  - voice
  - stage
- [x] 2.4 Add permission overwrite model.
  Scope:
  - role overwrite
  - member overwrite
  - allow/deny behavior
- [x] 2.5 Implement effective permission resolution service.
  Needs:
  - server baseline permissions
  - role assignments
  - channel overwrites
  - member-specific overwrites
- [x] 2.6 Enforce channel visibility and write permissions in APIs.
- [ ] 2.7 Add tests for overwrite resolution and visibility filtering.
  Status:
  - tests added in `apps/channels/tests.py`
  - direct shell/API verification completed
  - full `manage.py test` path still blocked by unrelated SQLite migration setup outside the partner/channel scope

Recommended deliverables:
- server category model
- overwrite model
- permission resolver service
- channel list endpoint that returns only visible channels
- tests for hidden staff channel scenarios

Verification:
- user with base membership sees only allowed channels
- admin sees staff channels
- denied member cannot read restricted channel
- explicit member allow can override a broader deny if intended by design

Implemented in this phase so far:
- Added `PartnerServerCategory` in `apps/partners/models.py` with migration `apps/partners/migrations/0036_partnerservercategory.py`
- Added partner server channel organization fields in `apps/channels/models.py`
  - `category`
  - `channel_type`
  - `order`
- Added category-aware validation and serializer output in `apps/channels/serializers.py`
- Added partner server category endpoints and server layout endpoint in `apps/partners/views.py`
  - `GET/POST /api/v1/partners/<partner_id>/server-categories/`
  - `PATCH/DELETE /api/v1/partners/<partner_id>/server-categories/<category_id>/`
  - `GET /api/v1/partners/<partner_id>/server-layout/`
- Added dedicated partner channel API prefix in `config/urls.py`
  - partner channel routes now live under `/api/v1/partner-channels/`
- Updated channel list ordering in `apps/channels/views.py` for partner-scoped requests
- Added admin registration in `apps/partners/admin.py` and `apps/channels/admin.py`
- Added regression coverage in `apps/channels/tests.py`
- Added `PartnerChannelPermissionOverwrite` in `apps/partners/models.py` with migration `apps/partners/migrations/0037_partnerchannelpermissionoverwrite_and_more.py`
- Added `PartnerChannelPermissionOverwriteSerializer` in `apps/partners/serializers.py`
- Added partner channel permission resolution in `apps/partners/services.py`
  - `resolve_partner_channel_permissions`
  - `partner_user_can_view_channel`
  - `partner_user_can_send_channel`
  - `partner_user_can_manage_channel`
  - `filter_partner_channels_for_user`
- Enforced channel visibility and management in `apps/channels/views.py`
  - partner channel create now requires partner management
  - partner channel list/retrieve now filter hidden channels
  - partner channel subscribe now respects read-only access
  - partner channel overwrite management endpoints added:
    - `GET/POST /api/v1/partner-channels/channels/<channel_id>/overwrites/`
    - `PATCH/DELETE /api/v1/partner-channels/channels/<channel_id>/overwrites/<overwrite_id>/`
- Updated `apps/channels/serializers.py` so `can_post` reflects effective partner channel permissions

Verification completed:
- `DATABASE_URL="" TEST_DATABASE_URL="" TEST_DATABASE_MIRROR="default" ../env/bin/python manage.py test apps.partners apps.channels`
- Result: `Ran 9 tests ... OK`
- `DATABASE_URL="" python3 manage.py migrate partners`
- `DATABASE_URL="sqlite:////Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/kis/test_db.sqlite3" python3 manage.py migrate partners`
- Direct Django shell/API verification on migrated SQLite schema confirmed:
  - member-specific allow can restore visibility while keeping send disabled
  - manager role overwrite can restore visibility and send access in a private category
  - subscribe returns `READONLY` when `view_channel` is allowed but `send_messages` is denied

Notes:
- `config/settings/local.py` was hardened so local test runs do not depend on mirrored Postgres by default.
- Full fresh SQLite test database creation is still blocked by an older unrelated migration elsewhere in the repo. The current verification path uses the existing local SQLite schema mirror and passes for `apps.partners` and `apps.channels`.
- The repo still contains unrelated SQLite schema drift in other apps, so the full `manage.py test` runner remains unreliable for fresh database creation. This is an environment issue, not a partner overwrite logic failure.

Handoff notes:
- Current overwrite precedence order:
  - partner owner implicit full access
  - partner baseline permissions from membership and scoped role assignments
  - channel role allows
  - channel role denies
  - channel member allows
  - channel member denies
  - if `view_channel` is absent, `send_messages` and `manage_channel` are also stripped
- Keep permission logic in a service, not scattered across viewsets.
- If continuing Phase 2 before Phase 3, focus next on:
  - tightening member list and unread-state groundwork around the same visibility rules
  - adding category-level inherited overwrites if Discord parity is still the target
  - cleaning the project-wide SQLite migration blockers so `manage.py test` can validate these new cases normally
