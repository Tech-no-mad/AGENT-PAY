# Payment security regression tests

From the repository root, install `backend/requirements-dev.txt` in a Python 3.12
virtual environment and run:

```powershell
python -m pytest backend/tests -q -p no:cacheprovider
```

The tests mock external providers and do not charge funds, send emails, or modify
a live Supabase database. The SQLite-backed PostgREST adapter executes the audit
table DDL from `001_initial_schema.sql` (adapting provider-specific defaults) to
check real columns, event checks, and foreign keys. It also exercises atomic
conditional mandate updates across two coordinator instances. This is not a live
PostgREST/RLS or PostgreSQL migration test.

`test_ai_recovery.py` verifies bounded retries for transient AI connection,
timeout, and remote-protocol failures, plus HTTP 429 and 5xx responses. Only the
AI request is retried; searches and payment actions are not replayed. Exhausted
retries and exceptions with empty messages persist a nonempty failure reason,
exception type, and execution stage for the UI. The reported quiet-keyboard
flow is exercised with a simulated post-search connection failure and mocked
payments.

Before using webhooks, set `PAYPAL_WEBHOOK_ID` to the ID of the webhook registered
for the configured PayPal sandbox application. Missing configuration or provider
verification outages return HTTP 503, invalid signatures return HTTP 401, and
missing signature headers return HTTP 400. Only verified notifications reach
database writes. Verification follows PayPal's verification API:
https://developer.paypal.com/api/rest/webhooks/rest/

Authorization now requires a finite positive `max_price` budget, a finite positive
purchase amount, matching currency/category, and a valid unexpired deadline when
specified. Missing budgets fail closed. The authorization tool includes category
and product name. The UI and API use `payment_tokens.id`; the backend resolves an
active token owned by the buyer to its PayPal vault identifier immediately before
creating the order.

`test_paypal_sdk.py` runs the installed SDK's actual models, request serialization,
controllers, and response parsing against a stubbed HTTP transport. Wallet orders
use `payment_source.paypal.vault_id` and `AUTHORIZE` intent. Create and authorize
request full responses so the authorization ID is available before capture.
Local model-construction errors record a failed operation and release its claim;
errors after submission retain the claim until the payment outcome is reconciled.
These tests do not contact PayPal or create sandbox payments.

Agent capture requires `constraints.auto_execute` to be explicitly true and uses
the authorization created by the current execution. Authorization-only payments
remain authorized. Completion requires a PayPal capture with status COMPLETED.
This change does not add a separate human approval/capture endpoint.

The pre-purchase confirmation gate runs after policy approval and before any
PayPal authorization. Apply migration `004_purchase_confirmation.sql` after 003
and restart all workers together. Product, amount, currency, decision ID, state,
and a ten-second server deadline are persisted on the mandate. Continue and
Cancel use an owner-scoped endpoint with conditional updates; only the backend
may select timeout approval. No decision before the deadline means automatic
continuation. Late clicks receive HTTP 409, and a stale offer cannot approve a
replacement. A changed product/price requires another confirmation window.

Authorization claims require the same approved confirmation ID and a persisted
decision audit. Cancellation remains authoritative. The existing auto-capture
setting still determines whether authorization proceeds to capture. The frontend
polls pending confirmations across the dashboard and adjusts its countdown to the
server time. Reloading or leaving the page never restarts the server deadline.

`test_purchase_confirmation.py` uses a controlled clock to verify the full
ten-second wait, both buttons, deadline races, changed offers, owner scoping,
duplicate decisions, and audit failures without real payments. Older payment
tests use a zero-length window while exercising the same gate and audit path.

The first policy rejection cancels the mandate. This includes malformed amounts
(for example `"285 USD"`, booleans, or nonfinite values), order-time
policy revalidation, and expired deadlines before capture. The backend persists
`cancel_requested` before audit writes or releasing a payment claim, then ends the
agent loop. Corrected arguments, another product, another worker, or remaining
tool calls in the same batch cannot resume approval or payment. Existing holds
are voided through the cancellation coordinator; unconfirmed release remains
pending with capture blocked. The rejection reason is retained in the audit trail.
`test_policy_rejection.py` covers these cases, including audit failures and
authorization already in flight.

