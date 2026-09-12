# ERPNext Payment Hub — v0.6.5

Provider-agnostic payment gateway foundation for ERPNext.


## v0.6.5 — POSNext v2.0.0 compatibility

v0.6.5 keeps the Payment Hub backend/payment/refund behavior from v0.6.4 and adds a dedicated frontend integration for **POSNext v2.0.0 at commit `e0a52c5`**.

Supported POSNext integration bases included with this source:

| POSNext base | Integration file | Notes |
|---|---|---|
| v2.0.0 / `e0a52c5` | `integrations/pos_next/pos_next_payment_hub_v0.6.5_posnext_v2.0.0_e0a52c5.patch` | Current adapter |
| legacy / `fbf8e80` | `integrations/pos_next/pos_next_payment_hub_legacy_v0.6.4_fbf8e80.patch` | Previous consolidated adapter |

The v2.0.0 adapter preserves:

- Payment Hub Waiting / Paid / Failed queues
- Current Shift -> Last Shift -> Today scope fallback
- overnight shift/business-date behavior
- 24-hour Waiting recovery across shift changes
- POS Profile isolation for normal cashiers and All POS Profiles for permitted managers/admins
- transaction history and daily reports
- original cashier/shift plus Previous Shift indication
- Receipt/Thermal and A4 draft printing
- asynchronous Electronic Payment / WhatsApp payment links
- Payment Hub-managed return/refund source locking
- manager/admin refund authorization and audited Cash override
- pending provider refund + Check Refund continuation
- invoice ownership/refund protection enforced by the backend

The adapter intentionally does **not** modify `POS/components.d.ts` or `pos_next/fixtures/custom_docperm.json`.

See `integrations/pos_next/README.md` before applying a POSNext patch. Never apply both POSNext patches to the same checkout.

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

Still not included:

- Physical SmartPOS / ECR terminal control (the POSNext adapter blocks this mode until the terminal API is validated)
- Automatic standalone Payment Entry creation
- Automatic unattended POS Invoice submit after capture (v0.3.0 uses cashier **Complete & Print**)
- Refund status polling scheduler
- Reconciliation dashboard
- Provider-specific webhook signature validation

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


# v0.2.2 — Draft Finalization Safety

This release hardens `Complete & Print` before the POSNext frontend adapter is connected.

## Completion safety

- `complete_pos_session(..., submit=0)` now creates or updates a draft invoice **without** marking the POS Payment Session `Completed`.
- A fully paid session with a draft invoice remains `Ready to Complete`, keeps `finalized = 0`, and stores the draft invoice reference for recovery.
- A later `complete_pos_session(..., submit=1)` automatically reuses that same draft invoice instead of creating a duplicate.
- The session becomes `Completed` only after the final invoice has `docstatus = 1`.
- Gateway Transactions are relinked from the temporary POS Payment Session to the final invoice only after successful submission.
- v0.2.1 sessions that were incorrectly marked finalized while their invoice is still a draft are repaired automatically the next time `complete_pos_session()` is called.
- Print results now include `submitted: true/false` in addition to `docstatus`.

## Backend flow validated before this release

- Cash + electronic split payments.
- Tap sandbox KNET capture with allocation/session synchronization.
- Pending Sales Waiting / Paid / Failed queues.
- Approved `frappe_whatsapp` payment-request template sending and send tracking.
- Waiting -> payment capture -> `Ready to Complete`.
- Draft invoice creation, ERPNext payment-row attachment, and print URL generation.

POSNext and POS Awesome frontend adapters remain intentionally separate from the provider logic and will call this common backend.


# v0.2.1 — Pending Sales + Complete & Print Backend

This release builds the cashier-facing backend workflow on top of the tested v0.2.0 split-payment session engine. POSNext and POS Awesome still share the same backend; their frontend adapters are the next step.

## Pending Sales queues

