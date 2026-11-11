# AgentPay: Setup and Operation

This guide explains how to configure AgentPay, run it locally, and operate its purchase workflow.

AgentPay uses a Next.js frontend, a FastAPI backend, Supabase, Cloudflare Workers AI, and PayPal Sandbox. Product search currently uses a **demo catalog**, and AI price estimates are **not live market quotes**. The normal application connects to the configured external services; it is not an offline simulation.

## 1. Prerequisites

Have the following available:

- Python 3.12, which is the version used by the project's test instructions.
- Node.js 18.17 or newer and npm, matching the installed Next.js 14 package's minimum runtime requirement.
- A Supabase project with access to its SQL editor, project URL, anonymous key, and service role key.
- A Cloudflare account with Workers AI access, an account ID, and an API token authorized to use Workers AI.
- A PayPal developer account, a sandbox application, and sandbox business and personal buyer accounts. The application/account must support the Vault operations used by this project.

Commands below use **PowerShell**, run from the repository root unless noted otherwise. Keep the `backend/`, `frontend/`, and `shared/` directories together; both applications depend on the shared purchase categories.

## 2. Install dependencies

Create a virtual environment and install the backend dependencies:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
```

If the Python launcher is unavailable, use `python -m venv .venv` with a Python 3.12 installation. Virtual environments copied from another computer may contain invalid interpreter paths; create a local environment instead of relying on a copied one.

Install the frontend dependencies:

```powershell
npm --prefix frontend install
```

On macOS/Linux, the equivalent environment commands are `python3.12 -m venv .venv` and `.venv/bin/python -m pip install -r backend/requirements.txt`. Substitute `.venv/bin/python` for the Windows interpreter path in subsequent commands.

## 3. Configure environment files

Create the configuration files only if they do not already exist:

```powershell
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
}
if (-not (Test-Path frontend/.env.local)) {
    Copy-Item frontend/.env.local.example frontend/.env.local
}
```

### Backend: root `.env`

Edit `.env` and replace the example values with your own:

```dotenv
PAYPAL_CLIENT_ID=your_sandbox_client_id
PAYPAL_CLIENT_SECRET=your_sandbox_client_secret
PAYPAL_WEBHOOK_ID=your_registered_sandbox_webhook_id

SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your_supabase_anon_key
SUPABASE_SERVICE_KEY=your_supabase_service_role_key

CLOUDFLARE_ACCOUNT_ID=your_cloudflare_account_id
CLOUDFLARE_API_TOKEN=your_workers_ai_token
CLOUDFLARE_MODEL=@cf/meta/llama-3.3-70b-instruct-fp8-fast

BACKEND_URL=http://localhost:8000
FRONTEND_URL=http://localhost:3000
```

Use your own Cloudflare account ID, including when copying the supplied example file. The backend needs the Supabase service role key for background database operations; an anonymous key alone is not a substitute for those permissions.

The startup command below explicitly loads the root `.env`. This avoids confusion if a separate `backend/.env` exists: configuration discovery without `--env-file .env` can load that file instead. Environment variables already set in the shell take precedence over dotenv values, so keep them consistent.

### Frontend: `frontend/.env.local`

```dotenv
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_supabase_anon_key
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Use the same Supabase project as the backend. `NEXT_PUBLIC_API_URL` is the backend origin, **without** an `/api` suffix; the client adds that prefix itself.

Only the public Supabase URL and anonymous key belong in browser configuration. Keep the Supabase service role key, PayPal secret, and Cloudflare token in backend configuration. Local environment files are excluded by the repository's `.gitignore`.

Restart the relevant server after changing environment variables. Rebuild the frontend if changing public variables for a production build.

## 4. Initialize Supabase

In your Supabase project's SQL editor, run these migration files in order:

1. [001_initial_schema.sql](supabase/migrations/001_initial_schema.sql): profiles, saved payment tokens, mandates, transactions, access policies, and realtime publication entries.
2. [002_payment_cancellation_coordination.sql](supabase/migrations/002_payment_cancellation_coordination.sql): cancellation and payment-operation coordination.
3. [003_ai_pricing_constraints.sql](supabase/migrations/003_ai_pricing_constraints.sql): the AI pricing audit event.
4. [004_purchase_confirmation.sql](supabase/migrations/004_purchase_confirmation.sql): the persisted purchase confirmation gate.

Run the initial migration on a fresh project. For an existing database, apply only migrations that have not already been applied; the initial schema creates existing objects and is not a reset script.

Enable email/password authentication for application users. If email confirmation is enabled, verify the signup email before logging in. Configure the authentication Site URL and permitted redirect URLs for the frontend origin you are using.

