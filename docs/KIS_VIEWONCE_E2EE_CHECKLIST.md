# KIS View-Once & E2EE — Implementation Checklist

Status legend: ✅ verified · 🚧 blocked on a product decision · ⏭ not applicable / already correct

## View-Once

- [x] ✅ Root cause 1 (cache-layer merge resurrection) identified and fixed — `mergeCachedMessage`, `KIS/src/Module/ChatRoom/Storage/chatStorage.ts`.
- [x] ✅ Root cause 2 (same-tick strip undone by replaceMessages' own merge) identified and fixed — `mergePreservingRich`, `KIS/src/Module/ChatRoom/hooks/useChatPersistence.ts`.
- [x] ✅ Root cause 3 (persistent decrypted-plaintext cache survives a restart) identified and fixed — `hydrateDecryptedMessages` + new `deleteDecryptedMessage`, `KIS/src/Module/ChatRoom/Storage/decryptedMessageStorage.ts`.
- [x] ✅ Cleanup wired into both the opener's own device (`ChatRoomPage.tsx`'s `handleViewOnce`) and every other device/participant (`useChatMessaging.ts`'s `onViewOnceOpened` socket listener).
- [x] ✅ Server-side (`markViewOnceOpened`, Nest) independently verified already correct — idempotent, never flips `isDeleted`, authorizes by conversation membership.
- [x] ✅ Attachment access control (`attachment-access.service.ts`) independently verified already correct — denies re-access post-view, forces view-once media through an auth-gated proxy rather than a revocable-in-name-only presigned URL.
- [x] ✅ Regression tests: 12 mobile (chatStorage/useChatPersistence/decryptedMessageStorage), 4 Nest (`messages.service.viewOnce.spec.ts`). All passing.
- [x] ✅ Verified normal (non-view-once) merge/rehydration behavior unchanged — every new test file includes a case proving the legitimate "recover content lost across a cache merge" behavior still works.

## E2EE pipeline

- [x] ✅ Confirmed bug fixed: concurrent same-session decrypts raced on Signal ratchet state (`runSerializedForSession`, `KIS/src/security/e2ee.ts`), mirroring the pre-existing encrypt-side fix for the identical problem.
- [x] ✅ Session/address key derivation verified correct against actual libsignal source.
- [x] ✅ `decryptPreKeyWhisperMessage` encoding argument verified inert/correct.
- [x] ✅ `rotateDeviceId` and per-device fault isolation in `encryptPayloadForRecipients` verified correct.
- [x] ✅ Both `JSON.parse` sites in the decrypt path verified to already have safe fallbacks.
- [x] ✅ Cross-boundary check (single-device vs. all-devices bundle endpoint usage) resolved — mobile client already calls the correct (all-devices) endpoint first.
- [x] ✅ Django key-distribution service audited across 6 X3DH correctness properties — all already correct; 3 new regression tests added to lock in the guarantees.
- [x] ✅ 4 new regression tests for the decrypt-serialization fix, covering ordering, cross-session concurrency, and failure isolation.
- [ ] 🚧 **Product/security decision needed**: attachments are currently access-controlled, not byte-level E2EE-encrypted. Implementing byte-level attachment encryption is a major architecture change (large-file key distribution, re-encryption on download, mobile binary crypto) — explicitly not built without sign-off. See audit doc §2 for detail.

## Testing

- [x] ✅ Mobile: `tsc --noEmit` 0 errors. `jest security + ChatRoom`: 88/88 real tests pass; 1 pre-existing, unrelated suite failure (confirmed present even with all session changes reverted).
- [x] ✅ Nest: `messages.service.viewOnce.spec.ts` 4/4 pass; 3 pre-existing, unrelated Mongo-dependent suite failures confirmed present regardless of this session's changes.
- [x] ✅ Django: `apps.accounts` — 404 tests, 3 pre-existing unrelated failures (welcome-email audit logging, privacy-field-visibility permissions — confirmed unrelated to E2EE). `manage.py check`: clean. `makemigrations --check`: no changes (no migration needed or created).

## Deployment

- [ ] Not yet deployed to production — mobile changes need a new app build to reach any device (same constraint as every other mobile fix this session); Nest/Django changes are test-only additions with no behavior change, safe to deploy whenever convenient, no migration involved.