- Waiting: sessions with an electronic allocation still waiting for capture.
- Paid / Ready to Complete: all money is captured and the sale can be finalized.
- Failed / Expired / Cancelled: recovery queue for unsuccessful or abandoned sessions.
- Queue counts for POS badges and search by session, customer, mobile, cart reference, or invoice.
- Manual `check_session_payments()` plus the existing five-minute gateway reconciliation fallback.
- Optional local expiry of stale electronic-payment allocations. A later provider capture can still recover an expired local allocation.

## Electronic Payment / WhatsApp

- `send_payment_link()` and `resend_payment_link()` now send through the configured WhatsApp adapter and record send count/time/message reference.
- Native support for the installed `frappe_whatsapp` app's `WhatsApp Message` DocType.
- Optional approved `WhatsApp Templates` name. This is recommended when initiating a conversation outside Meta's 24-hour customer-service window.
- The template reference document is `POS Payment Allocation`, so templates can use payment allocation fields and a dynamic URL can point at `payment_url`.
- Optional custom dotted sender method for installations that use another Meta/WhatsApp integration.

## Saved drafts

- `save_pos_draft()` records `draft_saved_at`.
- The cashier can send the payment link, leave the sale in the Waiting queue, and immediately serve the next customer.

## Complete & Print backend

`complete_pos_session()`:

1. Requires the session to be `Ready to Complete`.
2. Uses an existing draft invoice or a saved/passed JSON invoice payload.
3. Aggregates captured POS allocations by ERPNext Mode of Payment and attaches them to the invoice when the target DocType has a `payments` table.
4. Validates the invoice total against the POS Payment Session total.
5. Optionally submits the invoice.
6. Relinks Gateway Transactions from the temporary POS Payment Session to the final invoice.
7. Marks the session `Completed` exactly once.
8. Returns a Frappe print route and print-view URL for the POS adapter to print.

The backend does not directly control a cashier's local printer. POSNext/POS Awesome will open the returned print route after completion.

## Cancellation safety

- Captured allocations cannot be cancelled; they must be refunded through the original-provider refund flow.
- Pending allocations can be locally cancelled. If a remote payment link later captures, the provider webhook is authoritative and restores the allocation to Captured.
- A whole session cannot be cancelled after any money has been captured.

## New / expanded APIs

- `erpnext_payment_hub.pos.api.get_sales_queue`
- `erpnext_payment_hub.pos.api.get_sales_queue_counts`
- `erpnext_payment_hub.pos.api.send_payment_link`
- `erpnext_payment_hub.pos.api.resend_payment_link`
- `erpnext_payment_hub.pos.api.check_session_payments`
- `erpnext_payment_hub.pos.api.cancel_pos_payment`
- `erpnext_payment_hub.pos.api.cancel_pos_session`
- `erpnext_payment_hub.pos.api.complete_pos_session`
- `erpnext_payment_hub.pos.api.finalize_pos_session` remains as a backward-compatible alias.

## WhatsApp note

The configured `frappe_whatsapp` integration uses Meta WhatsApp Cloud API. Free-form outbound text is subject to Meta's customer-service window. For a cashier-initiated payment request, configure an approved payment-link template in Payment Hub Settings when required by Meta policy.


## v0.3.0 — POSNext asynchronous electronic payment adapter

This release adds the first production-shaped POS frontend adapter flow while
keeping provider logic inside Payment Hub.

### POSNext flow

- Cash-only sales continue through normal POSNext behavior.
- Cash + **Electronic Payment** split sales are handed to Payment Hub.
- The current POS cart is saved on a **POS Payment Session** before the cashier
  leaves the sale.
- Payment Hub creates the configured provider payment link (Tap, UPayments, or
  MyFatoorah), sends it through the configured WhatsApp integration, and places
  the sale in the **Waiting** queue.
- POSNext can clear the cart immediately so the cashier can serve the next
  customer.
- The queue exposes **Waiting**, **Paid / Ready to Complete**, and **Failed**
  states.
- Cashiers can manually check a waiting payment or resend its WhatsApp link.
- **Complete & Print** submits the saved Sales Invoice only after the session is
  fully captured and then prints the submitted invoice.