When updating an existing installation, stop or drain old backend workers before applying the migrations, and restart all workers on the same code version. Do not mix workers that bypass the newer cancellation or confirmation checks with updated workers.

## 5. Configure PayPal Sandbox and webhooks

Use the sandbox application's client ID and secret in `.env`. Use a **sandbox personal buyer account** when approving a saved payment method. The application's PayPal service currently selects the sandbox SDK environment and sandbox REST host in code; replacing credentials alone does not switch it to live payments.

For asynchronous payment notifications:

1. Make the backend reachable at a public HTTPS address, using your deployment or a development tunnel.
2. Register a webhook on the same PayPal sandbox application at `https://YOUR_BACKEND_HOST/api/webhooks/paypal`.
3. Subscribe to the payment lifecycle events handled by this repository:

   - `PAYMENT.AUTHORIZATION.CREATED`
   - `PAYMENT.AUTHORIZATION.VOIDED`
   - `PAYMENT.CAPTURE.COMPLETED`
   - `PAYMENT.CAPTURE.DENIED` / `PAYMENT.CAPTURE.DECLINED`, where available
   - `PAYMENT.CAPTURE.REFUNDED`

4. Save that webhook registration's ID as `PAYPAL_WEBHOOK_ID`, then restart the backend.

PayPal cannot deliver webhooks to a localhost-only address. You can start the local servers without webhook registration, but webhook verification and asynchronous updates will not work until it is configured. The handler verifies PayPal signatures; a hand-written unsigned POST is not a valid webhook test.

## 6. Start the application

### Terminal 1: backend

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --env-file .env --reload --port 8000
```

Check that the process is responding:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

Expected response: `status` is `healthy`. This endpoint checks that the API is running; it does not verify Supabase, Cloudflare, or PayPal credentials.

Interactive API documentation is available at `http://localhost:8000/docs`.

### Terminal 2: frontend

From the repository root:

```powershell
npm --prefix frontend run dev
```

Open `http://localhost:3000`. Keep both terminals running during a purchase workflow.

If you change ports or hosts, update `NEXT_PUBLIC_API_URL` and `FRONTEND_URL` accordingly. Use the same frontend origin throughout login and the PayPal return flow, because browser authentication storage is origin-specific.

## 7. Operate AgentPay

### Create an account and sign in

Open **Get Started** or `/login`, create an account, complete any required email verification, and sign in.

The login page's **Continue with Hackathon Sandbox Demo Mode** button only sets a placeholder browser token. It does not create a valid Supabase session or provide mocked backend responses. Use a real application account for an integrated sandbox run.

### Save a PayPal payment method

1. Open **Payment Methods** in the dashboard.
2. Choose **Connect PayPal Wallet** or **Add PayPal Sandbox Method**.
3. Approve the save-payment-method flow using the sandbox buyer account.
4. Return to AgentPay and wait for the saved method to appear.

The backend exchanges the approved setup token for a saved payment token and associates it with your application user. The agent uses a token reference rather than your card number or PayPal password.

### Create a purchase mandate

Choose **Create Mandate** and enter specific instructions, for example:

> Find a portable power bank with at least 20000mAh capacity for under USD 50 and purchase it.

Select a saved payment method and choose the auto-capture setting:

| Setting | Behavior after purchase confirmation and authorization |
| --- | --- |
| Auto-capture enabled | The agent can capture the authorized payment after the required checks. |
| Auto-capture disabled | Execution stops at authorization. Funds may be held, but the agent does not capture them. |

**Authorization-only mode currently has no separate manual capture button or human approval/capture API endpoint.** It is useful for inspecting the hold; use the cancellation flow to request its release when finished.

Click **Launch Autonomous Agent**. The pricing agent runs first, saving the estimated price range, category, and reasoning. The upper estimate becomes the spending ceiling, subject to the user's stated budget. A budget is optional in the form, but explicitly naming one makes your intended limit clear. Invalid pricing output stops execution before product search or payment.

### Review the proposed purchase

After a matching product passes the policy check, a **Confirm purchase** popup shows the product and price:

- **Continue** permits the purchase to proceed immediately.
- **Cancel** stops the mandate.
- **No response for ten seconds permits automatic continuation.**

The deadline is stored on the server. Refreshing, navigating away, or closing the browser does not cancel the mandate or restart the countdown. Disabling auto-capture still limits the flow to authorization after confirmation.

### Follow execution and inspect the outcome

Open the mandate detail page to see its price ceiling, reasoning, status, and activity timeline. Use **Audit History** to inspect events across mandates and filter by event type, payment reference, or details.

