# KIS Messaging Product Spec

## Product Goal

KIS Messaging should support personal, community, partner/company, and institution communication with the simplicity of WhatsApp, the scale and flexibility of Telegram, and stronger KIS-specific organization controls.

The roadmap must first finish the features already visible in the UI so users do not see broken or half-implemented experiences. After that, it can add missing WhatsApp/Telegram-grade capabilities.

## Primary Messaging Areas

### Direct Messaging

Must support:

- fast message delivery in both directions;
- reliable chat list entry for both participants;
- correct sent/received alignment after app restart;
- unread count and read receipts;
- typing and presence;
- attachments, voice notes, contacts, polls, events, styled text, stickers;
- edit/delete/reply/forward/react;
- mute/archive/block/report;
- message request/accept flow;
- E2EE with device trust.

### Groups

Must support:

- group creation from contacts;
- add/remove members;
- roles and permissions;
- invite links or codes;
- admin approval settings;
- pin/star/search;
- topics/subrooms where enabled;
- moderation and report tools.

### Channels

Must support:

- channel creation;
- subscribe/unsubscribe;
- admin/editor/posting roles;
- read-only subscriber experience;
- channel discovery/listing;
- channel post composer;
- comments/replies policy if enabled;
- analytics and moderation.

### Communities

Must support:

- communities with groups/channels inside;
- announcement/default channel;
- member onboarding and permissions;
- admin roles;
- invite/application flow.

### Updates/Status

Must support:

- text/image/video/audio status;
- audience privacy;
- status replies where allowed;
- viewer/progress UI;
- status seen/unseen ring on chat avatars;
- mute status;
- expiry cleanup;
- report status.

### Calls

Must support:

- voice and video call start from direct/group rooms;
- incoming call notification;
- call history;
- missed-call state;
- retry/redial;
- group calls;
- WebRTC media reliability;
- screen share and call links in later phases.

### Partner/Company Messaging

Must support:

- partner main conversation;
- partner groups/channels/communities;
- partner role assignments;
- partner policy checks and DLP;
- audit logs;
- webhooks;
- legal hold/export hooks;
- admin-visible moderation and compliance views.

## Non-Negotiable UX Rules

- No visible feature button should silently do nothing.
- If a feature is not ready, either hide it or show a clear disabled state.
- Sending must feel instant; retries should be silent unless the final send fails.
- Cached conversations must not override fresh server data.
- History must preserve correct `fromMe` alignment after restart.
- Every messaging tab must have a real purpose. Placeholder tabs must be implemented or removed.

## Security Rules

- Do not print secret values.
- Do not silently downgrade E2EE in production.
- Do not allow a user to fetch messages/calls/conversation metadata for a conversation they do not belong to.
- Partner DLP/legal hold should apply only to partner-owned conversations.
- Calls and media uploads must be origin/auth checked.
- All internal Django/Nest calls should use signed internal auth.

## Completion Standard

A phase is complete only when:

- backend and frontend contracts match;
- local development still works;
- focused validation commands pass or blockers are recorded;
- `docs/messaging-platform-roadmap/status.md` is updated;
- `docs/BUILD_STATE.md` is updated.