### Common adapter API

`erpnext_payment_hub.pos.api.begin_async_electronic_sale` creates the session,
saves the invoice payload, creates the electronic allocation, records an
optional cash split, and sends the WhatsApp payment request in one idempotent
frontend call.

The online provider still comes from **Payment Hub Settings**, so POSNext does
not contain Tap / MyFatoorah / UPayments-specific code.

### Safety

- Electronic Payment is online-only.
- The first POSNext adapter supports **Cash + Electronic Payment** splits.
- Other mixed payment modes are rejected when Electronic Payment is used until
  dedicated allocation adapters are added.
- Physical Payment Terminal remains blocked in the POSNext adapter until a
  terminal API is configured and validated; it must not be treated as captured
  merely because the cashier selected the mode.


## v0.3.1 — POSNext payment-mode routing fix

This maintenance release fixes the first POSNext adapter test where selecting
**Electronic Payment** could still fall through to POSNext's normal invoice
submission path instead of Payment Hub.

- POSNext now normalizes payment-mode names before routing them.
- The literal safety aliases `Electronic Payment`, `Physical Payment Terminal`,
  and `Cash` remain recognized even if the frontend configuration response is
  wrapped or contains harmless casing/whitespace differences.
- Payment Hub API responses are unwrapped defensively in the POSNext adapter and
  queue dialog.
- The WhatsApp mobile field now follows the same normalized Electronic Payment
  detection used by the submission handler.
- Cash-only POSNext sales remain unchanged.
- Physical Payment Terminal remains blocked until terminal integration is
  implemented and validated.


## v0.3.2 — concurrent gateway callback safety

This maintenance release hardens gateway status updates when a provider webhook,
browser return URL, manual **Check Payment**, or scheduled reconciliation reaches
the same transaction at nearly the same time.

- Retries `Gateway Transaction` saves after Frappe `TimestampMismatchError` by
  reloading the latest row and re-applying the verified provider status.
- Applies the same retry protection to POS Payment Allocation and POS Payment
  Session recalculation.
- Prevents late `Pending` or `Failed` responses from downgrading a payment that
  has already been confirmed `Captured` or `Refunded`.
- Keeps POS allocation/session synchronization idempotent when duplicate Tap
  return/webhook events arrive within milliseconds.
- No provider-specific behavior was added to POSNext.

## v0.4.0 — POSNext Return Sale + original-payment refund

This release adds Payment Hub-managed POS returns while preserving the normal
POSNext return flow for invoices that were not paid through Payment Hub.

### Return/refund flow

- A Payment Hub Sales Invoice is matched back to its finalized POS Payment Session.
- Return refunds are locked to the **original payment allocation**. Cashiers cannot
  switch an electronic payment to another gateway, cash, or a different terminal.
- Cash allocations are recorded as cash refunds.
- Electronic allocations call the **original Gateway Transaction / original provider
  account**, so changing the default provider later does not affect old refunds.
- Split payments remain split: each original allocation has its own refundable limit.
- Full and partial returns are supported up to the remaining refundable amount.
- A new `POS Refund Allocation` audit DocType links the original invoice, return
  draft, source allocation, source gateway transaction, refund gateway transaction,
  amount, provider and status.
- Refund reservations are persisted before external provider calls to reduce the
  risk of duplicate refunds from double-clicks or concurrent cashier requests.
- The return Sales Invoice is created as **Draft first**. It is submitted only after
  all Payment Hub refund allocations are confirmed `Completed`.
- Provider refunds that are still pending keep the return invoice in Draft and can
  be checked again with `refresh_return_refunds()`.
- Uncertain external errors are marked **Manual Review** instead of automatically
  repeating a possibly-successful provider refund.
- Refund gateway transactions are anchored to the Return Sales Invoice for
  idempotent recovery and audit.

### POSNext UI behavior

- Payment Hub returns show **Refund to Original Payment** instead of a free payment
  method selector.
