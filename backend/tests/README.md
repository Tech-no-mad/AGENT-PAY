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

Agent capture requires `constraints.auto_execute` to be explicitly true and uses
the authorization created by the current execution. Authorization-only payments
remain authorized. Completion requires a PayPal capture with status COMPLETED.
This change does not add a separate human approval/capture endpoint.

## Follow-up review regressions

`test_remaining_review_findings.py` covers BUG-9, BUG-10, BUG-11, BUG-12, BUG-14,
and BUG-15: the audit schema, cancellation races, partial constraint merging,
failed extraction, retryable voids, and category matching. The suite currently
contains 95 tests in total, including the original 34 security regressions.

Extraction must return a complete, typed object. Invalid JSON/shape/values or a
failed provider response raises an error before mandate insertion or worker
creation. Valid extraction is merged with only explicitly supplied, non-null
overrides. Missing optional fields do not overwrite extracted currency, approval,
deadline, or rules with model defaults. A positive final budget is mandatory.

`shared/purchase_categories.json` is the single category vocabulary imported by
the frontend and backend. Ship the `shared/` directory with both deployments.
Legacy `product`, `flight`, and `service` inputs remain accepted aliases. Broad
physical-goods mandates permit electronics/general results; category matching is
explicit rather than arbitrary substring matching.

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
