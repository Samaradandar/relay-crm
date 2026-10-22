# Relay CRM — web (frontend)

React 18 + TypeScript, Vite, React Router, TanStack Query and Tailwind, as fixed in the build plan.
Colours, type and spacing come from the **Relay CRM Design System** (`src/styles/tokens.css`).

## Run it

```bash
cd web
npm install
cp .env.example .env      # VITE_API_MODE=mock works with no backend
npm run dev               # http://localhost:5173
```

| Command | What it does |
| --- | --- |
| `npm run dev` | Dev server; `/api` is proxied to FastAPI on :8000 |
| `npm run build` | Production build into `dist/` |
| `npm run build:demo` | One self-contained `dist-demo/index.html` (mock data) to share a clickable preview |
| `npm run typecheck` | TypeScript, strict |
| `npm test` | Vitest |

**Sample accounts (mock mode):** Priya Sharma (sales rep), Neha Kapoor (Team Leader), Arjun Rao (Admin), Vikram Singh (Super admin).
Click one on the sign-in page, or type their email with password `relay123`.

## What happens when someone opens Relay

1. **Opening screen** (`features/splash`): the logo assembles, "Designed for Adandar Technology" rises letter by letter, a seam opens down the centre and the screen splits into two doors that swing apart. It plays once per browser session; click, Enter or Esc skips it; reduced-motion devices get a short fade.
2. **Sign in** (`features/auth/LoginPage.tsx`): Google (Cognito hosted UI, live mode) or email and password. Nobody picks a role here.
3. **Role from the account**: `/me` returns the person's role and team. `auth/navigation.ts → homeFor()` sends Admins and Super admins to Approvals and sales reps and Team Leaders to Home. The sidebar shows only that role's pages.

## Built phase by phase

Every page is listed once in `src/auth/navigation.ts` with the **build phase** that delivers it.
`VITE_RELEASE_PHASE` (default `1`) switches pages on, so each phase's frontend ships together with its backend.

| Phase | Frontend folder(s) | Pages |
| --- | --- | --- |
| 1 · The base (built) | `features/auth`, `splash`, `home`, `approvals`, `notifications`, `profile`, `admin` | Opening, Sign in, Home, Approvals, Notifications, Notification settings, Profile, Users, Teams, Failed jobs |
| 2 · Lead Pool | `features/leads`, `pool`, `accounts` | New lead, Lead Pool, Accounts, Account page, Deal page (basic), Assignment settings |
| 3 · New business | `features/pipeline`, `deals`, `today` … | Today, Board, stage pages, Won, Lost, Team monitoring, exports, deleted, pipeline/catalog/fields settings |
| 4 · Won and billing | `features/customers`, `billing`, `proposals` | Subscriptions to confirm, billing tab, proposal template, GST, usage feed health |
| 5 · Existing customers | `features/renewals`, `churn` | Existing customer stages, Churn History, churn-risk |
| 6 · Performance | `features/performance`, `reports` | Leaderboard, My dashboard, Reports and forecast |

To add a phase: build its pages under `src/features/<area>/`, add their API calls to `src/api/index.ts`
(mock and live), add the routes in `App.tsx` under that phase's comment, and raise `VITE_RELEASE_PHASE`.

## Folder map

```
src/
  api/           types.ts (backend shapes) · client.ts (fetch + token) · index.ts (mock and live) · mock/db.ts
  auth/          AuthProvider · cognito.ts (Google PKCE) · navigation.ts (pages × roles × phase) · permissions.ts · guards.tsx
  components/    ui/ (Button, Field, Badge, Tabs, Banner, ReasonBox, Toast, DataTable, Icon) · layout/ (AppShell, PageHeader)
  features/      one folder per CRM area, mirroring api/app/modules
  config/        brand.ts (logo, tagline) · env.ts
  lib/format.ts  IST dates, ₹ in Indian style, initials
  styles/        tokens.css (design system) · index.css (Tailwind)
  assets/brand/  logo files cut from the supplied logo
```

## Rules the screens follow

- **The backend decides permissions.** `auth/permissions.ts` only decides what to show; the API enforces every rule.
- **Hide, don't disable.** Routes a role can never open are hidden. A role that may only request an action sees "Request approval".
- **Reasons.** Important actions use `<ReasonBox>`: required for User, Team Leader and Admin, optional for Super admin. The reason travels to the API in the `X-Reason` header.
- **Times in IST, money in paise**, shown with `lib/format.ts`.

## Phase 2 · Lead Pool (frontend)

`VITE_RELEASE_PHASE=2` switches on:

| Role | Home | New in this phase |
| --- | --- | --- |
| Sales rep | `RepHome`: New for you, My open leads (by due date), Leads I added (where each one went) | Add new lead (full page, live duplicate check) |
| Team Leader | `TeamLeaderHome`: Waiting for you to assign, team load vs caps, transfer requests, recent assignments | Lead Pool (team share), Assign dialog (reason required) |
| Admin / Super admin | Approvals, with the Lead Pool summary strip on top | Lead Pool (all teams) |

- Sales reps never see the Pool (`pool.view`); they see only the status of leads they added.
- The floating **+** button opens Add new lead on every page except Home, Lead Pool and the form itself.
- Mock mode runs a copy of the assignment engine (`src/api/mock/leads.ts`): rules first, default team, round-robin among active reps under cap, Team Leaders excluded. Tests: `src/api/mock/__tests__/assignment.test.ts`.
- Still to build in Phase 2: Accounts (hidden from the sidebar via `pending`), deal read view, Settings > Assignment, bulk reassign, transfer request form.
- The app opens in light mode; dark mode only when picked from the profile menu.

