# KIS Prayer Calendar — Global 24-Hour Prayer Platform

A permanent, global prayer platform built as a coordination layer over
24 existing `apps.groups.Group` rooms — one per UTC hour. This app
(`apps.prayer`) owns no chat/calling infrastructure itself: message
history lives in the NestJS backend's MongoDB (reused via an existing
internal endpoint), and voice/video calls are the existing group-call
system reached from the mobile app's `useSocket()`. `apps.prayer` only
tracks what doesn't belong on a generic `Group`: which daily session is
live, who's leading it, prayer points, and the leadership audit trail.

## 1. Architecture

```
apps.groups.Group / GroupMembership   <- permanent room + membership/roles (REUSED, unmodified)
        │ OneToOne
apps.prayer.PrayerWatchGroup           <- hour (0-23) <-> Group mapping (NEW, permanent)
        │ FK
apps.prayer.PrayerDailySession         <- one row per (watch, UTC calendar date) (NEW, lazy+idempotent)
        │ FK                               leader / sharing_admin / stage timestamps / status
apps.prayer.PrayerLeadershipEvent      <- append-only audit trail of every claim/share/override (NEW)
apps.prayer.PrayerPointSubmission      <- one per (session, user), editable before session starts (NEW)
```

Message delivery: `apps.chat.tasks.send_system_message_as_user` (new
public function, reusing the pre-existing `_post_to_nest` helper and the
pre-existing `internal/messages/send-as-user` NestJS route — Django has
no Message model at all; this route is Nest's own, already-production-
used mechanism for "post a message as a given user", confirmed via
`apps/statuses/views.py`'s existing use of the same route for status
replies).

GO detection: `apps.partners.permissions.is_platform_go(user)` (reused,
unmodified — "GO" = General Overseer, the immutable platform-owner
identity already defined by `apps.partners.seed.GO_EMAIL`/`GO_PHONE`).

KIS announcer identity: `apps.prayer.provisioning.get_kis_system_user()`
— **a new, dedicated account**, deliberately NOT reusing
`apps.partners.seed`'s own "KIS system user" / KCAN Partner-owner
account. A real bug was found and fixed during this project: that
existing account's Partner ownership is automatically reassigned to GO
the instant GO logs in (`ensure_go_identity`), so
`Partner.objects.get(...).owner` silently resolves to **GO himself**
once he's logged in — not a separate identity at all. Since the spec
explicitly requires "do not use a developer's personal account," a
clean, separate `User` row is created instead (`set_unusable_password()`
— can never be logged into), configurable via
`PRAYER_KIS_ANNOUNCER_EMAIL`/`_USERNAME`/`_DISPLAY_NAME`/`_COUNTRY`
settings (defaults: `kis-announcer@kis.app` / `kis_announcer` / `KIS` /
`CM`).

## 2. The 24 permanent watches

Created once, idempotently, by `python manage.py provision_prayer_watches`
(see §9). Exact names in `apps/prayer/watch_names.py` (`WATCH_NAMES`) —
hour 0 is uniquely "The Final Hour Watch" (the last watch in the daily
sequence), hours 1-23 use their ordinal position. Every watch:
`join_policy=OPEN` (no subscription/partner-tier restriction), `owner`
= GO, members = GO (`OWNER`) + the KIS announcer (`MEMBER`).

## 3. Scheduling — backend-authoritative, UTC

A session's one-hour UTC window is `watch_group.hour:00:00` to `+1h` on
a given UTC calendar date — never derived from a device's local clock.
`services.get_or_create_session_for_watch` lazily creates/fetches a
session row for "now" and opportunistically advances its cached
`status` (never replaying missed transitions — just comparing `at`
against `scheduled_start`/`scheduled_end` directly, so a delayed task or
a client that hasn't opened the app in days still jumps straight to the
correct state). `services.compute_stage` derives the exact one of five
fixed stages (30/15/5/5/5 minutes) purely from elapsed time since
`scheduled_start` — reconnecting mid-stage always recomputes correctly,
never "resumes" a locally-remembered position.

