# Loan Eligibility Engine — local operations workspace

The local stack includes a React + Tailwind dashboard, FastAPI, PostgreSQL, n8n, and Mailpit. It supports CSV ingestion, deterministic SQL matching, sourced lender-product data, an optional Gemini-assisted criteria proposal that requires a reviewer, and controlled test notifications.

## 1. Configure Firebase sign-in

The Firebase web app configuration belongs in the local `.env` as `VITE_FIREBASE_*` values. These values identify the client app and are not server credentials. In Firebase Console:

1. Open **Authentication → Sign-in method** and enable **Google** (the UI currently implements Google sign-in).
2. Add `localhost` under **Authentication → Settings → Authorized domains**.
3. In **Project settings → Service accounts**, generate a Firebase Admin SDK private key. Save it as `secrets/firebase-service-account.json`. Keep this JSON private and never commit it; `secrets/` is ignored and mounted read-only into the API container.
4. Set `FIREBASE_PROJECT_ID` to the Firebase project ID and `FIREBASE_BOOTSTRAP_ADMIN_EMAIL` to the verified Google account that should become the first workspace and criteria administrator. This is the app administrator account, separate from the n8n owner account.

The API creates an HttpOnly Firebase session cookie after verifying the ID token and verified email. New users need a workspace invite; the configured bootstrap admin is provisioned automatically. Workspace admins can invite analysts or viewers in **Team & access**; the platform admin can grant or remove criteria-review access for workspace members there. Criteria approval uses the signed-in platform reviewer role; there is no pasted review token.

## 2. Configure local services

1. Copy `.env.example` to `.env` (or keep your existing `.env`) and set a strong local PostgreSQL password, `N8N_ENCRYPTION_KEY`, Firebase settings above, and a random `N8N_SERVICE_TOKEN` of at least 32 characters. Keep `.env` private.
2. Set `NOTIFICATION_TEST_RECIPIENT=test@clickpe.dev` to enable local test email. Messages go to Mailpit at http://localhost:8025; they are not delivered to the external address and CSV applicant addresses are never used as recipients.
3. Start/rebuild from the project root: `docker compose up --build -d`.
4. Open the dashboard at http://localhost:5173, API docs at http://localhost:8000/docs, n8n at http://localhost:5678, and Mailpit at http://localhost:8025. PostgreSQL is exposed on host port `5433` (container port `5432`).

The API reads Firebase Admin credentials from `/run/secrets/firebase-service-account.json`. If the local credential file or bootstrap email is absent, Firebase sign-in/workspace provisioning cannot complete; the Operations screen reports Firebase readiness.

## 3. n8n workflows

Import the three JSON files from `workflows/` if starting with an empty n8n volume:

- `product-discovery.json` — daily lender source refresh and criteria-review queue.
- `user-loan-matching.json` — new-batch webhook, set-based SQL matching, and notification handoff.
- `user-notifications.json` — bounded messages delivered to Mailpit.

Assign an SMTP credential to the notification email node with host `mailpit`, port `1025`, and no TLS/authentication. The active local workflows have been updated to read `N8N_SERVICE_TOKEN` from the n8n runtime environment for API calls. Do not put this token into workflow JSON. Recreate/restart n8n after changing `.env` so the container receives the token. Workflow exports use the same environment expression and contain no key value.

## Dashboard pages

- **Overview:** workspace run counts, applicants, potential matches, attention items, active matching jobs, and recent runs.
- **Runs:** searchable paginated history.
- **Run detail:** accepted/rejected rows, live matching state, event timeline, criteria versions, match outcomes, and bounded notification progress.
- **New upload:** CSV upload progress and automatic navigation to the created run.
- **Products:** published lender criteria, source links, and freshness state.
- **Criteria review:** proposal, evidence quotes, source links, uncertainties, and approve/reject confirmation. Approval validates the proposal hash and current source evidence on the server.
- **Operations:** API-visible database, Firebase Admin, n8n, and Mailpit health.
- **Team & access:** invite verified Firebase accounts as analysts or viewers.

## Input and matching contract

CSV columns required: `user_id,name,email,monthly_income,credit_score,employment_status,age`. Uploads are UTF-8 CSV up to 25 MB. Invalid rows are reported per run; identical file content within a workspace reuses the existing batch. The initial sample is `users.csv` (10,000 rows).

Matching uses a database-side candidate filter and idempotent bulk persistence. Applicant data is not sent to Gemini. Missing factors such as employment tenure, salary-account relationship, sector, or net/gross income basis remain explicit follow-up checks. A potential match is not a lender approval or offer. The local example assumes monthly INR and India.

Lender discovery first uses deterministic extraction from configured official sources. Gemini may propose typed criteria only when required product facts cannot be reliably structured deterministically; proposals remain inactive until a platform reviewer approves them. Never commit `LLM_API_KEY`.

## API overview

- `GET /api/health` — basic API/database health and Firebase configuration indicators.
- `GET /api/operations/health` — authenticated service health used by the dashboard.
- `POST /api/auth/session`, `POST /api/auth/logout`, `GET /api/me` — Firebase session exchange and current workspace profile.
- `POST /api/uploads`, `GET /api/uploads`, `GET /api/runs`, `GET /api/runs/{batch_id}` — workspace-scoped ingestion and run history.
- `GET /api/dashboard/summary` — workspace KPI and active-job data.
- `GET /api/products`, `GET /api/products/review-queue`, `POST /api/products/{product_id}/criteria-review` — lender catalog and role-controlled review.
- `/api/workflows/*` — n8n service operations protected by `X-Workflow-Token`; browser actions that start test delivery also require a Firebase session and CSRF check.

## Troubleshooting

- **Sign-in says no workspace:** verify the email matches `FIREBASE_BOOTSTRAP_ADMIN_EMAIL`, or invite that email from an existing workspace administrator.
- **Firebase Admin not configured:** check project ID and the mounted `secrets/firebase-service-account.json`; restart the API after changes.
- **n8n calls return 401:** ensure `.env` has a long random `N8N_SERVICE_TOKEN`, then recreate the n8n and API containers with `docker compose up --build -d api n8n`.
- **n8n SMTP node fails:** recreate/select its Mailpit credential and confirm `mailpit:1025`.
- **No matching workflow starts:** confirm workflow B is active and its production webhook path is `loan-match`.

## Stop and reset

Stop containers with `docker compose down`. `docker compose down -v` permanently deletes local PostgreSQL and n8n volumes. Uploaded source files remain in `uploads/` until removed manually.

## Evaluation commands

Existing evaluation suite (no Gemini calls or email delivery): from `backend/`, install `requirements-dev.txt` and run `python -m pytest evals -q`. The evaluations cover deterministic applicant rules, source-grounded proposal validation/privacy, notification recipient safety, and n8n export topology.
