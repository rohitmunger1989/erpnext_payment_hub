# ERPNext Payment Hub — Phase 1 v0.1.2

Provider-agnostic payment gateway foundation for ERPNext.

Supported adapters in Phase 1:

- Tap Payments
- MyFatoorah
- UPayments

## Core design

ERPNext calls one common payment API. The selected backend provider can be changed in **Payment Hub Settings**.

Every electronic payment is saved as a **Gateway Transaction** with the provider account and provider transaction references.

Refunds **always use the provider account stored on the original payment transaction**, even if the system default provider is changed later.

Provider account states:

- `Test`
- `Active`
- `Refund Only`
- `Disabled`

This allows an old gateway to remain available only for refunds after switching new sales to another gateway.

## Phase 1 scope

Included:

- Provider settings
- Default provider routing
- Tap hosted/charge flow foundation
- MyFatoorah v3 hosted payment flow
- UPayments UInterfaceV2 charge flow
- Payment status refresh
- Gateway transaction audit log
- Full/partial refund routing
- Original-provider refund enforcement
- Server-to-server webhook verification by re-querying payment status
- KWD 3-decimal handling

Not yet included:

- ERPNext POS payment modal/buttons
- Physical SmartPOS / ECR terminal control
- Automatic Payment Entry creation
- Automatic POS Invoice submit after capture
- Refund status polling scheduler
- Store-to-terminal mapping
- Reconciliation dashboard
- Provider-specific webhook signature validation

Those belong in Phase 2 after Phase 1 is installed and tested.

## Install for local testing

This package targets Frappe / ERPNext v15 and v16.

### Option A — use the included app folder

Copy `erpnext_payment_hub` into your bench `apps` directory, then from the bench directory:

```bash
./env/bin/pip install -e apps/erpnext_payment_hub
# First ensure the existing file ends with a newline, then append the app:
printf '\n' >> sites/apps.txt
grep -qxF 'erpnext_payment_hub' sites/apps.txt || echo 'erpnext_payment_hub' >> sites/apps.txt
bench --site YOUR_SITE install-app erpnext_payment_hub
bench --site YOUR_SITE migrate
bench clear-cache
```

If the app name is already in `sites/apps.txt`, do not add it again.

### Option B — create scaffold first

```bash
bench new-app erpnext_payment_hub
```

Then replace the generated app folder with this package and run:

```bash
bench --site YOUR_SITE install-app erpnext_payment_hub
bench --site YOUR_SITE migrate
```

## Configure

Search ERPNext Desk for:

1. **Payment Provider Account**
2. **Payment Hub Settings**
3. **Gateway Transaction**

The installer creates three disabled accounts:

- Tap Payments
- MyFatoorah
- UPayments

Open one provider account, enter its sandbox/test credentials, set status to `Test`, then select it as **Default Provider** in Payment Hub Settings.

### Tap

Typical fields:

- Secret Key
- Merchant ID
- Test Mode = enabled
- Status = Test

### MyFatoorah

Typical fields:

- API Key / Token
- Test Mode = enabled
- Status = Test

Sandbox base is selected automatically.

### UPayments

Typical fields:

- API Key / Token
- Test Mode = enabled
- Status = Test

Sandbox base is selected automatically.

For UPayments production, set **Base URL Override** to the live API base URL supplied/confirmed for your merchant account.

## Test from bench console

```bash
bench --site YOUR_SITE console
```

Example:

```python
from erpnext_payment_hub.api import create_payment

result = create_payment(
    reference_doctype="Sales Invoice",
    reference_name="ACC-SINV-2026-00001",
    amount=1.000,
    payment_method="KNET",
    currency="KWD",
)

print(result)
```

The response includes:

- Gateway Transaction ID
- Provider
- Status
- Payment URL

Open the payment URL in a browser and use the provider sandbox.

## Refresh payment status

```python
from erpnext_payment_hub.api import refresh_transaction
refresh_transaction("PGT-2026-00001")
```

## Test refund

Only test after the original transaction becomes `Captured`.

```python
from erpnext_payment_hub.api import refund_transaction

refund_transaction(
    transaction_name="PGT-2026-00001",
    amount=0.500,
    reason="Test partial return",
)
```

The refund is sent through the provider stored on `PGT-2026-00001`, not through the current default provider.

## Run tests

```bash
bench --site YOUR_SITE run-tests --app erpnext_payment_hub
```

## Safety before production

Do not use this Phase 1 package for live money until:

- provider sandbox tests pass
- webhook behavior is confirmed for the merchant account
- provider-specific signature validation is enabled
- refund status reconciliation is implemented
- ERPNext accounting entries are tied to verified captured states
- terminal/POS flow is tested separately

