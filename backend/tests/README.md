# Payment security regression tests

From the repository root, install `backend/requirements-dev.txt` in a Python 3.12
virtual environment and run:

```powershell
python -m pytest backend/tests/test_payment_security.py -q -p no:cacheprovider
```

The tests mock external providers and database writes; they do not charge funds,
send emails, or modify a live Supabase database. They cover webhook verification,
authorization-time policy enforcement, capture permission and association,
token ownership/activity checks, and separate authentication clients.

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

Live PayPal verification, real payments, Supabase RLS, and browser interaction
require separate integration validation. Other findings from the original review
(audit insert schema, cancellation coordination/void failure handling, natural
language extraction/merging, and category vocabulary) remain follow-up work.
