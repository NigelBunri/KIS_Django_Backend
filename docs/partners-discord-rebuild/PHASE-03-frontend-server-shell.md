# Phase 3 - Frontend Server Shell

Goal:
- Build the actual Discord-like partner UI shell.

Status:
- In progress
- Frontend codebase confirmed: `admin_ui`
- Initial server shell route is implemented
- Next action: unread and mention badges, richer member data, and invite/settings flows with real actions

Definition of done:
- A user can open a partner server and navigate it through a Discord-style shell.
- Server sidebar, category grouping, channel navigation, unread markers, and settings entry points exist.

Tasks:
- [x] 3.1 Decide and confirm the frontend codebase that owns the partner UI.
- [x] 3.2 Build partner server list rail.
- [x] 3.3 Build server header and primary actions.
- [x] 3.4 Build category and channel tree UI.
- [x] 3.5 Add role-gated visibility in the sidebar.
- [ ] 3.6 Add unread and mention badges.
- [x] 3.7 Add partner settings navigation.
- [x] 3.8 Add member list rail with role grouping.
- [x] 3.9 Add onboarding entry points and invite entry points.

Required UX targets:
- partner feels like a server, not a loose collection of endpoints
- navigation state survives refresh
- mobile and desktop both work

Handoff notes:
- Do not start this phase before backend visibility rules are stable.
- Keep the UI faithful to backend permission resolution; do not duplicate business rules loosely in frontend code.

Implemented so far:
- Frontend codebase confirmed as `admin_ui`
- Added partner platform API client functions in `admin_ui/lib/api.ts`
  - `fetchPartnerServers`
  - `fetchPartnerServerShell`
- Added React Query hooks in `admin_ui/hooks/usePartnerServers.ts`
- Added partner index route in `admin_ui/app/partners/page.tsx`
- Added partner server shell route in `admin_ui/app/partners/[partnerId]/page.tsx`
- Added shell component in `admin_ui/components/partners/PartnerServerShell.tsx`
  - left server rail
  - partner sidebar with category grouping and app launcher
  - permission-aware channel tree using backend-filtered `server-layout`
  - server header with settings/invite/onboarding entry buttons
  - member rail grouped from admins and role assignments
  - query-string channel selection so navigation state survives refresh
- Updated global admin navigation in `admin_ui/components/ui/Sidebar.tsx`

Verification:
- `pnpm tsc --noEmit` in `admin_ui` still fails because of many pre-existing TypeScript issues outside the partner shell files
- Filtered check for new files returned no matching errors:
  - `app/partners/page.tsx`
  - `components/partners/PartnerServerShell.tsx`
  - `lib/api.ts`
  - `hooks/usePartnerServers.ts`

Known gaps:
- Unread and mention badges are not wired yet because the backend does not currently expose stable unread/mention state for this shell
- Member rail currently falls back to owner/admin summaries plus role-assignment-derived identities; it still needs a dedicated partner membership/member-list endpoint for a full Discord-style right rail
- Header buttons are entry points only; they are not yet connected to real invite/settings modals