`tool_calls.py` normalizes model responses before dispatch. It accepts native
function calls or a complete JSON action in the response text, including
`name`/`parameters`, fenced JSON, and the model's invalid escaped-apostrophe
encoding inside JSON strings. It never extracts executable actions from prose
or evaluates code. Only known tools are dispatched. Native call IDs are preserved
and recovered calls receive matching IDs in the conversation history.

Plain decimal strings on numeric tool fields (e.g. `"39.99"`) become numbers
before the first policy decision. This is input normalization, not a retry after
rejection. Budget/category/currency checks and cancellation rules still apply.
Malformed/ambiguous tool envelopes fail explicitly. `test_tool_calls.py` covers
the reported power-bank response through policy approval, authorization and
capture with mocked providers, as well as rejection and manual-approval guards.

## Follow-up review regressions

`test_remaining_review_findings.py` covers BUG-9, BUG-10, BUG-11, BUG-12, BUG-14,
and BUG-15: the audit schema, cancellation races, partial constraint merging,
failed extraction, retryable voids, and category matching. These run alongside
the original security regressions, main-integration regressions, Cloudflare
request contracts, and timestamp/deadline tests.

Creation validates and stores only explicitly supplied, non-null controls, then
starts the orchestrator. `PricingCategoryAgent` is its first model call, before
the shopping tool loop. It infers `min_price`, `max_price`, a canonical category,
and `pricing_reasoning`, along with currency, approval, deadline and other rules.
The full typed output is persisted and audited before shopping. Missing user
budgets are allowed; missing/invalid AI budgets fail the worker before payment.
Explicit API caps may tighten the estimate, never increase it. Currency mismatch,
nonfinite/inverted ranges, unsupported categories, and failed responses stop the
worker. The minimum is an estimate, not a policy floor that rejects cheaper deals.

`test_pricing_category_agent.py` covers creation without a budget, first-call
ordering, persistence and audit before search, final policy enforcement,
cancellation during pricing, and malformed or failed responses. The legacy
extraction helper's validation regressions remain covered separately.

Apply `003_ai_pricing_constraints.sql` after migration 002 before starting the
updated backend. It adds `constraints_defined` to the audit event check. Estimates
use model knowledge, not live market data; the product catalog is still mocked.

`test_product_search.py` covers the power-bank search regression: product family
matching precedes category filtering, capacity requirements such as 20000mAh+ are
checked against demo specifications, and unrelated items are not substituted.
Search uses the saved category/currency/budget when tool arguments omit them and
cannot raise the stored price ceiling. Audit records include returned products.
Post-search model stops, provider errors, and turn exhaustion now record a failed
event with a reason and the last tool, while preserving authorized payment state.

`shared/purchase_categories.json` is the single category vocabulary imported by
the frontend and backend. Ship the `shared/` directory with both deployments.
Legacy `product`, `flight`, and `service` inputs remain accepted aliases. Broad
physical-goods mandates permit electronics/general results; category matching is
explicit rather than arbitrary substring matching.

## Timestamp and deadline conventions

Runtime timestamps come from `backend/services/time_utils.py`: every call reads
the current UTC clock and serializes an ISO-8601 timestamp with an explicit
offset. Database-generated creation/update times use PostgreSQL `now()` in
`timestamptz` columns. Backend log lines use UTC with a `Z` suffix. PayPal's signed
transmission timestamp and historical audit timestamps retain their original
meaning; they are not replaced with the current time when read or displayed.

Every Cloudflare HTTP attempt receives fresh current-time context, including
retries and later decision turns, without modifying stored conversation history.
Relative dates are resolved in UTC unless the instructions specify a timezone.
Travel/event dates are preserved as product requirements, while an explicit
deadline is the latest time to complete the purchase. Saved absolute dates do
not move as time advances. Explicit expired deadlines are rejected before insertion;
expired AI-derived deadlines stop the planning stage. Authorization and capture
recheck the current clock and reject at or after the
deadline. An expired capture cancels the mandate and attempts to void its authorization.