- The original provider/payment method and maximum refundable amount are displayed.
- `Add to Customer Credit Balance` is disabled for Payment Hub-managed sales so the
  original-source refund rule is preserved.
- `Check Refund` is available when a provider refund is pending.
- Invoice Details no longer labels every return with a payment row as **Cash Refund**.
  Payment Hub returns show provider-aware refund details; legacy/non-Payment-Hub
  electronic returns show the neutral **Refund Recorded** label.

### APIs

- `erpnext_payment_hub.pos.refund.get_refund_plan`
- `erpnext_payment_hub.pos.refund.process_pos_return_refund`
- `erpnext_payment_hub.pos.refund.get_return_refund_status`
- `erpnext_payment_hub.pos.refund.refresh_return_refunds`

Physical Payment Terminal refunds remain blocked until the terminal refund adapter
is implemented and validated.


# v0.5.0 — Transaction History, Refund Authorization & Daily Report

This release adds cashier-facing transaction lookup, manager-controlled refund security,
and daily payment/refund reconciliation while keeping the original-provider refund rule.

## Transaction History

POSNext Payment Hub now includes a **Transaction History** view. Search by:

- customer mobile number or name
- Sales Invoice / Return Invoice
- POS Payment Session / allocation
- Gateway Transaction
- provider transaction, payment, tracking or refund ID

History shows submitted payments, Payment Hub gateway status, refunds, remaining refundable
amount and pending electronic-payment sessions. Non-Payment-Hub POS invoices are also shown
from ERPNext payment rows so the lookup is useful for normal Cash / legacy POS payments.

Backend API:

```text
erpnext_payment_hub.pos.reporting.search_transaction_history
```

## Refund authorization

New roles:

- **Payment Hub Refund Approver** — can authorize electronic / terminal refunds.
- **Payment Hub Refund Override** — can authorize a refund and change the refund method.
- **Payment Hub Auditor** — read-only audit/report role.

By default:

- Cash refunds do not require manager authorization.
- Electronic Payment refunds require manager/admin authorization.
- Physical Payment Terminal refunds require manager/admin authorization.
- An override requires the **Payment Hub Refund Override** role.

The POS cashier enters the manager's own ERPNext username/email and password in a secure
authorization dialog. Password verification happens server-side. Payment Hub **never stores
the plaintext password**.

Successful authorization creates a **Payment Hub Refund Authorization** audit record containing:

- cashier/requesting user
- authorized manager
- authorization time
- original and return invoice
- amount
- original payment sources
- override target and reason (when applicable)

Authorization is short-lived (default 5 minutes) and bound to the exact return draft / amount.

### Refund-method override

The default refund remains locked to the original payment source/provider. v0.5.0 allows an
audited manager override to the configured **Cash** Mode of Payment only. Cross-provider refunds
(e.g. Tap payment refunded through MyFatoorah) are intentionally not represented as gateway
refunds because a different provider cannot refund the original charge.

The original provider transaction remains in the audit record while the actual cash refund,
manager and override reason are stored separately.


## v0.5.1 security migration

v0.5.1 fixes an upgrade edge case for sites that already had **Payment Hub Settings** before
v0.5.0. Frappe can materialize newly-added Check fields as `0` on an existing Single DocType,
so the intended secure JSON defaults were not automatically applied on some upgraded sites.

The one-time v0.5.1 migration explicitly enables:

- manager authorization for **Electronic Payment** refunds;
- manager authorization for **Physical Payment Terminal** refunds;
- the audited refund-method override feature (still restricted by the **Payment Hub Refund Override** role);
- a 5-minute authorization lifetime when no valid value exists.

Cash refunds remain policy-configurable and default to no manager authorization. After this
one-time migration, administrators can change these options normally in **Payment Hub Settings**;
the patch is not re-run on every migrate.

## Daily Payment & Refund Report

New Desk Script Report:

```text
Payment Hub Daily Transactions
```

