# KIS Navigation And Information Architecture Guide

Last updated: 2026-05-14

## Purpose

KIS is a large super-app. Users should not need to understand the internal architecture to use it.

This guide defines the shared navigation and information architecture rules introduced in Phase 04 of the KIS 120 Percent roadmap.

## Main Mental Model

Use these plain meanings across the app:

- Messages: private communication, calls, statuses, contacts, communities.
- Broadcast: public discovery, channels, feeds, market, education, and health broadcasts.
- Bible: Scripture, devotions, prayer, reading plans, and Christian growth.
- Partners: organization workspaces, communities, groups, channels, jobs, apps, governance.
- Profile: identity, verification, principles, wallet/billing history, notifications, dashboards, settings.

## Every Major Screen Should Have

- Clear title.
- Short subtitle that explains the screen in human language.
- One obvious primary action.
- Optional secondary action.
- Search/filter only when it clearly applies to the current surface.
- Empty state.
- Loading state.
- Error/retry state.
- Age-safe wording that works for children, youth, adults, and older users.
- Minimum touch target of 44px, preferably 48px or higher.

## Shared Frontend Components

Phase 04 added:

- `/Users/nigel/dev/KIS/src/components/common/MainTabScaffold.tsx`

Exports:

- `MainTabPageHeader`
- `MainTabStateBlock`

Use these when building or refactoring main-tab surfaces.

## Header Rules

Main tab headers should:

- use `MainTabPageHeader` where possible;
- include an eyebrow only when it helps orientation;
- keep title short;
- keep subtitle under two lines;
- avoid marketing copy;
- place primary action in the header only when it is the natural next step;
- keep destructive actions out of the main header.

## Empty / Loading / Error Rules

Use `MainTabStateBlock` for:

- missing profile;
- no notifications;
- no conversations;
- no channels;
- no courses;
- no marketplace items;
- network errors;
- retry paths.

Messages must be calm and direct. Avoid blaming the user.

## Age-Aware UX Rules

Children:

- simple words;
- clear icons;
- no hidden critical actions;
- strong safety/reporting language.

Youth:

- fast actions;
- expressive but safe surfaces;
- visible privacy and block/report controls.

Adults:

- productivity and trust;
- fewer taps for common workflows;
- clear payment/verification/status evidence.

Older users:

- larger tap targets;
- predictable navigation;
- readable contrast;
- no tiny floating-only controls for critical actions.

## Next Migration Targets

After Phase 04, migrate these surfaces incrementally:

- Messaging main header and search state.
- Broadcast/Channels section empty and error states.
- Partner workspace empty and permission states.
- Health institution dashboard empty/error states.
- Education institution/course empty/error states.
- Marketplace order/cart empty/error states.
- Notifications list empty/error states.