**Known property of hour 0 specifically**: since `session_date` is
always `at.date()`, there is no instant "before midnight on the same
date" — hour 0's session is created already `ACTIVE`, never
`SCHEDULED`. This is correct (hour 0's watch genuinely starts at the
first instant of the day), not a bug — documented here because it
surprised this project's own test suite during development.

## 4. Leadership rules

- One ordinary-member leader per session, claimed via an atomic
  `select_for_update()` (`services.claim_leadership`) — concurrent
  claims always resolve to exactly one winner (proven by a threaded
  test, `ConcurrentLeadershipClaimTests`).
- Max 4 member-led sessions per **calendar month, UTC, across all 24
  watches combined** (not per-watch) — counted directly from
  `PrayerDailySession` rows (`leader=user, leader_kind=MEMBER`), never a
  separate cached counter. A release before the session starts clears
  `leader` back to `None`, so it correctly never consumed a slot; a
  failed claim attempt never touched the row at all.
- Release only while `status == SCHEDULED`; once `ACTIVE`, only a
  GO override or an admin-mediated handover can change the leader.
- An admin may lead solo (`claim_admin_solo_leadership`) only when no
  member has claimed — this **is** the session's leader
  (`leader_kind=ADMIN`), not an additional slot. A second admin, or an
  admin when a member already leads, is rejected.
- Admin-sharing: `request_admin_share` → the member leader
  `respond_admin_share`(accept/decline) — tracked entirely through the
  append-only `PrayerLeadershipEvent` log (doubles as the "pending
  request" queue: the most recent `SHARE_REQUESTED` with no later
  `SHARE_ACCEPTED`/`SHARE_DECLINED` after it).
- GO override (`go_override_leader`) — GO-only, always logged, notifies
  the new leader.

## 5. Admin permissions — a deliberate scoping decision

Group admin assignment reuses `GroupMembership.role` (the exact field
the generic group member-list already reads) — but is exposed through a
**new, GO-only** endpoint (`WatchAdminsView`), not the existing generic
`GroupViewSet.members/set-role` action. That generic action also lets an
existing group *admin* promote another admin — correct for ordinary
community groups, but it would violate this spec's explicit "group
admins cannot appoint other admins unless the GO explicitly grants that
permission." Modifying the generic endpoint's permission logic app-wide
to add a GO-gate was rejected as unnecessary risk to unrelated group
features; a parallel, strictly-scoped endpoint was the correct fix.

## 6. Prayer points

One per `(session, user)` (`update_or_create` — resubmitting edits in
place), editable/deletable only while `now < session.scheduled_start`.
Scoped per session, never bleeding across watches or days.

## 7. Calling

No new calling infrastructure. The room screen uses the mobile app's
existing `useSocket().startCall({ conversationId, title, media })` —
identical to how any other group conversation starts a call. "Only the
appointed leader or an authorised admin may initiate the official call"
is enforced at the point that actually matters server-side
(`set_participation_mode` — `LeadershipError(code="not_authorized")` for
anyone else) and reflected in the mobile UI via button labeling
("Start/Join" for leader/admin vs. "Join" for everyone else) — Nest's
own call-join mechanism was deliberately left untouched (it has no
concept of "prayer leader" and modifying a shared, heavily-used system
for this one feature was out of scope).

## 8. Notifications

`apps.prayer.notifications` wraps `apps.notifications.services.
create_notification` for: leadership claimed, share requested/responded,
GO override — all **not** gated on `GroupMembership.is_muted` (critical,
per spec). The one genuinely optional type, the upcoming-watch reminder
(`send_upcoming_watch_reminder`, fired by the Celery Beat task
`send_upcoming_watch_reminders` every 5 minutes against a 10-minute
window), **is** gated on `is_muted` — joining a watch and leaving it
unmuted is this feature's model of "opted in."

