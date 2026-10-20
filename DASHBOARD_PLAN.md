# ClickPe Loan Operations Dashboard — Product and Implementation Plan

## 1. Objective

Replace the current single scrolling page with a coherent management dashboard for the local loan eligibility system. The dashboard should make it easy to answer:

- What is running now?
- What happened in each previous upload/run?
- How many records were accepted, rejected, evaluated, matched, unresolved, or notified?
- Which workflow stage failed, and what can an operator do next?
- Which lender criteria need human review, and what official evidence supports them?

Use ClickPe's current public brand as the visual reference: white surfaces, deep navy text, bright blue primary actions, vivid green accents, large friendly headings, and rounded product cards. The public site emphasizes a simple journey and clear product discovery; the operations dashboard should carry that visual language into a denser, task-focused layout rather than reproduce consumer marketing sections. Reference: [clickpe.ai](https://clickpe.ai/).

This document is a plan only. No dashboard architecture, authentication, or API changes described below are implemented by this plan.

## 2. Current workspace facts

- The frontend is React + Vite + TypeScript in `frontend/src/App.tsx`; it currently renders upload, upload history, and criteria review in one long page.
- Existing API data includes upload batches (`GET /api/uploads`, currently latest 20), one-batch details (`GET /api/uploads/{batch_id}`), products and provenance (`GET /api/products`), Gemini proposals (`GET /api/products/review-queue`), match jobs (`GET /api/workflows/match-jobs?batch_id=...`), notification status (`GET /api/workflows/notifications/status?batch_id=...`), and service health (`GET /api/health`).
- Upload processing automatically triggers matching after the batch is committed. Matching is tracked by a durable `MatchJob`; notification delivery is controlled and status is visible per batch.
- Workflow A product discovery is scheduled/manual and can create a Gemini proposal for human review. It is not a per-upload stage. Do not animate it as part of each applicant run unless the backend explicitly launches and records it for that run.
- The app does not yet expose n8n execution history, a durable stage-event timeline, per-stage start/end timestamps, or paginated/searchable upload history. `GET /api/uploads` only returns the latest 20 batches.
- Existing database/job and notification counters provide real status, but they do not currently support a trustworthy percentage completion value for all stages. Avoid invented percentages and fake process completion.

## 3. Navigation and screen map

Use a persistent left navigation rail on desktop, a compact top bar on mobile, and a shared page header with breadcrumbs and service state. Each screen should have its own route so refresh, sharing, and browser back/forward work.

| Route | Screen | Main job |
| --- | --- | --- |
| `/` | Overview | Summarize system health, recent batches, queue counts, and attention items. |
| `/runs` | Runs | Search, filter, sort, and paginate upload/matching runs. |
| `/runs/:batchId` | Run detail | Show one batch's ingestion, matching, notification, and error evidence as a timeline and drill-down. |
| `/uploads/new` | New upload | Select a CSV, show file/schema checks, confirm import, and enter the resulting live run. |
| `/products` | Product catalog | Browse available products using ClickPe-style category filters and compact partner cards. |
| `/products/:productId` | Product detail | Show lender/product terms, criteria, source provenance, freshness, and matching coverage. |
| `/reviews` | Criteria review | Review pending Gemini proposals and approve/reject with clear preconditions and audit history. |
| `/operations` | Workflow health | Show API/database/n8n/Mailpit availability, latest workflow execution state, and actionable failures. |
| `/settings/team` | Workspace and access | Show current workspace, members, roles, and identity provider state. |
| `/login`, `/auth/callback` | Sign in / Firebase return | Start Firebase sign-in and complete the secure API session exchange. Session validation stays on the backend. |

Use `react-router` (or an equivalent lightweight router) rather than a local tab state so routes are deep-linkable. Keep route navigation client-side and preserve query parameters for run filters.

### Overview screen

- A restrained header: ClickPe logo, environment badge (`Local`), live service indicator, and profile/settings placeholder only if it has real behavior.
- Four compact KPI cards: total recent uploads, applicants imported, potential matches, and criteria awaiting review. All cards link to the filtered source screen.
- “Currently running” panel that appears only while a real job is queued/running, with batch name, current known stage, elapsed time, and a link to run detail.
- Recent runs table (last 5–10): file, start time, imported/rejected rows, match count, delivery result, final status, and “View run”.
- Attention list: failed run, stale lender source, pending criteria proposal, or pending notification. Each item links to the exact corrective action.
- No decorative chart without enough historical data. Add trend charts only after the API provides stable time-series aggregates.