The report uses submitted ERPNext POS invoice payment rows as the complete daily source and
enriches Payment Hub-managed rows with gateway/refund metadata. This means normal Cash / legacy
POS payments are included as well as Payment Hub electronic transactions.

Filters include date range, POS Profile, cashier, transaction type, channel, provider, status and
free-text search.

Summary includes:

- total payments
- total refunds
- net collection
- payment/refund/net totals by channel
- payment/refund/net totals by provider

Backend API used by POSNext:

```text
erpnext_payment_hub.pos.reporting.get_daily_transaction_report
```

## POSNext UI

The Payment Hub dialog now contains:

1. **Sales Queues** — Waiting / Paid / Failed, automatic refresh, WhatsApp resend count/status.
2. **Transaction History** — customer/mobile/invoice/provider transaction/refund lookup.
3. **Daily Report** — current POS Profile payments, refunds, net and breakdowns.

Electronic and physical-terminal refund processing is blocked until manager/admin authorization
is successfully verified. Physical terminal provider refunds remain unavailable until the terminal
refund adapter is implemented; an authorized Cash override can be used when company policy allows.


## v0.5.2 return-security hotfix

v0.5.2 closes a second fail-open path found during POS testing: a legacy/non-tracked
Sales Invoice could contain **Electronic Payment** or **Physical Payment Terminal** accounting
rows without a linked Payment Hub session. POSNext then treated the return as a normal editable
return, allowing a cashier to change the refund to Cash without creating a Payment Hub refund
allocation or manager authorization.

### Fail-closed server validation

Payment Hub now registers a server-side `Sales Invoice.before_submit` guard. UI controls are no
longer the security boundary. For returns against an original Electronic/Physical payment:

- a Payment Hub-managed original must have completed `POS Refund Allocation` rows before the
  return invoice can submit;
- any source that requires approval must have a linked manager authorization;
- a legacy/untracked Electronic/Physical original cannot be represented as a provider refund;
- the only supported legacy override is the configured **Cash** Mode of Payment;
- that Cash override requires a password-verified user with **Payment Hub Refund Override**;
- a direct POSNext/ERPNext submit call without the authorization is rejected server-side.

Customer-credit returns with no payment rows remain allowed because no cash/card refund is
released.

### Legacy protected plan in POSNext

`get_refund_plan` now identifies legacy Electronic/Physical invoices even when no Payment Hub
session exists. POSNext receives a `legacy_protected` plan, locks the original payment, blocks
direct gateway/terminal refund, and exposes only the audited **Manager Override to Cash** path.
The manager password is still checked server-side and is never stored.


## v0.5.3 POS PDF / Excel exports

v0.5.3 adds direct export buttons to the POSNext Payment Hub dialog for both **Transaction History** and the **Daily Report**.

Supported exports:

- **Excel (.xlsx)** - complete transaction detail including invoice/session, customer/mobile, POS Profile, cashier, payment channel, mode, provider, payment method, signed amount, status, gateway/provider references, manager authorization, override audit data and remaining refundable amount.
- **PDF** - landscape A4 customer/accounts-friendly transaction report with the key payment/refund audit fields.
- **Daily Report Excel/PDF** additionally includes Payments, Refunds, Net Collection, channel summary and provider summary before the detail rows.

Exports use the same server-side Sales Invoice read permission check as the Payment Hub history/report APIs. Transaction History exports preserve the current search and POS Profile; Daily Report exports preserve the selected date range and POS Profile.

Backend download endpoint:

```text
erpnext_payment_hub.pos.reporting.download_transaction_export
```


## v0.6.0 — ERPNext Desk Workspace & Reporting

v0.6.0 adds a native Frappe/ERPNext v16 Desk experience for Payment Hub. The app now declares the standard `add_to_apps_screen` hook and a public **Payment Hub** workspace, so a fresh install/migrate can expose a Payment Hub icon on the Desk without manual workspace creation.

### Desk navigation

The Payment Hub workspace and curated v16 sidebar provide direct access to:

