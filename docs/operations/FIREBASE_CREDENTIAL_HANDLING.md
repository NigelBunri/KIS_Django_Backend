# Firebase Credential Handling

This document separates Firebase admin credentials from mobile Firebase config.
It does not contain real credential values.

## Firebase Admin Credentials

Firebase admin service account JSON is a server-side secret. It can mint or send
privileged Firebase requests depending on IAM permissions.

Production rules:

- Do not store production service account JSON in source control.
- Store it in the hosting provider secret manager or a protected file mount.
- Prefer `FIREBASE_CREDENTIALS_FILE=/secure/path/firebase-service-account.json`.
- If using `FIREBASE_CREDENTIALS_JSON`, store it only in protected provider env.
- Limit IAM permissions to the minimum required for push notification delivery.
- Rotate any key that appeared in local files, tickets, chat, screenshots, or logs.
- Revoke old keys after deployment confirms the new key works.

Current local finding:

- `backend/Nestjs/CC_Node_Backend/config/firebase-adminsdk.json`

Safe action:

1. Do not print the file content.
2. Confirm whether it is real or local-only.
3. If real, create a new key in Firebase/Google Cloud.
4. Move new key to secret manager or protected mount.
5. Update `FCM_SERVICE_ACCOUNT_PATH` or `FIREBASE_CREDENTIALS_FILE`.
6. Restart notification services.
7. Revoke the old key.

## React Native Firebase Mobile Config

Files such as `android/app/google-services.json` are not equivalent to admin
service account JSON, but their API keys still need restrictions.

Production rules:

- Restrict API keys by Android package name and SHA certificate where possible.
- Restrict enabled APIs to the minimum required.
- Use separate Firebase projects or apps for development, staging, and production.
- Do not place server/admin credentials in mobile config.
- Review Firebase console for unauthorized usage spikes.

Current local finding:

- `android/app/google-services.json`

Safe action:

1. Verify the key belongs to the intended Firebase project.
2. Verify app/package restrictions.
3. Verify SHA restrictions for release builds.
4. Verify API restrictions.
5. Rotate if it was exposed outside intended mobile distribution.

## Verification Checklist

- `FIREBASE_CREDENTIALS_FILE` or provider secret mount is configured.
- No production service account JSON is stored in source.
- Admin service account has least-privilege IAM.
- Old service account keys are revoked.
- Android Firebase config key has package/SHA/API restrictions.
- Push notification smoke test passes in staging.
- Push notification smoke test passes in production after launch.
