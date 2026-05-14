# Phase 08 - Partner Messaging Completion

Purpose: complete KIS's strongest unique messaging advantage: partner/company/institution messaging with governance.

## Files To Inspect First

Django:

- `apps/partners/models.py`
- `apps/partners/views.py`
- `apps/partners/serializers.py`
- `apps/partners/services.py`
- `apps/chat/views.py`
- `apps/chat/models.py`

Frontend:

- Search partner messaging screens:
  - `rg -n "partner.*conversation|conversation.*partner|partner.*channel|partner.*chat|openChat|main_conversation" /Users/nigel/dev/KIS/src -S`
- Likely areas:
  - `/Users/nigel/dev/KIS/src/components/partners/`
  - `/Users/nigel/dev/KIS/src/screens/partners/`
  - `/Users/nigel/dev/KIS/src/Module/ChatRoom/`

Nest:

- `src/chat/integrations/django/django-conversation.client.ts`
- `src/realtime/handlers/messages.ts`

## Required Work

### 1. Product decision

Decide and document:

- Are partner conversations shown in the main chat list?
- Or are they shown only inside partner workspace?
- If hidden from main list, show a clear partner inbox entry point.

Do not leave partner conversations invisible to users who need them.

### 2. Partner conversation access

Verify:

- partner owner/admin can manage partner chat;
- subscribers/members get correct readonly/member role;
- banned/removed users cannot read or send;
- partner channels respect permission overwrites.

### 3. DLP and policy UX

In partner-owned conversations:

- if DLP blocks message, show user-safe error;
- if DLP warns, show warning before send or after send depending policy;
- log audit event;
- do not apply partner DLP to ordinary direct chats.

### 4. Webhook dispatch

Verify partner webhooks are called for approved events:

- message sent;
- member joined;
- moderation action.

Failures should not break user chat unless the partner policy explicitly requires blocking.

### 5. Admin/audit UI

Add or connect screens showing:

- recent partner messaging audit events;
- blocked/warned DLP messages metadata, not secrets;
- channel permission overwrites;
- export/legal hold status.

## Validation

```bash
python3 manage.py check
python3 manage.py test apps.partners.tests --noinput
npx eslint src/components/partners src/screens/partners src/Module/ChatRoom --quiet
```

If frontend path lint is too broad or blocked, run focused files touched only and record the blocker.

## Best Prompt For Phase 09

```text
Please proceed with Phase 09 of the KIS Messaging Platform Roadmap without using git commands. Focus on WhatsApp-grade privacy features: disappearing messages, view-once media, chat lock, blocked contacts, privacy defaults, screenshot/save restrictions where possible, and clear UI. Use docs/messaging-platform-roadmap/phase-09-privacy-disappearing-view-once-chat-lock.md as the source of truth. Run safe validation or record blockers and update docs/messaging-platform-roadmap/status.md and docs/BUILD_STATE.md.
```

