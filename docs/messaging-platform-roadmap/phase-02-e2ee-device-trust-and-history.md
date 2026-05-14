# Phase 02 - E2EE Device Trust And History

Purpose: complete the existing encryption implementation so messages decrypt reliably across history and devices, while making production fallback behavior explicit and safe.

## Files To Inspect First

Frontend:

- `/Users/nigel/dev/KIS/src/security/customE2EE.ts`
- `/Users/nigel/dev/KIS/src/security/e2ee.ts`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/hooks/useChatMessaging.ts`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/hooks/useChatPersistence.ts`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/ChatRoomPage.tsx`
- `/Users/nigel/dev/KIS/src/Module/ChatRoom/componets/`

Nest:

- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend/src/chat/features/e2ee/`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend/src/chat/features/messages/schemas/message.schema.ts`
- `/Users/nigel/All other files/CC/KIS/main_kis_bakend/backend/Nestjs/CC_Node_Backend/src/chat/features/messages/messages.service.ts`

Django:

- `apps/chat/urls.py`
- `apps/chat/views.py`
- any E2EE endpoints under `apps/chat/`

## Required Work

### 1. Define encryption modes

Add a shared frontend type:

```ts
type EncryptionMode = 'signal' | 'conversation_key' | 'plaintext_dev_fallback';
```

Each message should expose safe metadata:

- `encryptionMode`
- `encryptionVersion`
- `senderDeviceId`
- `recipientDeviceIds`
- `decryptState`: `decrypted`, `pending_key`, `failed`, `unsupported`

Do not expose raw keys.

### 2. Production fallback rule

In `useChatMessaging.ts`:

- Keep plaintext fallback only for explicit local/dev flag.
- In production, if encryption fails, keep message pending/failed with a clear local error.
- Do not silently send plaintext in production.

Add an env/config flag in React Native if not already present:

- `KIS_CHAT_ALLOW_PLAINTEXT_DEV_FALLBACK=false` by default.

### 3. Device trust UI foundation

Create or extend a small UI component under:

- `/Users/nigel/dev/KIS/src/Module/ChatRoom/componets/SecurityInfoSheet.tsx`

It should show:

- conversation encryption status;
- participant device count;
- whether any device keys are missing;
- last key refresh time;
- a placeholder for safety-number verification.

Do not block chat while this UI is incomplete.

### 4. History decryption repair

In history load code:

- If message has ciphertext but missing key, show a temporary `Securing message...` state, not permanent `encrypted message`.
- Trigger one silent key refresh.
- Store decrypted plaintext only according to current app security policy. If local decrypted cache exists, mark it as device-local.

### 5. Backend E2EE endpoints

Ensure Nest/Django endpoints exist for:

- list current user devices;
- list conversation member devices;
- fetch public key bundle;
- rotate/revoke device;
- get conversation key only if member.

If endpoints are not all present, create skeletons returning safe structured errors and document missing provider/crypto work.

## Tests / Validation

Run:

```bash
python3 manage.py check
pnpm tsc --noEmit
npx eslint src/security/customE2EE.ts src/security/e2ee.ts src/Module/ChatRoom/hooks/useChatMessaging.ts src/Module/ChatRoom/hooks/useChatPersistence.ts src/Module/ChatRoom/ChatRoomPage.tsx --quiet
```

Manual QA:

- Send short and long encrypted messages both directions.
- Restart apps and confirm old encrypted messages decrypt.
- Simulate missing recipient key and confirm safe pending/error behavior.
- Confirm production mode does not silently plaintext fallback.

## Best Prompt For Phase 03

```text
Please proceed with Phase 03 of the KIS Messaging Platform Roadmap without using git commands. Focus on chat list, presence, unread counts, filters, status rings, and conversation metadata. Use docs/messaging-platform-roadmap/phase-03-chat-list-presence-and-unread.md as the source of truth. Preserve current UI layout unless a fix is required, run safe validation or record blockers, and update docs/messaging-platform-roadmap/status.md and docs/BUILD_STATE.md.
```

