# Phase 06 - Updates Status Current Completion

Purpose: complete the current WhatsApp-style Updates/Status tab.

## Files To Inspect First

Frontend:

- `/Users/nigel/dev/KIS/src/screens/tabs/MesssagingSubTabs/UpdatesTab.tsx`
- `/Users/nigel/dev/KIS/src/screens/tabs/MessagesScreen.tsx`
- `/Users/nigel/dev/KIS/src/network/routes/socialRoutes.ts`
- `/Users/nigel/dev/KIS/src/network/index.ts`

Django:

- Search status endpoints:
  - `rg -n "Status|statuses|status" apps -S`
- Likely files:
  - `apps/core/views.py`
  - `apps/core/serializers.py`
  - `apps/social*` if present

## Required Work

### 1. Status list and composer

Status must support:

- text status with style;
- image status;
- video status;
- audio status;
- upload progress;
- clear validation errors;
- expiry time, default 24 hours.

### 2. Audience privacy

Implement or verify:

- contacts;
- contacts except;
- only share with;
- reply permission: contacts or nobody.

The UI should show selected audience count before posting.

### 3. Viewer

Viewer must:

- auto-advance;
- pause/resume;
- mark viewed;
- show progress;
- allow reply only if `replyAllowed`;
- close smoothly;
- handle unavailable media fallback.

### 4. Status rings in chat list

Connect `MessagesScreen.tsx` `statusByUserId` to the status summary.

### 5. Muting/reporting

Add visible actions:

- mute status user;
- unmute;
- report status item.

If backend is missing, add safe endpoints or hide action until implemented.

## Validation

```bash
python3 manage.py check
npx eslint src/screens/tabs/MesssagingSubTabs/UpdatesTab.tsx src/screens/tabs/MessagesScreen.tsx --quiet
```

Manual QA:

- Post text/image/video/audio status.
- View from another account.
- Confirm seen/unseen ring changes.
- Confirm audience restrictions.
- Confirm status expires or is hidden after expiry in test data.

## Best Prompt For Phase 07

```text
Please proceed with Phase 07 of the KIS Messaging Platform Roadmap without using git commands. Focus on completing current voice/video calls and call history before adding advanced call features. Use docs/messaging-platform-roadmap/phase-07-calls-current-completion.md as the source of truth. Run safe validation or record blockers and update docs/messaging-platform-roadmap/status.md and docs/BUILD_STATE.md.
```