| Status | Meaning |
| --- | --- |
| `active` | The mandate has been created. |
| `searching` | The agent is searching for a suitable option. |
| `evaluating` | The proposal is being evaluated; confirmation may be pending. |
| `authorized` | PayPal has authorized funds; capture is not yet confirmed. |
| `completed` | Payment capture has been confirmed. |
| `cancelled` | The mandate was stopped; inspect the cancellation reason and payment events. |
| `failed` | Execution stopped because of an error; inspect the recorded reason. |

A policy rejection stops the mandate and blocks further payment attempts for it. The agent cannot retry that rejected mandate with corrected arguments or a different product.

### Cancel a mandate

On an ongoing mandate, select **Cancel & Void Hold** and confirm. If an authorization exists, the backend attempts to void it. Wait for the recorded outcome rather than assuming the first click has released the hold.

A pending cancellation can return HTTP 409 while a payment operation is in progress. A failed or unconfirmed void can return HTTP 503 and leave cancellation requested. Refresh the mandate and inspect its events before retrying the cancellation. A capture already completed by PayPal cannot be undone by cancellation; cancellation is not a refund operation.

Removing a payment method in **Payment Methods** deactivates its application record. It does not cancel existing mandates or refund completed payments.

## 8. Stop, restart, and maintain the application

Use `Ctrl+C` in each terminal to stop its server. Before stopping the backend, let active work finish or cancel it and verify the result. Agent execution runs in the backend process; a restart is not a guaranteed automatic resume mechanism. Development `--reload` can also interrupt work when files change.

After a restart, inspect any nonterminal mandates and their payment references before submitting replacement purchases. If the outcome of an authorization or capture is unknown, check the provider result rather than assuming the payment failed.

The detailed reconciliation procedure is in [backend/tests/README.md](backend/tests/README.md#cancellation-migration-and-operation). An unresolved operation claim must not be cleared just because it is old; it protects against duplicate payment attempts. Reconciliation requires verifying the PayPal outcome and updating the matching operation claim.

To run a local frontend production build:

```powershell
npm --prefix frontend run build
npm --prefix frontend run start
```

Run the backend without `--reload` for a stable local demonstration:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --env-file .env --port 8000
```

These commands do not configure production hosting, HTTPS, or process supervision.

## 9. Run the backend checks

Install development dependencies into the same virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest backend/tests -q -p no:cacheprovider
```

The tests mock external providers. They do not create real sandbox payments, send signup emails, or modify a live Supabase database. Passing them does not replace an integrated test of your own credentials, database migrations, PayPal permissions, and webhook delivery. See the [test documentation](backend/tests/README.md) for coverage and limitations.

## 10. Troubleshooting

| Symptom | What to check |
| --- | --- |
| Backend will not import or start | Run from the repository root, use the environment where dependencies were installed, and keep `shared/` present. |
| A copied virtual environment reports a missing Python executable | Create a local Python 3.12 environment and reinstall dependencies. |
| Browser reports failed API requests | Confirm the backend is running, `NEXT_PUBLIC_API_URL` has no `/api` suffix, and `FRONTEND_URL` matches the browser origin. Restart after environment changes. |
| Missing or invalid authentication token | Sign in with a real application account. The demo-mode placeholder does not authenticate backend requests. Verify email if required, or sign in again if the session expired. |
| Missing database column, constraint failure, or permission error | Apply all four migrations in order and configure the backend service role key for the same Supabase project. |
| Saved PayPal method never appears | Check sandbox credentials, Vault permissions, the buyer approval flow, the frontend return URL, and backend logs. |
| Pricing or search fails | Check the Cloudflare account ID, token permissions, model setting, and the mandate's recorded failure reason. The demo catalog may not contain the requested product. |
| Purchase continues after leaving the page | This is the persisted ten-second confirmation behavior. Use **Cancel** to stop the purchase. |
| Mandate stays authorized | Auto-capture may be disabled, or execution may have stopped after authorization. Review the setting and audit trail; there is no manual capture endpoint in this version. |
| Cancellation remains pending | Inspect the active payment operation and PayPal outcome; follow the reconciliation notes above. |
| Webhook returns 400 / 401 / 503 | Check signature headers, signature validity, the registered webhook ID, and provider verification availability. |

Backend timestamps are logged in UTC. The frontend displays recorded timestamps in the browser's timezone. Include an explicit timezone in purchase instructions when using time-sensitive requirements.

## 11. Recorded walkthrough

If the generated demo artifacts are included in your checkout, the narrated walkthrough is at `artifacts/demo-video/AgentPay-hackathon-demo.mp4`, with a local player at `artifacts/demo-video/watch-demo.html`.

That recording uses the real interface with isolated sample responses and simulated payment outcomes. It illustrates the workflow; it is not evidence of a live sandbox transaction. Its reproduction instructions are in `artifacts/demo-video/README.md`.