## Sales rep · Batch 1 (Phase 3 screens + existing-customer stages)

`VITE_RELEASE_PHASE=5` in the demo. New for the sales rep:

- **Today** (`features/home/TodayPage.tsx`) is the rep home from Phase 3: revenue vs target with projection, gold rank tile, stale banner, Your move / Waiting on them lanes (Done asks for the next step, +2d snooze, Nudge, They replied), new leads, meetings today, leads I added.
- **Pipeline board** (`features/deals/BoardPage.tsx`): New business / Existing customers switch, summary strip, search and filters, drag forward (blocked with the missing criteria listed), drag back asks for a reason.
- **Stage pages** (`features/deals/StagePage.tsx`): one per stage, driven by `lib/pipeline.ts` columns; sort, pagination 10/25/50, export CSV with a logged reason.
- **Deal page** (`features/deals/DealPage.tsx`): stage bar, Move / Another round / Move back / Close as Lost, next step, exit criteria with proof capture (activity, fields or file), key details, contacts, documents, timeline.
- **Log activity** drawer (`features/deals/LogActivityDrawer.tsx`): Feature 7 structured note, opens from anywhere via `useLogActivity().open({ dealId })`.
- Pipeline definitions and exit criteria live in `src/lib/pipeline.ts`; the mock engine is `src/api/mock/deals.ts` (tests in `__tests__/pipeline.test.ts`).
- Sidebar: long list with New business and Existing customers stage groups and live counts. Won, Lost, Churn History, Accounts, Customers, My dashboard and Reports are listed but `pending` until their batch ships.

## Team Leader · Batch 1

- **Home** (`features/team/TeamHome.tsx`, Phase 3+): "Needs you" inbox with tabs Assign (Pool), Verify ticks, Approve (team-level requests from own reps), Proof missing, Transfers; team strip (revenue vs summed targets, open deals, weighted forecast, overdue, stale); one card per rep.
- **My team** `/app/team` and **rep page** `/app/team/:id` (`features/team/TeamPages.tsx`): profile, their Today lanes with "Assign task", pipeline by stage, recent activity, lost reasons.
- **Deal page** for team deals: Verify / Reject ticks, Assign task, Reassign (within team, reason), Override and move (reason; never on own deals; missing criteria become "Overridden by"), Reopen Lost.
- **My Today** `/app/my-today`: the TL's own deals, same as the rep Today (no rank strip — TLs are not on the leaderboard).
- **Owner filter** (Whole team / Mine / a rep) on the board and stage pages, kept in `?owner=`.
- Mock rules: `src/api/mock/team.ts`; tests: `__tests__/team.test.ts`.
- Batch 2 (next): targets and incentive %, transfer request form, bulk reassign, team reports.

## Admin and Super admin

- **Home** stays the Approvals inbox (with the Lead Pool strip). **Company overview** `/app/overview`: company revenue vs target, every team side by side, every rep across teams.
- **Settings › Assignment** `/app/settings/assignment`: rules in priority order (↑↓, on/off, edit), default team, lead caps, rep groups, Test a rule (dry run, not logged).
- **Settings › Pipeline** `/app/settings/pipeline`: edit stage names, probabilities and exit criteria in a draft; publish validates and creates a version; roll back republishes an older one.
- **Settings › Product catalog** `/app/settings/catalog`: plans, list price, floor price; deal values and proposals read from here.
- **Audit log** `/app/admin/audit`, **Deleted records** `/app/deleted` (also Team Leaders, team scope), **Roles & permissions** `/app/admin/permissions` (Super admin, read-only matrix).
- **Users › Manage**: change role/team, deactivate/reactivate. Admins manage Users and Team Leaders; only a Super admin touches Admins. The last Super admin cannot be removed.
- **Deal page**: Delete (hidden, restorable) for Team Leaders and Admins; Admins reassign across teams.
- **Won** and **Lost** pages for every role (scoped).
- Every Admin change asks for a reason (`components/ui/ReasonPrompt.tsx`); optional for a Super admin. Mock rules: `src/api/mock/admin.ts`; tests: `__tests__/admin.test.ts`.

## Page map by role (VITE_RELEASE_PHASE=5)

| Page | Sales rep | Team Leader | Admin | Super admin |
| --- | --- | --- | --- | --- |
| Home | Today | Team home (Needs you) | Approvals | Approvals |
| My Today / My team / rep pages | — | ✓ | rep pages | rep pages |
| Company overview | — | — | ✓ | ✓ |
| Pipeline board, stage pages, deal page | own | team | all | all |
| Won, Lost | own | team | all | all |
| Add new lead | ✓ | ✓ | ✓ (reason) | ✓ |
| Lead Pool | — | team share | all | all |
| Approvals | my requests | team requests | all | all |
| Settings: Assignment, Pipeline, Catalog | — | — | ✓ (reason) | ✓ |
| Users, Teams, Audit log, Failed jobs | — | — | ✓ | ✓ |
| Deleted records | — | team | all | all |
| Roles & permissions | — | — | — | ✓ |

Not built yet (listed in `auth/navigation.ts` as `pending`): Accounts and account page, Customers (subscriptions, minutes, payments, renewals), Churn History, proposal builder, My dashboard and incentives, targets, Leaderboard, Reports, transfer request form, bulk reassign.