## License

MIT


## v0.1.2 layout correction

Frappe DocTypes are now stored inside the module package:

```text
erpnext_payment_hub/
└── erpnext_payment_hub/
    ├── hooks.py
    ├── modules.txt
    ├── api.py
    ├── gateway.py
    └── erpnext_payment_hub/
        └── doctype/
            ├── payment_hub_settings/
            ├── payment_provider_account/
            └── gateway_transaction/
```

This is required so Frappe can resolve the `ERPNext Payment Hub` module and import the DocType controllers correctly.


## v0.1.3 — Multiple terminal mapping

A new **Payment Terminal** DocType supports multiple branches and POS stations.

Recommended mapping:

```text
Provider Account: Tap Payments

Salmiya POS 1  -> Terminal T001
Salmiya POS 2  -> Terminal T002
Jahra POS 1    -> Terminal T003
Jahra POS 2    -> Terminal T004
```

Resolution order for a new POS transaction:

1. Exact POS Profile
2. Branch default terminal
3. Any enabled terminal for the branch by priority
4. Provider Account default terminal ID as fallback

Every Gateway Transaction now stores the resolved:

- Branch
- POS Profile
- Payment Terminal
- Terminal ID

This is important for audit, reconciliation, and future SmartPOS/ECR integration.


## v0.1.4 — POS Station + Windows computer name

The app now supports physical POS station identity separately from POS Profile.

Example:

```text
POS Profile: Salmiya

SAL-POS-01 -> Tap Terminal T001
SAL-POS-02 -> Tap Terminal T002
SAL-POS-03 -> Tap Terminal T003
SAL-POS-04 -> Tap Terminal T004
```

Each Windows POS machine can be named accordingly.

A small local helper exposes only the computer name at:

```text
http://127.0.0.1:8765/device
```

POSNext / POS Awesome integration can read this hostname and send it to Payment Hub.

Resolution priority:

1. Windows Computer Name
2. Pairing Code
3. Unique POS Station for POS Profile
4. Unique POS Station for Branch
5. Existing Payment Terminal fallback logic

Every Gateway Transaction can now retain:

- POS Station
- Computer Name
- POS Profile
- Branch
- Payment Terminal
- Terminal ID
- Provider
- Provider transaction IDs

This makes clearing browser cache/localStorage harmless when the Windows helper is available.


## v0.1.5 — Credential fallback fix

Fixed Frappe password-field handling when a provider uses only `API Key / Token`
and leaves `Secret Key` blank. Empty password fields are now checked with
`raise_exception=False`, allowing UPayments and MyFatoorah API-token
configurations to work correctly.


## v0.1.6 — Gateway Transaction naming fix

Corrected the Frappe autoname pattern for Gateway Transaction from the old/invalid
dot-token form to:

```text
format:PGT-{YYYY}-{#####}
```

New records will be named like:

```text
PGT-2026-00001
PGT-2026-00002
```


## v0.1.7 — UPayments session/status synchronization

- Stores UPayments `session_id` immediately from the charge response URL.
- Supports status lookup by `session_id` before a `track_id` exists.
- Webhook can locate a payment by the merchant/requested order ID.
- Webhook saves `track_id` / `payment_id` then verifies status server-to-server.
- Browser return flow also verifies UPayments status server-to-server.
- Corrected Gateway Transaction naming for Frappe v16 expression syntax:

```text
PGT-.YYYY.-.#####
```

Expected new transaction names:

```text
PGT-2026-00001
PGT-2026-00002
```


## v0.1.8 — UPayments status parser fix
Reads the real payment state from `data.transactionData.result` / `data.payMit.order.status` instead of the top-level boolean API success flag.


## v0.1.9 — UPayments final transaction response fix

Live sandbox testing showed that UPayments currently returns final payment data under:

```text
data.transaction
```

including:

```text
result
payment_id
track_id
order_id
merchant_requested_order_id
payment_type
session_id
```

The parser now supports `data.transaction` in addition to the previously handled
`data.transactionData` / `data.transaction_data` shapes.

A successful KNET payment such as `result = CAPTURED` will now update the
Gateway Transaction to `Captured` and save the final provider references.


## v0.1.10 — UPayments sandbox refund guard

UPayments currently rejects refund execution in Sandbox with:

```text
work_in_production_only
```

Payment Hub now detects UPayments Test Mode before sending the refund request and
shows a clear message that refunds must be tested with a live captured transaction.

No refund transaction or refunded amount is reserved when the provider rejects or
blocks the refund request.


## v0.1.11 — Tap Payments sandbox readiness

