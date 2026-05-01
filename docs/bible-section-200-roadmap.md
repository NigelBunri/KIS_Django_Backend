# Bible Section 200% Roadmap

This is the continuity plan for completing the KIS Bible section as a trusted, manual-first Bible product controlled by the default KCAN partner account.

## Product Identity

- Default publisher/admin partner: `KCAN, Kingdom Citizens & Ambassadors Network`.
- Fixed partner slug: `kcan`.
- Legacy slugs that must be normalized or resolved as the same default partner: `cc`, `kis`, `christian-community-cc`.
- KCAN is the system-level partner account, default admin hub, and trusted source for official Bible content.
- Daily passages, prayers, meditations, lessons, announcements, and official Bible resources must be posted by KCAN or by admins acting through KCAN.
- AI is out of scope for launch. All spiritual content is manual, reviewed, and traceable to an admin/editor.

## Final User Tabs

Only these Bible tabs should remain in the user-facing Bible section:

1. Read
2. Daily
3. Meditations
4. Prayer Calendar
5. Reading Planner
6. Lessons
7. Settings

Any existing Bible tab or route outside this list should be removed from the frontend or moved behind admin-only/internal screens.

## Existing Assets To Preserve

- The root `bible/` directory is the canonical local translation source.
- Language directories use short language codes such as `en`, `es`, `fr`, `pt`, `zh`, and so on.
- The existing backend already has models for translations, books, chapters, verses, audio, daily devotionals, prayer requests, meditation entries, reading plans, reading history, bookmarks, notes, highlights, memory verses, preferences, cross-references, courses, lessons, quizzes, assignments, forums, live sessions, credentials, coupons, and bundles.
- The Bible import pipeline in `apps/bible/importers.py` must remain compatible with the `bible/<language>/<translation>.json` layout.

## Non-Negotiables

- No AI-generated spiritual content for launch.
- Every official devotional, meditation, prayer, and lesson must have author/editor metadata and KCAN ownership.
- Translation licensing must be audited before public release, especially modern copyrighted translations.
- Offline reading, reminders, push notifications, and alarms can be phased, but their backend structure must be designed early.
- Bible reader interactions must feel native to top Bible apps: fast, calm, readable, searchable, and highly reliable.
- All phase progress must be saved in `docs/bible-section-status.md` after each implementation pass.

## Phase 0 - Identity, Scope, And Trust Baseline

Goal: lock the Bible section around KCAN and remove ambiguity before adding more features.

Deliverables:
- Normalize the default partner from CC/Christian Community to KCAN.
- Ensure startup seeding always creates/repairs the KCAN partner and system user.
- Ensure Bible courses and official Bible content resolve to KCAN by slug, not by fuzzy name search.
- Add admin-only ownership fields for official content.
- Mark AI chat/meditation routes inactive or remove from public navigation.
- Define content states: draft, review, scheduled, published, archived.
- Add audit trail for publish/update/delete actions.

Verification:
- New database with no partner creates KCAN automatically.
- Existing database with `cc`, `kis`, or `christian-community-cc` becomes KCAN without duplicate partner rows.
- Bible course `scope=bible` returns KCAN-owned Bible courses.
- No public Bible route depends on AI.

## Phase 1 - Translation Registry And Bible Data Integrity

Goal: make the root `bible/` directory production-safe and legally safe.

Deliverables:
- Add translation metadata: language code, display language, abbreviation, full name, copyright status, license notes, source path, active/public flags.
- Add import validation for book count, chapter count, verse count, malformed JSON, empty verses, duplicate translation codes, and unsupported languages.
- Add admin import command for selected language/translation.
- Add safe public-domain defaults until licensing is resolved.
- Add migration or management command to reconcile imported translations with metadata.
- Add API filters by language, testament, translation status, and availability.

Verification:
- Import command can import one language and one translation without breaking existing data.
- Translation list never exposes inactive/unlicensed translations.
- Admin can see why a translation is hidden.