- Gateway Transactions
- POS Payment Sessions
- Payment Allocations
- Refund Allocations
- Refund Authorizations
- Payment Provider Accounts
- Payment Terminals
- POS Stations
- Payment Hub Settings

The app icon is permission-gated to Administrator and users with appropriate Accounts / Payment Hub roles.

### Standard Desk reports

The following standard Script Reports are included and installed with the app:

- **Payment Hub Daily Transactions**
- **Payment Hub Transaction History**
- **Payment Hub Refund Audit**
- **Payment Hub Provider Reconciliation**
- **Payment Hub Pending Failed Transactions**
- **Payment Hub Cashier Branch Summary**
- **Payment Hub Payment Method Summary**

These reports use the same Payment Hub transaction/refund audit data as POSNext. Frappe Desk report actions can be used for normal report export/print workflows, while the POSNext v0.5.3 dialog retains its dedicated PDF and Excel download buttons.

### Frappe Payments dependency

`erpnext_payment_hub` does **not** require the separate `payments` app. Tap Payments, MyFatoorah and UPayments continue to use Payment Hub's provider adapters and audit DocTypes. This keeps installation independent while leaving room for optional compatibility adapters later.


## v0.6.1 — Desktop Icon Upgrade Fix

v0.6.1 fixes existing-site upgrades where the **Payment Hub** workspace was installed but the Frappe v16 app-screen **Desktop Icon** row was missing. This can happen when Payment Hub was originally installed before the `add_to_apps_screen` hook was introduced.

Payment Hub now runs an idempotent desktop-icon reconciliation from both `after_install` and `after_migrate`. On Frappe v16 it calls Frappe's installed-app icon builder so the Payment Hub icon is created automatically from the app hook. On Frappe v15 the helper is unavailable and is safely skipped.

After upgrading, run:

```bash
bench --site YOUR_SITE migrate
bench --site YOUR_SITE clear-cache
bench restart
```

No manual Bench Console command is required. The icon remains permission-gated by `erpnext_payment_hub.permissions.check_app_permission`.


## v0.6.2 — Safer POS Electronic Payment Preflight & Recovery

v0.6.2 changes the asynchronous POS flow so Payment Hub **saves and validates the exact
Sales Invoice draft before creating an electronic gateway payment request**. The draft save
uses the site's normal Sales Invoice validation stack, including ERPNext and any installed
company/POS validation hooks. Payment Hub does not hardcode tax, negative-stock, selling-rate
or POS-shift policy; it follows the rules already enforced by the site.

New flow:

```text
POS cart
  -> create Payment Hub session
  -> save/validate Sales Invoice draft
  -> validation fails: stop, no gateway link, customer not charged
  -> validation succeeds: create gateway payment attempt and optionally send WhatsApp
  -> provider captures payment
  -> submit the same validated draft invoice
```

The preflight deliberately avoids a fake submit/rollback because submit hooks may have external
side effects. Submit-only accounting/stock validations can therefore still fail later; if money
has already been captured, Payment Hub preserves the native validation error in the session and
keeps the same draft recoverable for correction and retry.

### 24-hour sale recovery vs gateway-link expiry

A POS payment session is recoverable for **24 hours by default** (`Pending Sale Retention
(Hours)`). This is separate from the lifetime of an individual provider payment URL. Each
gateway attempt keeps its own expiry and provider limits still apply.

A failed/expired electronic attempt no longer destroys the whole sale. Within the recovery
window the cashier can create a **new gateway attempt** for the unpaid balance. Previous attempts
remain in Gateway Transaction / Payment Allocation history.

For duplicate-payment protection, Payment Hub fails closed when any previous provider attempt
still reports `Pending`; it will not create a second payable URL until the previous attempt is
confirmed Failed/Expired or Captured.

### POS queue recovery actions

The POSNext Payment Hub queue can now expose:

- **Create New Link** — creates a fresh gateway attempt and copies the URL where browser
  clipboard access is available.
