# KIS View-Once & E2EE Pipeline Audit

Date: 2026-10-15. Prompted by two reports: (1) view-once content reappearing after being opened; (2) some received images/files/messages appearing encrypted/unreadable.

## Scope audited

- **Mobile (KIS, React Native)**: view-once UI/state (`ChatRoomPage.tsx`, `MessageBubble.tsx`), message cache/merge logic (`Storage/chatStorage.ts`, `hooks/useChatPersistence.ts`), decrypted-message cache (`Storage/decryptedMessageStorage.ts`), the full client-side Signal Protocol implementation (`security/e2ee.ts`), and the socket/decrypt orchestration (`hooks/useChatMessaging.ts`).
- **NestJS (backend/Nestjs)**: view-once server-side consumption (`messages.service.ts`'s `markViewOnceOpened`), attachment access control (`uploads/attachment-access.service.ts`), message schema/storage (`schemas/message.schema.ts`).
- **Django (backend/kis)**: Signal-Protocol key distribution (`apps/accounts`'s `E2EDeviceKey`/`E2EPreKey` models, `E2EERegisterKeysView`/`E2EEFetchBundleView`/`E2EEFetchDeviceBundlesView`).

## 1. View-Once reappearance bug — three independent root causes, all fixed

All three live in the same bug class: content-merge/cache logic built to recover data genuinely *lost* across a merge (e.g. a downloaded file's local path present on one cached copy but not another) had no concept of a view-once message's emptied fields being an intentional, permanent, terminal state — so it "helpfully" restored them from any older copy that still had the pre-view content.

| # | Location | Trigger | Fix |
|---|---|---|---|
| 1 | `mergeCachedMessage`, `Storage/chatStorage.ts` | Every `loadMessages()` call — concatenates up to 5 storage layers (durable history, AsyncStorage V3/V2/V1, alias-recovered keys). Any one layer holding the pre-strip snapshot resurrected it on every room reopen/restart. | `isOpenedViewOnce` guard (viewOnce && viewedAt) short-circuits before any restoration logic, in either argument order. |
| 2 | `mergePreservingRich`, `hooks/useChatPersistence.ts` | **Most severe**: every `replaceMessages()` call — including the exact call `handleViewOnce` makes to apply its own strip. Undid the strip synchronously, on the call meant to apply it. | Same guard pattern. |
| 3 | `hydrateDecryptedMessages`, `Storage/decryptedMessageStorage.ts` | Explains surviving an app restart specifically: a persistent, Keychain-backed cache (`saveDecryptedMessage`) holds the first-ever-decrypted full plaintext; `handleViewOnce` never cleared `ciphertext`/`encryptionMeta` client-side, so this function still saw "encrypted, needs recovery" post-view. | Same guard, plus a new `deleteDecryptedMessage` export actually removing the stale cache entry — called from both `handleViewOnce` and the `onViewOnceOpened` socket listener (so a second device of the viewer, or another participant, gets the same cleanup). |

**Server-side (Nest) verified already correct, no change needed**: `markViewOnceOpened` strips every content field, sets `viewedAt`, never flips `isDeleted` (which would wrongly render "Message deleted"), is idempotent, and authorizes by conversation membership (not sender-only — the fix for a documented prior bug). `attachment-access.service.ts`'s view-once gating correctly denies re-access post-view and even forces view-once attachments through an auth-gated stream proxy instead of a revocable-in-name-only presigned S3 URL.

## 2. E2EE pipeline — one confirmed bug, several confirmed-correct findings, one confirmed design characteristic

### Confirmed bug, fixed: concurrent decrypts raced on Signal session state

`decryptFromUser` (`security/e2ee.ts`) had zero serialization protection, unlike the encrypt path (which already has a documented `encryptSerializationTail` fix for the identical problem). `decryptChatMessage` runs via fire-and-forget loops over whole message batches (history load, gap-fill resync) — two messages from the same sender/device racing on that session's `loadSession → advance-ratchet → storeSession` sequence left the session permanently desynced. This matches the reported pattern exactly: intermittent, sender-dependent, never total breakage.

**Fix**: `runSerializedForSession`, a per-session-id promise chain (keyed `userId.deviceId`, not one global queue — unrelated senders gain nothing from blocking each other), now wraps `decryptFromUser`'s crypto work.

### Confirmed correct, no change

- Session/address key derivation matches libsignal's own internal format exactly (checked against actual library source).
- `decryptPreKeyWhisperMessage`'s `'binary'` encoding argument is inert here (buffer is already an ArrayBuffer) — not a bug despite looking suspicious.
- `rotateDeviceId`, per-device fault isolation in `encryptPayloadForRecipients` (one broken device never blocks sends to a user's other devices).
- Both `JSON.parse` call sites in the decrypt path already have a safe plaintext fallback, not a silent-garbage case.
- Django's key-distribution service, across all 6 X3DH correctness properties checked (see §3 below).
- A cross-boundary question raised by the Django audit — does the mobile client ever call the single-device bundle endpoint (which returns only the target's most-recently-seen device) when it needs full multi-device fan-out? — was resolved as **not an issue**: `fetchDeviceBundles` in `e2ee.ts` calls the all-devices endpoint first, falling back to the single-device one only when that returns zero devices.

### Confirmed design characteristic, not a bug, not changed

**Attachments (images/files/voice) are access-controlled, not byte-level encrypted.** Only the E2EE envelope carrying attachment metadata/references is encrypted; the actual file bytes are protected by signed URLs / an auth-gated stream proxy, not ciphertext. This was independently re-verified (not just trusted from an earlier, separate investigation) via fresh review of `uploadFileToBackend.ts` and Nest's `uploads/*.ts` — zero encryption-related code found in either.

This is a real gap relative to a literal reading of "ensure all attachments are end-to-end encrypted," but implementing new byte-level attachment encryption is a **major architecture change** — key distribution for potentially large files, re-encryption semantics on download, mobile-side crypto for binary payloads — not a bug fix within the existing design. This was **not** unilaterally built. It is recorded here as a decision point requiring explicit product/security sign-off before any implementation work begins.

## 3. Django key-distribution service — audited, no bug found, regression tests added

All six properties an X3DH-style key-distribution system requires were checked against the actual implementation and confirmed correct:

1. **Atomic, exactly-once prekey consumption** — `select_for_update()` + `transaction.atomic()`, genuinely race-safe under Postgres row locking.
2. **Exhaustion handling** — returns a valid signed-prekey-only bundle (correct X3DH fallback), not an error or silently malformed response.
3. **Replenishment** — full delete+recreate on re-registration is intentional design (already covered by an existing idempotency test), not a missing top-up path.
4. **Device revocation** — `revoke_device_session` actively deletes the device's key rows, stronger than the read-time filtering both fetch views also apply independently.
5. **Signature verification** — correctly out of server scope; verified client-side by the recipient's own session builder, standard X3DH.
6. **Multi-device correctness** — enforced by the database's own `(user, device, prekey_id)` unique constraint.

Three new regression tests lock these guarantees in (one-time-prekey-consumed-exactly-once, revoked-device-excluded-from-both-endpoints, revocation-actually-deletes-keys).

## Residual risks / known limitations

- **Attachment byte-encryption decision is still open** — see §2. No deadline attached; flagging for the user to decide.
- **True concurrent-request races were not reproduced under real multi-process load** — the decrypt-serialization fix is verified by tracing the actual Signal/libsignal session lifecycle and by direct unit tests of the serialization primitive itself (proving strict per-session ordering, cross-session concurrency, and failure-doesn't-wedge-the-queue), and the Django prekey-atomicity guarantee is verified by reading the `select_for_update()` implementation against Postgres's documented row-lock semantics — neither was empirically reproduced by actually racing two live concurrent requests against the real race window, since this environment has no multi-process test harness for that.
- **No real two-device Signal Protocol handshake was run end-to-end** from this environment (no physical devices/simulators with live network timing available here).
