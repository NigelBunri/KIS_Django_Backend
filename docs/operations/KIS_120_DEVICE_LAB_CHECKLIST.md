# KIS 120 Percent Device Lab Checklist

Status: Phase 27 foundation.

Use this checklist on real devices and representative emulators before staging sign-off.

## Required Devices

- iPhone small screen.
- iPhone large screen.
- iPad or tablet-sized viewport if supported.
- Android small screen.
- Android mid-range device.
- Android older/low-memory device.
- Poor network profile: 3G/low-bandwidth.
- Offline/reconnect profile.

## Accessibility And Age Modes

For each device:

- Child mode loads without exposing unsafe recommendations.
- Youth mode uses safe defaults.
- Adult mode keeps standard navigation.
- Older-adult mode has readable text and reachable tap targets.
- Main bottom-tab selected states are rounded and readable.
- Dark theme has no unreadable gold-on-gold or light-on-light buttons.
- Light theme has royal/gold styling without low-contrast text.
- Screen reader labels are present for primary actions where available.

## Messaging

For each device pair:

- User A sends direct message to User B.
- User B sends direct message to User A.
- Long text messages decrypt and render.
- Conversation list updates on both devices.
- Sender alignment remains correct after app restart.
- Retry is invisible when network returns.
- Subrooms cannot be duplicated for the same message/context.
- Opening a listed subroom lands in the correct messaging room.
- DM media upload passes through safety gate.
- Unsafe DM media shows blocked/review state.

## Broadcast And Channels

- Channel creation button is visible.
- Feed/content creation is scoped to selected channel.
- Channel home loads.
- Content detail loads for video, image, text, audio/document where available.
- Subscribe/bell UI works or shows clear placeholder.
- Comments, saves, playlists, history, and broadcast state render.
- Public growth readiness card loads in Profile.
- Private/unlisted content is not exposed in public metadata.

## Bible And Spiritual Growth

- Bible reader loads.
- Highlighted/commented verse navigation opens the exact verse.
- Dark-theme highlights remain readable.
- Reading plans, reminders, daily meditations, and missed schedule badges behave correctly.
- Offline/low-bandwidth scripture behavior is documented.

## Commerce, Education, Health, Partners

- Commerce product/service discovery loads.
- USD direct payment handoff works in staging or shows safe pending state.
- Education discovery/detail/enrollment flow loads.
- Health appointment/session flow loads.
- Partner workspace/channel/subroom flow loads.
- Verification/trust badges render consistently.
- No KISC/wallet-as-money copy appears in new checkout flows.

## Evidence Capture

For every checked device, capture:

- Device model and OS version.
- App build/version.
- Network mode.
- Screenshots for each major tab.
- Short screen recording for messaging, payments, channel creation, and media safety.
- Any crash logs or console errors.
- Pass/fail notes and blocker owner.