## 9. Setup / provisioning

```bash
python manage.py provision_prayer_watches
python manage.py provision_prayer_watches --skip-welcome-messages
```

Idempotent — running it any number of times creates nothing new for an
already-provisioned watch; it only fills in whatever's still missing
(e.g. a group that exists but whose welcome message failed to send on a
prior partial run, because Nest was unreachable). **Precondition**: the
GO account must have logged in at least once (`apps.partners.seed.
GO_EMAIL`/`GO_PHONE` must match a real `User` row) — `Group.owner` is a
required, non-nullable field, and fabricating an owner was rejected as
unsafe.

## 10. API endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/prayer/watches/` | All 24, summary + current-watch flag |
| GET | `/api/v1/prayer/watches/<hour>/` | Full detail: stage, leader, my state |
| POST | `/api/v1/prayer/watches/<hour>/leadership/claim/` | "I AM LEADING" |
| POST | `/api/v1/prayer/watches/<hour>/leadership/release/` | Before session starts only |
| POST | `/api/v1/prayer/watches/<hour>/leadership/admin-claim/` | Admin/GO only |
| POST | `/api/v1/prayer/watches/<hour>/leadership/share-request/` | Admin only |
| POST | `/api/v1/prayer/watches/<hour>/leadership/share-respond/` | `{accept: bool}`, member leader only |
| POST | `/api/v1/prayer/watches/<hour>/leadership/override/` | GO only |
| POST | `/api/v1/prayer/watches/<hour>/participation-mode/` | `{mode}`, leader/sharing-admin/admin only |
| GET/POST/DELETE | `/api/v1/prayer/watches/<hour>/prayer-points/` | List / submit-or-edit / delete own |
| GET/POST/DELETE | `/api/v1/prayer/watches/<hour>/admins/` | GO-only management |
| GET | `/api/v1/prayer/my-activity/` | Joined watches, upcoming leadership, monthly count |

## 11. Database migrations

- `apps/prayer/migrations/0001_initial.py` — four new tables
  (`PrayerWatchGroup`, `PrayerDailySession`, `PrayerLeadershipEvent`,
  `PrayerPointSubmission`). No changes to any existing table.

## 12. Tests

- `apps/prayer/test_provisioning.py` — provisioning idempotency, exact
  names, GO/KIS-announcer distinction, welcome-message delivery/failure.
- `apps/prayer/test_leadership.py` — stage transitions, claim/race/
  monthly-limit rules, admin rules, admin-sharing, GO override, prayer
  points, API permissions.
- `apps/prayer/test_notifications.py` — notification wiring, Celery
  reminder task (window matching, mute opt-out, idempotency).

Run: `python manage.py test apps.prayer --keepdb`.

## 13. Operational troubleshooting

- **"GO account has not logged in yet" on provisioning**: expected in a
  fresh environment per `apps.partners.seed`'s own documented behavior —
  have the GO log in once, then re-run the command.
- **Welcome message missing for a watch**: check
  `NEST_INTERNAL_URL`/`DJANGO_INTERNAL_TOKEN` are configured and Nest is
  reachable, then re-run `provision_prayer_watches` — it's idempotent
  and will only attempt the still-missing welcome messages (existing
  groups are left untouched).
- **A watch seems stuck mid-stage**: stage is always recomputed from
  `scheduled_start` on every read — if the client shows a stale stage,
  it hasn't re-polled; the backend itself is never "stuck."
- **Hour-0 watch never shows "Upcoming"**: expected — see §3.

## 14. Known limitations / not implemented this pass

- No automatic retry/escalation if a scheduled leader never shows up —
  the room simply stays open for text participation, matching spec
  section 16's explicit "do not fabricate a leader or a completed
  prayer session."
- The mobile GO-override UI is intentionally minimal (service + API
  exist and are tested; no dedicated admin-picker screen was built this
  pass, given time budget — the GO can act via the API directly or a
  future small screen).