## Phase 2 - World-Class Read Section

Goal: build the main Bible reader to match and exceed top Bible app ergonomics.

Deliverables:
- Reader API supports book/chapter/verse lookup, previous/next chapter, verse ranges, full chapter, and passage references.
- Parallel translations with verse alignment.
- Reader preferences: font size, font family, line height, theme, red-letter mode, paragraph/verse mode, audio speed, default translation, default language.
- Verse selection toolbar: highlight, note, bookmark, copy, share, add to planner, add to memory, compare, open cross-references.
- Multi-color highlighting with searchable color filters.
- Highlight library grouped by color, book, date, translation, and tags.
- Notes with rich text, tags, private/public flag, verse links, and search.
- Bookmarks with folders/collections.
- Reading history with resume point and recently read list.
- Reference parser supporting inputs like `John 3:16`, `Jn 3`, `Romans 8:28-39`.
- Reader UI for mobile and desktop: top book/chapter selector, bottom reading toolbar, no clutter, strong accessibility.

Verification:
- User can select a verse, highlight with multiple colors, filter highlights by color, and return to the verse.
- User can add a whole chapter or selected verses to the Reading Planner.
- Reader remains fast on large chapters and large translation sets.

## Phase 3 - Search, Study, And Discovery

Goal: go beyond simple text search into a serious study experience.

Deliverables:
- Replace `icontains` search with database full-text search or indexed search service.
- Search modes: exact phrase, all words, any word, reference, highlighted verses, notes, bookmarks, lessons.
- Filters: translation, language, book, testament, content type, highlight color, date.
- Cross-reference browsing.
- Topic collections curated manually by KCAN.
- Manual study notes, dictionary entries, maps/timelines placeholders, people/places/themes.
- Compare translations view.
- Public share cards for verses and passages.

Verification:
- Search results are ranked and explainable.
- Highlight-color search works from both reader and search screen.
- User can find content from Bible text, notes, highlights, lessons, and plans.

## Phase 4 - Daily Manual Content

Goal: make daily content trustworthy and KCAN-controlled.

Deliverables:
- Daily passage model with date, passage reference, selected verses, title, exhortation, prayer, language, translation, publisher, editor, and publish status.
- Admin calendar for scheduling daily passages.
- User Daily tab showing today, previous days, saved days, and share actions.
- Manual review workflow for daily passages.
- No AI generation.

Verification:
- KCAN admin can schedule 30 days of daily passages.
- Users only see published content for their language/translation.
- Content has visible KCAN attribution.

## Phase 5 - Meditations Feed

Goal: create a clean feed for manual KCAN messages and videos.

Deliverables:
- Meditation feed content types: message, video.
- KCAN publisher and editor metadata.
- Feed fields: title, body, video URL/file, thumbnail, scripture references, tags, language, publish time, status.
- User interactions: save, share, mark watched/read.
- Admin scheduling and moderation.
- Remove AI meditation generation from launch UX.

Verification:
- Feed shows only message/video content from KCAN.
- Video and message cards have consistent UI and no unrelated social tabs.

## Phase 6 - Monthly Prayer Calendar

Goal: replace generic prayer requests with a monthly prayer calendar experience.

Deliverables:
- Prayer month model with month, year, title, theme, language, publisher.
- Prayer day model with date, prayer points, short exhortation, scripture references, status.
- Calendar UI for current month; users click a day to open that day’s prayer points.
- Optional save/share/mark prayed actions.
- Admin bulk entry for a full month.
- Future reminder hooks for in-app/push notifications.

Verification:
- Current month renders like a calendar.
- Clicking a day opens the right prayer points and exhortation.
- Missing days show a clean empty state.

## Phase 7 - Reading Planner Like Google Calendar

Goal: make planning Bible reading feel as capable as a calendar, but constrained to Scripture.