### Runs list

- Table columns: file name, batch ID (copy action), created/started time, rows accepted/rejected, match job status, candidate pairs, matches, notification status, and overall state.
- Search by file name/batch ID; filters for state, date range, and notification state; pagination backed by the API.
- Clear empty state, loading skeleton, API error/retry state, and no-results state.
- Never show applicant email/name in list views by default. Treat the input CSV as sensitive and keep batch summaries aggregate-first.

### Run detail

Header: filename, batch ID, created timestamp, final/live state, and safe actions (retry matching only when supported; continue test notifications only when pending and under cap; download row-error report if implemented).

Top summary cards: rows read/imported/rejected, candidate pairs evaluated, potential matches created, needs-information outcomes, notification recipients sent/pending. Label all matches as indicative and never as lender approval.

Main timeline stages:

1. **Upload received** — file hash/idempotency outcome and upload time.
2. **CSV validation** — total, accepted, rejected, row errors; click to inspect errors.
3. **Matching** — queued/running/completed/failed, attempt number, progress counters when available, criteria fingerprint, verified products used, duration.
4. **Notifications** — disabled/not started/running/partially sent/completed/failed; page count, test-recipient-only label, sent and pending totals.
5. **Completed** — final timestamp and summary, or failed stage with error code/message and next action.

Show execution IDs and n8n links only when backend has securely mapped them to this batch. Do not infer an n8n execution from timestamps or display another batch's execution.

### New upload flow

1. Drag/drop or browse for CSV; show filename, size, and remove/replace action.
2. Preflight validation: required headers, file size/encoding, row count, and duplicate-file detection. Preview a small sanitized sample (mask email) only if the API supports a non-persisting preview endpoint.
3. Show the exact consequence: accepted rows enter the local DB and matching starts automatically; notification routing stays on the controlled test inbox.
4. Confirm **Upload and start matching**. Do not call it “Continue” if it launches the whole pipeline.
5. Navigate directly to `/runs/:batchId` and begin polling the batch state. The run view stays usable if the user navigates away.

Current `POST /api/uploads` both stores the upload and schedules matching. A separate preview/commit API is an enhancement, not a frontend-only simulation. If preview is deferred, the UI must say that validation happens during upload and clearly distinguish committed from rejected rows.

### Product catalog and human review