- Enforces Tap's documented minimum charge of 0.100 in the transaction currency.
- Normalizes Kuwait phone numbers before sending them to Tap.
- Handles Tap redirect `tap_id` and verifies the charge server-to-server.
- Saves Tap payment method details (for example KNET) when returned by the API.
- Tap sandbox uses the normal `https://api.tap.company/v2` API hostname; test/live
  mode is selected by the secret key (`sk_test_...` vs `sk_live_...`).


## v0.1.12 — Tap refund readiness

- Normalizes free-text ERPNext refund notes to Tap's supported refund reason codes.
- Preserves the original ERPNext refund note in Tap metadata.
- Maps Tap `ACCEPTED` refund status to Pending.
- Maps Tap `REJECTED` and `RESTRICTED` refund statuses to Failed.


## v0.1.13 — MyFatoorah sandbox readiness

- Uses the current MyFatoorah v3 customer mobile structure:
  `Customer.Mobile.CountryCode` + `Customer.Mobile.Number`.
- Handles the MyFatoorah v3 browser return `paymentId`.
- Supports MyFatoorah Webhook V2 nested `Data.Transaction.PaymentId`.
- Saves the actual MyFatoorah payment method from Get Payment Details.


## v0.1.14 — MyFatoorah hosted checkout callback fix

- MyFatoorah v3 Create Payment normally returns `PaymentId = null`.
- After checkout, MyFatoorah appends `paymentId` to the Redirection URL.
- Payment Hub now queries MyFatoorah with that returned PaymentId, obtains the
  InvoiceId, then matches the pending ERPNext Gateway Transaction by InvoiceId.
- Existing pending hosted-payment links created by v0.1.13 can be completed
  after upgrading to v0.1.14.


## v0.1.15 — MyFatoorah InvoiceId recovery

- `refresh_transaction()` no longer requires MyFatoorah `PaymentId` to already
  be stored.
- If the hosted checkout completes but the browser does not return through the
  ERPNext callback, Payment Hub queries MyFatoorah `v2/GetPaymentStatus` using
  the already stored `InvoiceId`.
- When that inquiry returns a `PaymentId`, Payment Hub immediately verifies the
  transaction again with the current `GET /v3/payments/{paymentId}` endpoint.
- This allows existing pending transactions such as `PGT-2026-00008` to recover
  and synchronize without creating another payment.


## v0.1.16 — MyFatoorah refund timeout safety

- MyFatoorah refund calls now allow a longer response window.
- Before sending another refund POST, Payment Hub checks `GetRefundStatus` by
  InvoiceId for an already-created matching request.
- Refund requests use a deterministic `ExternalIdentifier` so a retry after a
  timeout can be recovered instead of duplicated.
- Legacy v0.1.15 timeout attempts using the original Gateway Transaction name
  as `ExternalIdentifier` are also recoverable.
- `refresh_transaction()` now supports MyFatoorah Refund transactions through
  `GetRefundStatus`.

# v0.2.0 — Common POS Payment Backend

This release adds a provider-agnostic POS payment session layer designed to be shared by POSNext and POS Awesome.

## New DocTypes
- POS Payment Session (`PPS-YYYY-#####`)
- POS Payment Allocation (`PPA-YYYY-#####`)

## Core flows
- Cash allocations
- Split payments
- Asynchronous electronic payment links (save pending sale, serve next customer)
- Pending-payment and paid-pending-sale queues
- Gateway webhook/status synchronization back into POS sessions
- Realtime session status events (`payment_hub_pos_update`)
- Five-minute low-rate status reconciliation fallback
- Physical terminal API contract (`start_terminal_payment`) ready for provider SmartPOS/ECR adapters
- Final invoice linking only after confirmed payment

## Shared APIs
- `erpnext_payment_hub.pos.api.get_pos_payment_config`
- `erpnext_payment_hub.pos.api.create_pos_session`
- `erpnext_payment_hub.pos.api.save_pos_draft`
- `erpnext_payment_hub.pos.api.get_pos_session`
- `erpnext_payment_hub.pos.api.add_cash_allocation`
- `erpnext_payment_hub.pos.api.create_payment_link`
- `erpnext_payment_hub.pos.api.compose_payment_message`
- `erpnext_payment_hub.pos.api.mark_payment_link_sent`
- `erpnext_payment_hub.pos.api.check_pos_payment`
- `erpnext_payment_hub.pos.api.start_terminal_payment`
- `erpnext_payment_hub.pos.api.finalize_pos_session`
- `erpnext_payment_hub.pos.api.get_pending_payments`
- `erpnext_payment_hub.pos.api.get_paid_pending_sales`

POSNext and POS Awesome frontend adapters are intentionally not patched in this release. Both will call this same backend in the next integration step.