Deliverables:
- Bible reading event model: user, translation, passage refs, selected verse IDs/chapter IDs, date, start time, end time, recurrence, reminder offsets, status.
- Planner views: month, week, day, agenda.
- User can add selected verse/chapter from the Read tab to a date/time.
- User can create planner items only by selecting Bible passages, not free-form event text.
- Reminder structure for alarm, in-app notification, and push notification.
- Completion tracking and missed-plan recovery.

Verification:
- User can select John 3 or John 3:16-18 and schedule it for a future day/time.
- Planner can show recurring reading events.
- Reminder data is saved even before push implementation is complete.

## Phase 8 - Foundational Lessons

Goal: build KCAN lessons with Bible-reader-like UI and manual content.

Deliverables:
- Lesson content owned by KCAN.
- Lesson reader UI mirrors the Bible reader: highlighting, notes, bookmarks, search, save, progress, font/theme controls.
- Lessons support text, scripture references, video/audio attachments, reflection questions, and downloadable files.
- Foundational lesson track curated by KCAN.
- Progress and completion.
- Certificates only after verified completion where needed.

Verification:
- Lesson text can be highlighted and searched like Bible text.
- Lesson navigation is distinct from the Bible Read tab but visually consistent.
- KCAN admin can publish/update lessons.

## Phase 9 - Settings And Personalization

Goal: consolidate all Bible settings into one clear tab.

Deliverables:
- Translation/language defaults.
- Reader display settings.
- Highlight palette management.
- Audio preferences.
- Reminder preferences.
- Download/offline preferences.
- Privacy settings for notes, highlights, activity, and prayer actions.
- Notification settings for daily passage, prayer calendar, planner reminders, and lessons.

Verification:
- Settings map directly to stored preferences.
- Changing settings updates reader/planner behavior without app restart.

## Phase 10 - Offline, Sync, Notifications, And Alarms

Goal: make the Bible section reliable for daily use without constant internet.

Deliverables:
- Offline translation packages.
- Offline user data cache for notes, highlights, bookmarks, history, and planner.
- Conflict resolution rules.
- Local search indexes where feasible.
- In-app notification backend.
- Push notification hooks.
- Alarm/reminder scheduling hooks.
- Download status and storage management.

Verification:
- User can download a translation and read offline.
- User actions sync safely after reconnecting.
- Planner reminders can be queued and delivered through the notification system when enabled.

## Phase 11 - Admin Control Panel

Goal: KCAN becomes the control room for the Bible section.

Deliverables:
- KCAN dashboard for translations, daily passages, meditations, prayer calendar, planner templates, lessons, users, reports, publishing workflow, and audit logs.
- Role-based access: owner, admin, editor, reviewer, analyst.
- Draft/review/publish workflow.
- Bulk import/export.
- Content scheduling.
- Analytics: active readers, reading streaks, completion, most highlighted verses, prayer calendar engagement, lesson progress.

Verification:
- KCAN admins can manage all Bible content without direct database access.
- Non-KCAN partners cannot publish official Bible content.
- Every change is audited.

## Phase 12 - Best-In-Class Differentiators

Goal: beat the market in the areas KIS can uniquely own.

Deliverables:
- Church/community reading groups.
- Pastor/admin-led plans.
- Shared prayer months.
- Guided discipleship tracks.
- Public testimony and answered-prayer workflows, with moderation.
- Family/household reading plans.
- Scripture memory spaced repetition.
- Verse images and share templates.
- Live Bible class integration.
- Ministry analytics for KCAN admins.

Verification:
- KIS is not just a Bible reader; it becomes a complete manual, trusted discipleship system.

## Progress Rules

- After each implementation session, update `docs/bible-section-status.md`.
- Each phase must list changed files, migrations, commands run, tests run, and known gaps.
- Do not start a later phase that depends on unfinished data contracts from an earlier phase.
- Do not delete old endpoints until the replacement frontend and API are verified.
- Avoid AI features until explicitly approved for a later phase.
