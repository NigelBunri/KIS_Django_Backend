# KIS 120 Percent Staging Evidence Template

Release:

Date:

Environment:

Evidence owner:

## Build Versions

- Django commit/build id:
- Nest build id:
- React Native iOS build:
- React Native Android build:
- Public web build:

## Environment Proof

Record only present/absent or pass/fail. Do not paste secrets.

| Area | Evidence Link | Status | Notes |
| --- | --- | --- | --- |
| `DEBUG=False` |  |  |  |
| `ALLOWED_HOSTS` real values configured |  |  |  |
| Django CORS/CSRF origins configured |  |  |  |
| Nest HTTP CORS configured |  |  |  |
| Socket.IO origins configured |  |  |  |
| Redis/cache throttling active |  |  |  |
| Internal signatures required |  |  |  |
| Firebase/admin credentials mounted safely |  |  |  |
| Flutterwave sandbox credentials mounted safely |  |  |  |
| Verification provider sandbox credentials mounted safely |  |  |  |
| AI live provider calls disabled |  |  |  |
| Public indexing disabled/enabled with approval |  |  |  |

## Automated Validation

| Command | Evidence Link | Result | Blocker |
| --- | --- | --- | --- |
| `python3 manage.py check` |  |  |  |
| `python3 manage.py makemigrations --check --dry-run` |  |  |  |
| `python3 manage.py verify_deployment_security --target-production` |  |  |  |
| `python3 scripts/security/kis_120_launch_evidence_check.py` |  |  |  |
| Django focused tests |  |  |  |
| Nest `pnpm tsc --noEmit` |  |  |  |
| RN `npm run typecheck -- --pretty false` |  |  |  |
| RN `npx eslint . --quiet` |  |  |  |

## Manual QA Evidence

| Domain | iOS Evidence | Android Evidence | Status | Notes |
| --- | --- | --- | --- | --- |
| Login/profile/main tabs |  |  |  |  |
| Messaging direct/group/subrooms |  |  |  |  |
| Messaging media safety |  |  |  |  |
| Broadcast/channels/studio |  |  |  |  |
| Public web/embed metadata |  |  |  |  |
| Bible/spiritual growth |  |  |  |  |
| Commerce/payment |  |  |  |  |
| Education/payment |  |  |  |  |
| Health/payment |  |  |  |  |
| Partners/workspaces |  |  |  |  |
| Notifications/badges |  |  |  |  |
| Verification/trust badges |  |  |  |  |
| Accessibility/family modes |  |  |  |  |
| Offline/low-bandwidth |  |  |  |  |

## Provider Evidence

| Provider | Evidence Link | Status | Notes |
| --- | --- | --- | --- |
| Flutterwave payment links |  |  |  |
| Flutterwave signed callbacks |  |  |  |
| Firebase push delivery |  |  |  |
| Verification sandbox callbacks |  |  |  |
| Explicit-content provider disabled/enabled proof |  |  |  |
| AI provider disabled/enabled proof |  |  |  |

## Safety Evidence

| Gate | Evidence Link | Status | Notes |
| --- | --- | --- | --- |
| Pornography/explicit media blocked |  |  |  |
| Child/youth content controls |  |  |  |
| Private media protection |  |  |  |
| Public/private content separation |  |  |  |
| Moderation queue/actions |  |  |  |
| Report/block/hide/mute |  |  |  |
| Audit log visibility |  |  |  |

## Recovery Evidence

| Recovery Area | Evidence Link | Status | Notes |
| --- | --- | --- | --- |
| Database backup |  |  |  |
| Restore test |  |  |  |
| App rollback |  |  |  |
| Environment rollback |  |  |  |
| Media/storage rollback |  |  |  |
| Secret rotation tabletop |  |  |  |
| Payment incident rollback |  |  |  |

## Final Decision

Decision: GO / NO-GO / CONDITIONAL GO

Required sign-offs:

- Product:
- Engineering:
- Security/privacy:
- Child safety:
- Payments:
- Verification/trust:
- Operations:
- Pastoral/Christian principles:

Open blockers:

1.
2.
3.