Frontend creation/activity/history views format recorded instants in the
browser's timezone, with timezone labels on times. Missing or ambiguous timestamps
display `Time unavailable` rather than a guessed time. Fixed dates in tests are
deliberate frozen-clock or invalid/expired cases; valid rolling-deadline fixtures
are generated relative to the test's current clock.

## Cancellation migration and operation

1. Drain/stop existing background workers before rollout. Do not mix old workers
   that ignore cancellation with the new coordinator.
2. Apply `002_payment_cancellation_coordination.sql` after the initial migration,
   before starting the updated backend. No live migration is run by the tests.
3. Deploy all workers using the new coordinator and include `shared/`.

The migration adds `cancel_requested`, `payment_operation`,
`payment_operation_id`, and `payment_started_at`. An atomic conditional UPDATE
claims a payment only when cancellation has not been requested and no operation
already owns the mandate. Completion/checkpoint writes match the claim UUID.
Ordinary status writes cannot change terminal, cancel-requested, or claimed rows.

The process-wide task registry also lets a request-scoped service stop an AI
await started by another request in the same process. This is an optimization;
the durable stop flag and conditional payment claims enforce cross-process safety.

Cancellation sets the durable stop flag before touching PayPal. A worker
rechecks state after each AI await and before tool dispatch. If authorization
was already in flight, the authorization is recorded, then voided before any
capture can begin. An order with no authorization is cancelled without creating
a new hold. A capture already sent to PayPal cannot be undone by cancellation;
a completed capture remains completed and is not reported cancelled.

Cancellation during an outstanding operation returns HTTP 409, preserving the
request for retry/reconciliation. Failed voids return HTTP 503, keep the original
non-cancelled status and authorization, and keep `cancel_requested=true` so
payment tools cannot resume. The caller can retry the same cancellation.
Void retries use a stable PayPal request ID. After a void timeout/rejection,
the coordinator accepts success only if an authorization lookup confirms VOIDED.

An unknown authorization/capture outcome (network failure, pending capture, or
missing provider confirmation) retains its operation claim. There is deliberately
no timer that clears it and permits a duplicate charge. An operator must check
PayPal using the stored order/authorization and request ID, then persist the
verified result and clear all three operation fields in an update conditional on
the same claim UUID. Never clear a claim solely because it is old. Unknown
order/capture reconciliation is not automated by this patch.

## Audit contract

Audit rows contain the required `user_id`, a schema-permitted lowercase event,
and `paypal_resource_id`. The original event name, provider status, and all
individual PayPal IDs are preserved inside `details`. Policy decisions map to
`policy_approved`/`policy_rejected`; mandate cancellation/completion use
`voided`/`captured` with their original action retained in `details` because the
initial schema has no separate mandate-cancelled/completed event values.

Audit persistence failures raise `AuditLogError` rather than disappearing.
Financial outcomes are saved before audit writes; an audit outage does not turn
a confirmed capture into a failure or allow another charge. The activity/history
UI renders structured JSON safely and reads `paypal_resource_id`.

Live PayPal verification/payments, Supabase RLS, PostgreSQL deployment, and browser
interaction still require separate integration validation. The local frontend
build/type check uses placeholder public Supabase configuration, not live keys.

## Integrating the main-branch fixes

The resolution of main at `2f6272d` retains its configurable Llama 3.3 model,
explicit model override, development anonymous-key fallback, legacy category
aliases, activity metadata rendering, canonical audit labels, and dashboard copy.
The anonymous fallback remains subject to RLS and does not grant privileged
payment/audit access; production background operations require the service key.

`test_main_integration.py` checks those configuration contracts, exact failure
event normalization, the explicit `paypal_resource_id` argument, legacy aliases,
rejection of wildcard categories, and cancellation between separate request
service instances. Main's new review scenarios remain covered, using persisted
schema-backed rows rather than unconfigured MagicMock return values. The invalid
AI JSON test patches `chat_with_tools`, the method actually used, so it cannot
accidentally make a provider request.