- **New Link + WhatsApp** — creates a fresh attempt and sends that new URL through the configured
  WhatsApp integration.
- **Print Unpaid Draft** — prints the saved draft Sales Invoice for unpaid or partially-paid
  sessions.
- Native validation errors are retained on the Payment Hub session and surfaced in the queue.
- Failed sessions remain recoverable until the configured recovery deadline. Sessions with
  captured money are never auto-expired by the unpaid-session timer.

`resend_payment_link` now only resends a currently `Waiting` attempt. Failed/Expired URLs must
use the new-link flow so stale provider links are not presented to customers.


## v0.6.3 — POS Draft Ownership Safety Hotfix

v0.6.3 fixes a critical draft-reuse edge case discovered while testing v0.6.2.
A new POS cart could carry a stale Sales Invoice ``name`` in its payload and cause
a newer Payment Hub session to reuse an invoice already referenced by an older
session.  Payment Hub now owns invoice identity server-side:

- a new payment session always creates a fresh Sales Invoice draft and ignores
  stale ``name`` / document metadata supplied by a new-cart payload;
- once a session has an ``invoice_name``, retries continue to reuse only that
  session's explicit draft;
- before reusing a draft, Payment Hub rejects the operation if another
  ``POS Payment Session`` already claims the same invoice; and
- captured gateway transactions remain attached to their own payment session,
  preventing a later cart from hijacking a paid session's draft.

Existing duplicate references are not silently rewritten during migration.
They should be reviewed and repaired explicitly so a submitted invoice is never
reassigned automatically.

## v0.6.4 — POS Shift Scope, Branch Isolation and Draft Print Selection

v0.6.4 makes the POS Payment Hub queue/report experience follow normal retail shift behavior while keeping unresolved electronic payments recoverable across shift handover.

### POS shift and business-date scope

- New POS sessions store `POS Opening Shift` and `Business Date`.
- `Business Date` is the shift opening date, so an overnight shift such as 19:00 → 02:00 remains one reporting period after midnight.
- Normal POS users default to **Current Shift**.
- If there is no active shift, the dialog falls back to **Last Shift**; if no shift is available, it falls back to **Today**.
- Available period selectors are **Current Shift**, **Last Shift**, **Today**, **Yesterday**, and **Custom Date Range** when applicable.

### Waiting queue handover

- **Waiting** deliberately ignores the current-shift/date filter.
- It shows unresolved sessions for the selected/current POS Profile while they are inside the pending-sale recovery window.
- A payment created near shift close therefore remains visible to the next cashier on the same POS Profile.
- Rows expose original cashier, POS profile, opening shift and a **Previous Shift** indicator.

### POS Profile isolation

- Normal cashier UI is locked to the active/current POS Profile (for example KM or Jahra).
- Users with cross-profile reporting roles (`System Manager`, `Accounts Manager`, `Payment Hub Auditor`) can select another profile or **All POS Profiles**.
- When **All POS Profiles** is selected, the default period is **Today** because a single current shift cannot represent multiple profiles.

### Draft printing

Payment Hub Settings now includes:

- **Draft A4 Print Format**
- **Draft Receipt Print Format**
- **Final Receipt Print Format**
- **Default Draft Print Type** (`Receipt`, `A4`, `Ask Each Time`)

The POS queue provides a **Draft Print** selector for unpaid/failed recovery documents. Draft printing remains non-posting: it does not submit the Sales Invoice and does not change payment state.

The selected A4/receipt format is site-configurable rather than hardcoded. If the selected draft format is blank, Payment Hub falls back to **Default POS Print Format** and then `Standard`.

### Upgrade corrections

- Existing sites with `Pending Sale Retention (Hours) = 0` are corrected to **24 hours** during migrate.
- Existing Payment Hub sessions are backfilled with shift/business-date context when it can be recovered from the saved POS payload or linked invoice.
- v0.6.3 invoice-ownership protections remain in place; each Payment Hub session owns only its own Sales Invoice draft.
