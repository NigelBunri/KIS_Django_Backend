# Bible Launch Manual QA

Use this checklist for final device QA before production release.

## Roles

- Logged-out public user.
- Logged-in normal user.
- Non-KCAN partner admin.
- KCAN admin.

## Public / Logged Out

- Bible appears as a main app section.
- Only these tabs show: Read, Daily, Meditations, Prayer Calendar, Reading Planner, Lessons, Settings.
- Read opens to a public/licensed translation.
- Language and translation dropdowns only show public/licensed translations.
- Restricted or unreviewed translations do not appear.
- Personal actions such as highlights, comments, bookmarks, planner, preferences, and control room require login or fail gracefully.

## Logged-In Normal User

- Long-press a verse and add a highlight.
- Reopen filter sheet, choose Highlights, and confirm the verse appears.
- Filter highlights by color.
- Long-press a verse and add a comment.
- Reopen filter sheet, choose Comments, search the comment text, and confirm the verse/comment appears.
- Long-press a verse and add a bookmark.
- Reopen filter sheet, choose Bookmarks, and confirm the verse appears.
- Add a verse to Reading Planner from the Read tab.
- Open Reading Planner and confirm the event appears in the selected day or the upcoming list.
- Create a planner event from selected verses.
- Edit date/time, recurrence, reminder offsets, and reminder channels.
- Mark the event completed, then delete it.
- Close and reopen the Bible section; highlights, comments, bookmarks, and events should remain visible.

## Daily / Meditations / Prayer Calendar / Lessons

- Daily shows today's KCAN passage, exhortation, scripture refs, and prayer.
- Daily recent history loads.
- Meditations show KCAN message/video feed items.
- Prayer Calendar shows the current month and clickable prayer days.
- Selecting a prayer day shows prayer points, scripture refs, and exhortation.
- Lessons show KCAN foundational course, module, lesson reader, and completion action.

## KCAN Admin

- Settings shows KCAN Control Room.
- Translation registry scan works.
- Registry rows show license review status.
- Public-domain translations can remain public/licensed.
- Non-public-domain translations require explicit review approval before public exposure.
- Rejecting a translation disables public/licensed flags.
- Content audit list loads.

## Non-KCAN Partner Admin

- Bible Control Room does not appear.
- Partner-created apps remain in the partner section launcher.
- Partner app renderer shows partner-defined tabs/content blocks.

## Push / Reminder Readiness

- The app stores FCM/APNS tokens when Firebase messaging is configured.
- The app calls `/api/v1/notification-device-tokens/register/` after receiving a token.
- `python3 manage.py dispatch_bible_reading_reminders --dry-run` runs without errors.
- With `FCM_SERVER_KEY` configured, due planner reminders with `push` channel should create push deliveries.
- Without `FCM_SERVER_KEY`, push deliveries stay pending with a configuration message instead of breaking the app.

## Final Release Blockers

- Complete this checklist on at least one iOS device/simulator and one Android device/emulator.
- Configure production Firebase/APNS credentials and verify real push delivery.
- Human-review every modern/non-public-domain translation before setting it public.