- Follow the public [ClickPe product page](https://clickpe.ai/product) pattern: an **All products** view, compact category chips, partner/product cards, clear product family, one standout fact, a state badge, and a “View details” action. The public category set includes personal loans, business loans, credit line, credit cards, savings accounts, demat accounts, and loan against property.
- This engine currently stores only three personal-loan products (SBI, HDFC, ICICI). Show only categories/products backed by this application's own data. Other ClickPe marketplace categories can be added only after product schemas, reliable sources, and matching rules exist; do not fill them with scraped marketing cards or placeholder data.
- Product cards show lender, product name, current rate range and rate label, verified/stale state, last checked, source links, and whether it is eligible for matching. Avoid “best” or “recommended” rankings unless a defined, explainable comparison method exists.
- Product details distinguish the lender's published claims from this engine's extracted criteria. Show source timestamps, rate type, eligibility conditions, missing applicant fields, and the final-decision disclaimer.
- Review queue gets a clear count and a dedicated screen; separate pending, approved, rejected, and superseded proposal history.
- Detail drawer/page for a proposal: proposed field values; exact source quote beside each value; official URL; fetch time/source hash; model and usage metadata; uncertainty list; validation blockers; current rate evidence; reviewer decision and timestamp.
- Approval/rejection controls should be available only when the reviewer has the required access and the backend says the decision is valid. Explain missing/invalid token, stale evidence, source hash mismatch, and missing current rate evidence inline. Never hide all context behind a disabled grey button.
- Approval is a consequential operation. Use a confirmation step that summarizes which rule version will become active. Rejection asks for an optional reason and remains auditable.
- Gemini remains restricted to source-text extraction support. Never send applicant PII to it or imply that its proposal approves a person for credit.

## 4. Real workflow animation and live state

Animation should communicate state, not manufacture progress. Use a short entrance transition for newly created runs, a moving highlight on the current active timeline stage, and count-up transitions only when real API counters change. Do not render a 0–100% progress bar unless the backend exposes a meaningful denominator and progress measure.

### State mapping

| UI state | Source of truth | Display |
| --- | --- | --- |
| Uploading | Browser upload request progress | Byte progress if measurable; otherwise spinner and “Uploading file”. |
| Validating/importing | Upload batch API/job state | Indeterminate progress with “Checking and saving rows”. |
| Matching queued/running | `MatchJob.status`, attempt, timestamps, counters | Active matching timeline step; update counts when server commits a chunk. |
| Matching completed | `MatchJob.status=completed` and result | Checkmark, final counts, elapsed time. |
| Matching failed | `MatchJob.status=failed` and error | Error state with retry guidance only if retry is safe/supported. |
| Notification running/partial/done | Match notification statuses and n8n/API state | Recipient/page progress from persisted counts; label controlled local test inbox. |
| Product discovery/review | Product source timestamps and proposal status | Separate operations event/card; never imply this runs for every upload. |

### Polling behavior

- While a run is active, poll the run summary about every 1–2 seconds with exponential backoff on API errors; slow to 5–10 seconds after activity settles.
- Stop polling on completed/failed terminal states; allow manual refresh and reopening a prior run.
- Use ETag/updated-at or a version counter to avoid transferring an unchanged large payload.
- Consider Server-Sent Events only after the durable event model is established. Polling is adequate for the local first version and easier to recover after disconnect/reload.
- Respect `prefers-reduced-motion`; all essential state must be conveyed by text/icon/status, never color or animation alone.

## 5. Backend/API work required before the live dashboard is credible

The existing endpoints are a useful starting point but are not enough for a full management dashboard. Implement these behind stable response schemas:

1. **Dashboard summary endpoint** — aggregate batch counts, active jobs, review queue count, product freshness, and notification summaries. Define exact time window and status semantics.
2. **Paginated run list endpoint** — cursor/page pagination, filters, sort order, and summary aggregates per batch; replace the fixed latest-20 limit.
3. **Run summary/detail endpoint** — batch data plus match-job history, counts by match status and notification status, row errors, product/criteria snapshot, timestamps, and safe error information.
4. **Durable workflow event table** — append-only events keyed by `batch_id` and `job_id`: event type, stage, status, occurred_at, attempt, safe metadata, and external execution ID when known. Add retention and privacy policy.
5. **Stage transitions and correlation** — n8n webhooks carry `batch_id` and an idempotent `run_id`; Workflow B and C callbacks record start/success/failure and n8n execution IDs. Retries create a new attempt under the same run/correlation model.
6. **Progress counters** — define committed/total work by stage. Persist counters as chunks commit so browser refresh does not lose state. Expose unknown/indeterminate rather than a misleading percentage.
7. **Notification aggregate consistency** — API returns recipients and match rows with clear meanings, page attempts, last error, and local test recipient routing. Never expose recipient PII unnecessarily.
8. **Review decision readiness** — review queue returns machine-readable blocker codes/messages and safe readiness metadata (e.g. source stale, rate evidence missing, reviewer token required). The decision endpoint remains authoritative and revalidates all preconditions.
9. **Retry command endpoints** — only expose explicit idempotent retry actions for a failed stage, validating state and preserving attempt history. Avoid a generic “rerun everything” action.
10. **Health endpoint expansion** — report component health/degraded state for API and DB, and n8n/Mailpit connectivity where feasible; do not return credentials/secrets.

The workflow event store must be the durable source for the run timeline. n8n execution search is useful for operations diagnostics, but the UI should not call n8n directly with editor credentials.

## 6. Frontend architecture

- **CSS migration:** move the hand-authored page CSS to Tailwind CSS v4 using the official Vite integration. Define ClickPe colors, typography, spacing, radius, shadows, focus, and motion settings as CSS theme tokens; do not scatter raw hex values through JSX. Keep a small global stylesheet for reset, fonts, and theme variables only. The current official Tailwind guide provides a Vite setup; shadcn/ui documents Tailwind v4 support. See [Tailwind + Vite installation](https://tailwindcss.com/docs/installation/using-vite) and [shadcn/ui Tailwind v4](https://ui.shadcn.com/docs/tailwind-v4).
- **Component library:** use shadcn/ui as the accessible, source-owned component foundation with Radix primitives. Add only components the screens need: Button, Card, Badge, Input, DropdownMenu, Dialog/AlertDialog, Tabs, Tooltip, Table, Skeleton, Sheet for mobile nav, and Toast/Sonner for transient confirmations. Preserve the ClickPe visual system through shared CSS variables and variants instead of accepting the default theme unchanged. The existing `lucide-react` package can remain for icons.
- **Data table:** use TanStack Table for sortable/filterable/paginated runs, with server pagination controlling large history. Keep table markup and shadcn styling owned in the app.
- **Routing and data:** add React Router (or equivalent) for deep links. Put API calls in typed service modules. TanStack Query is the preferred cache/mutation/polling layer for cancellation, request retries, stale data, and active-run polling.
- **Forms and validation:** use React Hook Form + Zod if upload/team forms become multi-field; use the backend as the final validator and avoid duplicate, drifting eligibility rules in client code.
- **Motion:** prefer small CSS transitions for hover/focus and route state. Use Motion for React only for timeline transitions, drawers, and a few intentional layout changes if CSS becomes difficult. Respect reduced-motion preferences. Do not animate business states without corresponding server state.
- Add route-based screen layout: `AppShell`, `Sidebar`, `Topbar`, `PageHeader`, reusable status badges, metric cards, data table, empty states, error panels, and timeline.
- Keep API calls in typed service modules, not in page rendering components. Define request/response types matching backend schemas.
- Build reusable interaction primitives with keyboard support, focus management, accessible dialogs, and toasts/inline errors.
- Add a small motion library only if native CSS transitions are insufficient. Use CSS for simple hover/entry changes; avoid animation-heavy chart/scene dependencies.
- Keep token/authorization secrets out of URLs, persisted browser storage, logs, and analytics. For local human review, keep the token in memory and clear it on logout/tab close.
- Centralize date/time formatting (display local timezone with an explicit timezone label), INR formatting, and status mapping.

### Tailwind and component migration sequence

1. Add Tailwind v4 and its Vite plugin; add theme tokens for the ClickPe brand and light/dark operational states (start with light mode unless a dark mode is explicitly requested).
2. Add shadcn/ui CLI/config and a small reviewed set of components; keep component source in the repo so behavior and styles can be audited.
3. Convert the shell, navigation, buttons, fields, cards, status badges, dialogs, tables, and timeline one layer at a time.
4. Remove old page-level CSS as each area is converted; retain only global theme/base rules and any justified custom animation.
5. Add route and accessibility checks to the eventual implementation acceptance plan. Do not mix this plan with a large one-shot generated component dump.

## 7. Firebase Authentication and workspace data isolation

Use **Firebase Authentication** for sign-in, as requested, with the existing Firebase web app in project `clickpe-3e7ac`. Do not build a separate OAuth login provider integration or ask reviewers to paste `CRITERIA_REVIEW_TOKEN`. Firebase Auth may use an enabled provider such as Google or email/password; Firebase's client SDK handles that provider flow. The dashboard user sees ordinary sign-in and review actions.

Firebase's web API key identifies the Firebase app/project; it is not authorization for the loan API. Firebase documents that Firebase web config is public by design, while authorization must be enforced by the backend and Firebase security controls. Keep the provided config in environment-specific frontend config, apply Firebase API key restrictions/authorized domains, and never put Gemini keys or service-account credentials in the frontend. Reference: [Firebase API key guidance](https://firebase.google.com/docs/projects/api-keys).

### Firebase environments and providers

- **Local app configuration:** use the supplied Firebase project/app configuration for the local React client, sourced from local `VITE_FIREBASE_*` variables. Add `localhost` and the local dashboard host to Firebase Authentication's authorized domains. Do not commit `.env`.
- **Authentication provider:** enable only the provider(s intended for this tool (for example, Google and/or email/password). The UI should offer only those configured sign-in choices. New account access must follow an explicit provisioning policy; successful Firebase sign-in alone must not automatically grant access to existing customer data.
- **Separate deployment environments:** before a shared/staging or production release, create separate Firebase projects (or at minimum explicitly separate app clients and allowlists) for development, staging, and production. Prevent local builds from authenticating against production by accident.
- **Optional Firebase Local Emulator Suite:** support it for automated/local auth development when internet-independent testing is useful. It is optional; normal local usage can authenticate against the configured Firebase project.
- Firebase Analytics is not required for authentication. Keep it out of the first dashboard release; if later enabled, do not send applicant data, email, CSV contents, match details, criteria evidence, or authentication tokens as analytics events.

### Session architecture — no manual review token

Prefer Firebase session cookies so React does not persist Firebase ID/refresh tokens in browser storage:

1. React initializes Firebase Auth from environment config and signs the user in through the configured Firebase provider.
2. The client obtains a short-lived Firebase ID token from the signed-in Firebase user and posts it once to a same-origin API session exchange endpoint over HTTPS, with CSRF protection.
3. FastAPI uses the Firebase Admin SDK to verify the ID token against the expected Firebase project, then resolves the stable Firebase `uid` to a local user and workspace membership. Reject disabled, unprovisioned, wrong-project, expired, or invalid identities.
4. FastAPI creates a Firebase session cookie using the Admin SDK and sets it `HttpOnly`, `Secure` in HTTPS environments, and `SameSite=Lax` (or stricter if deployment routing permits). Clear Firebase client-side auth persistence after the session is established. The exact session approach and CSRF design must be reviewed against the app's local/deployed origin layout.
5. React calls `/api/me` with credentials to retrieve the signed-in profile, active workspace, and permitted capabilities. Browser requests include the cookie automatically; no user copies a token into the UI.
6. Every protected FastAPI route verifies the session and then authorizes the requested action against workspace membership and role. Logout clears the server cookie and signs out the client SDK; session revocation/expiry is handled explicitly.

Firebase documents the ID-token-to-backend verification flow and Admin SDK verification, and separately documents server-side session cookies with `HttpOnly`/cookie controls and CSRF considerations. References: [Verify Firebase ID tokens](https://firebase.google.com/docs/auth/admin/verify-id-tokens) and [Manage Firebase session cookies](https://firebase.google.com/docs/auth/admin/manage-cookies).

If session-cookie integration proves incompatible with the local topology, the fallback is to send a Firebase ID token as a bearer credential on API calls, with Firebase SDK-managed refresh and in-memory use. Do not persist bearer tokens in `localStorage`, and never accept an unverified UID/workspace from request JSON.

### User/workspace authorization model

- Create `users`, `workspaces`, and `workspace_memberships` tables. Map Firebase identity by `firebase_uid` (unique and immutable); email is a mutable profile/contact field, not the primary user key.
- A user may belong to one or more workspaces. Every upload batch belongs to one workspace. Applicants, validation errors, matching jobs, matches, notification records, and audit events inherit that workspace scope through foreign keys.
- All workspace-owned API queries derive the authorized workspace from the authenticated session/membership. Never accept `workspace_id` from the browser as sufficient authorization; verify membership and role on every request.
- Keep the public lender product/source catalog shared and read-only to workspace users. Product criteria approval is a platform-level operation for a designated `criteria_reviewer`/`platform_admin` role because approved rules affect every workspace. The approval API authorizes the Firebase-authenticated session and checks this role; remove `CRITERIA_REVIEW_TOKEN` from the normal UI flow. Record reviewer user ID, Firebase UID reference, proposal/source hash, decision, reason, and timestamp. If criteria later need tenant-specific overrides, represent overrides explicitly and version them rather than mutating shared rules.
- Initial roles: `workspace_admin` (manage members/settings and data), `analyst` (upload/view runs), `reviewer` (human criteria decision only where platform-authorized), and `viewer` (read-only). Assign the minimum capability needed; do not grant platform reviewer permission to every logged-in user.
- n8n service-to-service callbacks use a separate service identity/shared secret or signed webhook, never an end-user Firebase session cookie. n8n must receive and return `workspace_id`/batch correlation only after API authorization; do not expose n8n editor access as a substitute for dashboard auth.
- Define workspace-level retention/export/deletion behavior for uploaded CSVs and personal data before enabling multiple real users. Keep audit metadata with minimal necessary PII.

### Auth screens and UX

- Signed-out route is a clean ClickPe-branded sign-in page with configured Firebase sign-in provider buttons and a concise workspace-access explanation. No public self-registration/access to historical data until workspace provisioning is designed.
- After sign-in, show active workspace in the top bar and provide a workspace switcher only if membership supports multiple workspaces.
- Protect every dashboard route; redirect signed-out users through login, then restore the originally requested safe route after callback.
- Provide explicit `403` (signed in but lacks permission), session-expired, provider-unavailable, and account-not-invited screens. Never silently show an empty dashboard for access denial.
- Add team invitation/member management only after workspace ownership and email invitation policies are agreed. Local seeded accounts are local-only.

### Auth/API/database work required

1. Add Firebase client initialization/config validation, Firebase Admin SDK initialization, session exchange/logout endpoints, `/api/me`, CSRF protection for cookie-authenticated state-changing calls, and secure cookie configuration.
2. Add user/workspace/membership/session schema and workspace ownership columns/foreign keys to applicant batches and all derived records; include a migration/backfill strategy for existing local data.
3. Define how legacy unauthenticated local rows are assigned (one seeded local workspace) before turning auth enforcement on. Do not make old data visible to every new account by default.
4. Add workspace-scoped authorization dependencies to every API route, including batch details, run jobs, notification state/actions, and uploads; keep shared product read and platform review permissions separately explicit.
5. Add auth audit events for login/logout, invitation/membership changes, data export/deletion, and criteria decisions without logging tokens or source secrets.
6. Add tenant-isolation acceptance checks: User A cannot list/read/download/retry User B's batch, matches, errors, or notification state, including with guessed IDs; a viewer cannot upload; an analyst cannot approve global criteria.
7. Add rate limits/CSRF/session expiration/revocation handling and secure proxy/CORS settings. Current CORS is local-origin based and must be revisited for the deployed domain.

## 8. Visual system and interaction principles

- Palette from the current ClickPe site: navy `#001143`, blue `#295BEE`, bright green `#1CFF7D`, white, and cool neutral backgrounds. Keep green as a highlight/status accent; do not use it as the only success indicator.
- Use persistent left rail on desktop, collapsing to a mobile menu; stable content grid and consistent page gutters prevent the current “scattered page” feeling.
- Use navy for headings, blue for primary actions/links, green for small success/active accents, amber for needs-review/stale states, and red for failures.
- Apply shared spacing, card radius, typography scale, shadow, focus ring, and responsive breakpoints as design tokens.
- Motion: 150–220 ms for hover/route/card transitions; 250–400 ms for a newly created run timeline; honor reduced motion. Do not animate every row or flash active workflow steps.
- Keep dense operational tables scannable; use details pages/drawers for provenance and field-level evidence.
- All loading/error/empty/partial states are intentionally designed. Avoid blank panels and disabled buttons without a nearby reason.

## 9. Privacy, correctness, and reliability requirements

- Never display full applicant email addresses in overview/run tables. Mask or omit them in row-level troubleshooting; maintain least-privilege access if authentication is added.
- UI status and counts must come from persisted API/DB state; no optimistic “success” for workflows until callbacks confirm it.
- Keep test email routing clearly labeled; distinguish `SMTP accepted` from lender/user delivery. Current Mailpit is a local capture inbox.
- Make uploads and retries idempotent and show duplicate-file behavior explicitly.
- Preserve a criteria version/source snapshot for each matching job so historical runs can explain which lender data was used.
- Escape and constrain surfaced error text and source content. Link lender sources with safe external-link behavior.
- The current app is a local operator console, not a public borrower portal. Do not add public registration/login or expose applicant-level results to arbitrary users as part of the dashboard redesign.
- Before Firebase Authentication is enabled, there is no user-level tenancy boundary in the current API/database. Authentication must ship together with workspace ownership and authorization checks; adding only a login screen would create a false sense of data isolation.

## 10. Delivery phases

### Phase 0 — Confirm the information model

- Agree on run/batch/job terminology and final status definitions.
- Define run summary, event schema, progress denominator rules, retention, and privacy behavior.
- Map each visible dashboard field to a persisted backend source.
- Define workspace/user ownership, global versus workspace-scoped data, roles, invitation/provisioning policy, legacy data assignment, Firebase UID mapping, and session model.

### Phase 1 — Dashboard shell and read-only overview

- Migrate to Tailwind + shadcn/ui, add route navigation, sidebar/topbar, ClickPe design tokens, responsive layout, and overview cards.
- Add Firebase sign-in and API authorization/session foundations before showing private data to multiple identities.
- Implement real aggregate summary and paginated runs endpoints.
- Show recent history and pending-review/freshness attention items without fabricated charts.

### Phase 2 — Run detail and live tracking

- Add event persistence and correlation IDs to API and n8n workflows.
- Implement run detail endpoint and timeline with per-stage state, attempts, timestamps, counts, and errors.
- Add robust polling/reconnection and reduced-motion behavior.
- Move upload into its own screen and navigate to run detail immediately after commit.

### Phase 3 — Product and human review operations

- Move lender catalog and criteria review into separate routes.
- Add blocker/readiness explanation, proposal version history, decision audit, and clear approval confirmation.
- Keep server validation authoritative and proposals inactive until approved.

### Phase 3b — Product marketplace alignment

- Shape product catalog cards and category filters after ClickPe's public product discovery page.
- Initially expose only the personal-loan catalog this engine can source and evaluate. Add other ClickPe product categories only after approved product-specific criteria, data provenance, ownership, and evaluation plans are defined.

### Phase 4 — Operations and quality polish

- Add API/DB/n8n/Mailpit health, execution links, per-stage retry where safe, and failure explanation.
- Test duplicate upload/webhook, refresh during active run, API disconnection/recovery, concurrent runs, failed matching, partial notifications, stale source, empty/no-match file, and narrow mobile layouts.
- Measure dashboard query count/latency at 10k and larger batches. Add indexes/caching only from evidence.

## 11. Acceptance criteria

- Navigation routes directly to overview, runs, run details, uploads, products, review queue, and operations; reload/back/forward work.
- A new CSV upload navigates to its run detail and shows actual persisted stage changes as they occur.
- Refreshing or reopening an active run restores its state from the API and does not restart or duplicate the workflow.
- A run history entry shows a full aggregate summary and links to detailed validation/matching/notification evidence.
- Every timeline status, number, error, and timestamp maps to a persisted source; no fake percentages or invented events.
- Failures show stage, attempt, safe error summary, and supported next action; partial notifications are visibly distinct from complete delivery.
- Criteria review clearly communicates token and approval blockers, source evidence/freshness, human decision, and resulting rule state.
- Applicant PII is not shown in high-level dashboards or exposed to Gemini; sample recipient emails remain excluded from delivery.
- Layout works on desktop, tablet, and mobile; keyboard navigation and reduced-motion preferences are honored.
- ClickPe brand cues are consistent with the public site while the information architecture remains designed for internal loan operations.
- Tailwind tokens and shadcn/ui components are the primary styling system; no scattered one-off CSS recreates core controls.
- Login uses Firebase Authentication, with a backend-verified Firebase identity and secure session handling; no Firebase Admin service-account key or Gemini key is shipped to the React bundle.
- Criteria-review approve/reject actions require a signed-in account with the correct reviewer role. No manually pasted shared review token is required in the UI.
- Every workspace-owned endpoint enforces membership/role scope; cross-workspace ID guessing cannot read or mutate another workspace's data.
- Shared lender criteria and product catalog behavior are explicit; only authorized platform reviewers can approve globally shared lender rules.

## 12. Recommended implementation order

1. Agree on workspace access model, roles, Firebase sign-in provider and account provisioning, legacy-data assignment, and shared lender criteria ownership.
2. Design user/workspace/run/event schemas and define each status and progress counter.
3. Add Firebase Auth sessions and API authorization/data isolation together with migrations; do not ship a login-only facade.
4. Migrate styling to Tailwind and establish shadcn/ui primitives and app routes.
5. Add backend dashboard summary, paginated runs, run detail aggregates, and append-only stage events.
6. Build overview, runs, run detail, and upload-to-run live transition using real API state.
7. Add product catalog/review workspace and operations health; start with personal loans only.
8. Add motion, accessibility, responsive polish, and acceptance coverage after the real state and authorization models work.

## 13. External references

- [ClickPe product marketplace](https://clickpe.ai/product) — product category filters and compact lender/product cards.
- [ClickPe public homepage](https://clickpe.ai/) — visual brand and consumer journey context.
- [Tailwind CSS Vite installation](https://tailwindcss.com/docs/installation/using-vite) — official Vite integration guidance.
- [shadcn/ui Tailwind v4 guide](https://ui.shadcn.com/docs/tailwind-v4) — component setup and Tailwind v4 compatibility.
- [Firebase Authentication for web](https://firebase.google.com/docs/auth/web/start) — initialize Firebase Auth in the React client.
- [Firebase Google sign-in](https://firebase.google.com/docs/auth/web/google-signin) — optional Google sign-in provider setup in Firebase Auth.
- [Firebase ID token verification](https://firebase.google.com/docs/auth/admin/verify-id-tokens) — authenticate client identities on FastAPI.
- [Firebase session cookies](https://firebase.google.com/docs/auth/admin/manage-cookies) — server-managed `HttpOnly` session approach and CSRF guidance.
- [Firebase API key guidance](https://firebase.google.com/docs/projects/api-keys) — client config visibility and API-key restrictions.


## Implementation status — 2026-10-19

The dashboard plan is now implemented locally across the React client, FastAPI service, PostgreSQL model, and active n8n workflows. This status records shipped code and the remaining local environment actions; it does not imply production readiness.

### Implemented in this workspace

- React Router multi-screen operations dashboard: overview, searchable run history, detailed per-run event timeline/results, CSV upload with progress, lender catalog, criteria review, operations health, and team invites.
- Tailwind CSS 4 with Vite integration, reusable Radix UI/shadcn-style controls, responsive layouts, motion-reduced support, animated loading/progress states.
- Firebase Google sign-in client; Firebase Admin ID-token verification and HttpOnly session cookies in the API; verified-email requirement; CSRF protection; workspace membership and role authorization; bootstrap platform admin; workspace-scoped upload/run data; reviewer decisions tied to authenticated identity; platform-admin grant/revoke controls for reviewer access.
- API summaries and run detail assemble persisted database/job/match/notification/event records, with polling while matching is active.
- Internal n8n API calls use `X-Workflow-Token`; the value is sourced from local runtime environment and is not stored in workflow exports. Live workflows A/B/C were updated and published during this implementation.
- `GET /api/operations/health` checks PostgreSQL, Firebase Admin configuration, n8n, and Mailpit reachability.
- README now documents local Firebase setup, Admin credentials, required env values, workflow credentials, roles, service URLs, and common setup issues.

### Required local setup still owned by the developer

- Enable Google as a Firebase Auth provider and authorize `localhost` in Firebase Console.
- Provide `secrets/firebase-service-account.json` locally and set `FIREBASE_PROJECT_ID` plus `FIREBASE_BOOTSTRAP_ADMIN_EMAIL` in `.env`.
- Ensure a strong private `N8N_SERVICE_TOKEN` exists in `.env`, then recreate API and n8n containers so both receive it. The n8n workflows read the token from runtime environment.
- Assign/verify the Mailpit SMTP credential if rebuilding n8n from a fresh volume.

### Known scope limits before any hosted deployment

- The current persistent schema changes run as startup backfills; production should move to versioned Alembic migrations and have a reviewed backup/rollback process.
- Authentication is Firebase/Google for the supplied client; password/email provider and multi-factor configuration are not part of this slice.
- Workspaces and invites are implemented, while shared lender catalog/criteria remain platform-managed by design.
- Health checks show dependency reachability, not provider SLA or successful completion of every historical execution.
- Validate local callback authentication and run a complete upload → match → Mailpit flow only after the local Firebase Admin credential and n8n environment token are present.
