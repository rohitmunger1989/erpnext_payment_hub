# ERPNext Payment Hub

**Provider-agnostic payment orchestration for ERPNext and POSNext**

[![Version](https://img.shields.io/badge/version-v0.6.8-blue.svg)](https://github.com/rohitmunger1989/erpnext_payment_hub/releases/tag/v0.6.8)
[![Frappe](https://img.shields.io/badge/Frappe-15%20%7C%2016-5e64ff.svg)](https://frappeframework.com/)
[![ERPNext](https://img.shields.io/badge/ERPNext-15%20%7C%2016-0089ff.svg)](https://erpnext.com/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

ERPNext Payment Hub provides a common payment layer for ERPNext with provider routing, POSNext payment sessions, asynchronous payment links, split tender, refund controls, transaction history, reconciliation reports, and audit-safe payment records.

The app is designed so ERPNext and POSNext work with **Payment Hub channels** instead of hardcoding a provider name into the POS flow.

---

## Current Release

**ERPNext Payment Hub v0.6.8**

- Git tag: `v0.6.8`
- Stable baseline commit: `bf5dbc3`
- POSNext tested source: `v2.0.0` / commit `e0a52c5`
- Python: `>= 3.10`
- Frappe: `>= 15, < 17`
- ERPNext: `>= 15, < 17`

> POSNext's Git source is tagged `v2.0.0` at `e0a52c5`. Depending on the POSNext package metadata, `bench version` may still display `pos_next 1.17.0 HEAD`.

---

## Highlights

### Provider-agnostic payment routing

Payment Hub can route any ERPNext **Mode of Payment** through a configurable mapping.

Supported internal channels:

- `Cash`
- `Manual / Non-Cash`
- `Electronic Payment`
- `Physical Payment Terminal`

Example mappings:

| ERPNext Mode of Payment | Payment Hub Channel | Provider Account | Terminal |
| --- | --- | --- | --- |
| Cash | Cash | — | — |
| Cheque | Manual / Non-Cash | — | — |
| Bank Transfer | Manual / Non-Cash | — | — |
| Tap Payment | Electronic Payment | BM Tap Live | — |
| MyFatoorah | Electronic Payment | BM MyFatoorah | — |
| UPayment | Electronic Payment | BM UPayments | — |
| TAP Terminal | Physical Payment Terminal | BM Tap Terminal | TAP POS 01 |

Mappings may be global or POS Profile-specific. A POS Profile-specific mapping takes priority over a global mapping.

---

## Supported Payment Providers

Included hosted/online payment adapters:

- **Tap Payments**
- **MyFatoorah**
- **UPayments**

Provider account states:

- `Test`
- `Active`
- `Refund Only`
- `Disabled`

A provider can therefore be disabled for new sales while remaining available for refunds of historical transactions.

### Physical terminal note

Payment Hub includes routing and terminal mapping support, but **physical SmartPOS / ECR charging requires a real provider-specific terminal API implementation**.

The included hosted-payment adapters do not invent or emulate a terminal protocol.

---

## POSNext Integration

v0.6.8 includes a dedicated POSNext integration for **POSNext v2.0.0 / `e0a52c5`**.

The integration provides:

- Payment Hub **Waiting / Paid / Failed** queues
- Current Shift → Last Shift → Today scope fallback
- overnight shift / business-date handling
- configurable pending-sale recovery
- recovery visibility across shift changes
- POS Profile isolation for normal cashiers
- All POS Profiles access for permitted manager/admin roles
- original cashier and original shift visibility
- **Previous Shift** indication
- Receipt / Thermal draft printing
- A4 draft printing
- configurable print formats
- asynchronous electronic payment
- WhatsApp payment-link delivery
- transaction history
- daily transaction reports
- Payment Hub-managed return/refund handling
- backend invoice ownership and refund protection

### POSNext patch files

For a **clean POSNext v2.0.0 / `e0a52c5` checkout**:

```text
integrations/pos_next/pos_next_payment_hub_v0.6.8_posnext_v2.0.0_e0a52c5.patch
```

For POSNext that already has the **v0.6.7 Payment Hub adapter**:

```text
integrations/pos_next/pos_next_payment_hub_v0.6.8_from_v0.6.7.patch
```

**Do not apply both patches.**

Always run `git apply --check` before applying a patch.

---

## POS Payment Session Model

Payment Hub separates the **sale session** from individual payment attempts.

### POS Payment Session — PPS

One Payment Hub sale.

Stores information such as:

- customer
- POS Profile
- original POS opening shift
- business date
- currency
- grand total
- confirmed paid amount
- pending amount
- remaining amount
- draft payload
- recovery deadline
- linked invoice
- completion state

### POS Payment Allocation — PPA

One payment allocation/attempt inside the sale.

Examples:

- cash allocation
- manual/non-cash allocation
- Tap payment link
- MyFatoorah payment link
- physical terminal payment

A single PPS can contain multiple PPAs.

### Gateway Transaction — PGT

Provider-facing gateway audit record.

Stores provider references, status, response data, payment URL, captured/refunded state, and related payment metadata.

---

## Split Tender and Cash Change

Payment Hub supports mixed payment methods in one POS sale.

Example:

```text
Sale total:             KWD 5.000

Tap Payment:            KWD 1.000
TAP Terminal:           KWD 1.000
Bank Transfer:          KWD 1.000
Cash tendered:          KWD 5.000

Cash applied:           KWD 2.000
Change:                 KWD 3.000
```

Rules:

- non-cash methods may be partial
- combined non-cash payment may not exceed the sale total
- non-cash methods never generate change
- **Cash is the only tender allowed to exceed the remaining balance**
- only the cash amount actually applied to the invoice is posted to the invoice
- excess cash is retained as `Change Amount`
- the session records cash tendered and change separately

POSNext's built-in **Customer Credit** is an accounting feature and should not be treated as a captured manual payment unless it is represented as a normal ERPNext Mode of Payment.

---

## Payment Links and Recovery

Payment Hub deliberately separates:

1. **POS sale recovery lifetime**
2. **individual provider payment-link lifetime**

These are not the same thing.

### Pending sale recovery

Configure:

```text
Payment Hub Settings
→ Pending Sale Retention (Hours)
```

Default:

```text
24 hours
```

This controls how long an incomplete Payment Hub POS sale may remain recoverable.

### Payment-link lifetime

Configure the global fallback:

```text
Payment Hub Settings
→ Fallback Payment Link Lifetime (Minutes)
```

Default for new installs:

```text
1440 minutes
```

A **Payment Provider Account** may override the fallback.

Expiry priority is:

```text
Provider-reported expiry
        ↓
Provider Account fallback
        ↓
Payment Hub Settings fallback
```

If Tap returns its own `transaction.expiry`, that provider value is authoritative for that attempt.

### Resend safety

Before resending a previously sent electronic payment link, Payment Hub refreshes provider status.

A final or captured attempt is not supposed to be blindly resent.

Examples of final states include:

- Captured
- Failed
- Abandoned
- Cancelled
- Expired

A retry creates a new payment attempt while keeping the original PPS sale session for audit continuity.

---

## WhatsApp Payment Links

Electronic payment links can be sent through WhatsApp.

Supported configuration fields include:

- WhatsApp Integration
- WhatsApp Account
- Payment Link WhatsApp Template
- Custom WhatsApp Sender Method
- Payment Link Message Template
- Save Electronic Payment and Serve Next Customer
- After Electronic Payment Capture

Default message template:

```text
Payment request {session}
Amount: {currency} {amount}
Please complete your payment using the secure link below:
{payment_url}
```

The electronic-payment flow can be asynchronous so the cashier may serve the next customer while the payment remains in the Payment Hub Waiting queue.

---

## Printing

Payment Hub supports configurable POS draft/final print formats.

Available settings include:

- Default POS Print Format
- Draft A4 Print Format
- Draft Receipt Print Format
- Final Receipt Print Format
- Default Draft Print Type

Supported draft-print use cases include:

- Receipt / Thermal
- A4
- Ask Each Time

---

## Refund and Return Security

Refunds are tied to the **original payment source**.

Payment Hub records the provider/account used for the original payment and uses that source for the refund even if the current default provider has changed.

Security controls include:

- Force Original Provider for Refund
- partial refunds
- manager authorization for electronic refunds
- manager authorization for physical-terminal refunds
- optional manager authorization for cash refunds
- authorized refund-method override
- short-lived refund authorization
- audited override flow
- server-side Sales Invoice return validation
- invoice ownership protection
- pending provider refund handling
- Check Refund continuation

Relevant settings:

```text
Require Manager Authorization for Cash Refund
Require Manager Authorization for Electronic Refund
Require Manager Authorization for Physical Terminal Refund
Allow Authorized Refund Method Override
Refund Authorization Validity (Minutes)
```

---

## Transaction History and Reports

Payment Hub includes POS-side history/reporting plus standard ERPNext Desk reports.

Included reports:

- Payment Hub Transaction History
- Payment Hub Daily Transactions
- Payment Hub Payment Method Summary
- Payment Hub Cashier / Branch Summary
- Payment Hub Provider Reconciliation
- Payment Hub Pending / Failed Transactions
- Payment Hub Refund Audit

Transaction reporting supports detailed payment/refund rows and export functionality.

---

## Scheduler and Reconciliation

Payment Hub registers low-rate reconciliation jobs every five minutes for:

- pending POS payment reconciliation
- stale POS payment expiry
- stale POS session expiry

Provider webhooks remain the primary notification path; the scheduler provides fallback reconciliation.

---

## Installation

### 1. Install Payment Hub

From your bench directory:

```bash
cd ~/frappe-bench

bench get-app https://github.com/rohitmunger1989/erpnext_payment_hub.git --branch main

bench --site YOUR_SITE install-app erpnext_payment_hub
bench --site YOUR_SITE migrate
bench --site YOUR_SITE clear-cache
bench restart
```

For an existing checkout:

```bash
cd ~/frappe-bench/apps/erpnext_payment_hub
git pull origin main

cd ~/frappe-bench
./env/bin/pip install -e apps/erpnext_payment_hub
bench --site YOUR_SITE migrate
bench --site YOUR_SITE clear-cache
bench restart
```

Verify:

```bash
bench version | grep erpnext_payment_hub
```

Expected release:

```text
erpnext_payment_hub 0.6.8
```

### 2. Integrate POSNext

Verify the POSNext source first:

```bash
cd ~/frappe-bench/apps/pos_next

git rev-parse --short HEAD
git describe --tags --exact-match 2>/dev/null || git describe --tags --always
git status --short
```

The v0.6.8 consolidated adapter targets:

```text
Commit: e0a52c5
Tag:    v2.0.0
```

For a clean v2.0.0 source:

```bash
git apply --check \
  ../erpnext_payment_hub/integrations/pos_next/pos_next_payment_hub_v0.6.8_posnext_v2.0.0_e0a52c5.patch
```

If the check succeeds:

```bash
git apply \
  ../erpnext_payment_hub/integrations/pos_next/pos_next_payment_hub_v0.6.8_posnext_v2.0.0_e0a52c5.patch
```

Build:

```bash
cd ~/frappe-bench/apps/pos_next/POS

npm install
npm run build
npm run copy-html-entry

cd ~/frappe-bench
bench --site YOUR_SITE clear-cache
bench restart
```

Then hard-refresh the browser:

```text
Ctrl + Shift + R
```

---

## Configuration

Open these DocTypes from ERPNext Desk:

1. **Payment Hub Settings**
2. **Payment Provider Account**
3. **Gateway Transaction**
4. **POS Payment Session**
5. **POS Payment Allocation**
6. **Payment Terminal**
7. **POS Station**

### Payment Hub Settings

Important areas include:

#### Provider defaults

- Default Provider
- Force Original Provider for Refund
- Allow Partial Refund
- Verify Webhooks with Status API
- Auto Reconcile

#### Payment Method Mappings

Map the ERPNext Mode of Payment to:

- Cash
- Manual / Non-Cash
- Electronic Payment
- Physical Payment Terminal

Mappings can optionally be scoped by:

- Company
- POS Profile

#### Payment recovery / WhatsApp / printing

These currently appear under the **Legacy / Fallback Defaults** section in v0.6.8, including:

- Fallback Payment Link Lifetime (Minutes)
- Pending Sale Retention (Hours)
- Auto Expire Stale Pending Sales
- Default POS Print Format
- Draft A4 Print Format
- Draft Receipt Print Format
- Final Receipt Print Format
- Default Draft Print Type
- WhatsApp Integration
- WhatsApp Account
- Payment Link WhatsApp Template
- Custom WhatsApp Sender Method
- Payment Link Message Template
- Save Electronic Payment and Serve Next Customer
- After Electronic Payment Capture

The section label is historical; these fields remain active configuration values.

---

## Provider Configuration

The installer can create disabled provider accounts for supported providers.

Configure a Payment Provider Account with the credentials supplied by the provider and set an appropriate state.

### Tap Payments

Typical fields:

- Secret Key
- Merchant ID
- Test Mode
- Status
- optional Base URL Override
- optional Fallback Payment Link Lifetime

### MyFatoorah

Typical fields:

- API Key / Token
- Test Mode
- Status
- optional Base URL Override

### UPayments

Typical fields:

- API Key / Token
- Test Mode
- Status
- optional Base URL Override

Use the provider's confirmed production API base when moving from sandbox to live mode.

---

## API Examples

### Create payment

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

### Refresh transaction

```python
from erpnext_payment_hub.api import refresh_transaction

refresh_transaction("PGT-2026-00001")
```

### Refund captured transaction

```python
from erpnext_payment_hub.api import refund_transaction

refund_transaction(
    transaction_name="PGT-2026-00001",
    amount=0.500,
    reason="Partial return",
)
```

The refund uses the provider/account stored against the original transaction.

---

## Tests

Run the app test suite:

```bash
bench --site YOUR_SITE run-tests --app erpnext_payment_hub
```

Before production use, test:

- provider sandbox payment creation
- success / failure / abandonment paths
- webhook/status reconciliation
- split tender
- payment-link expiry
- retry behavior
- partial and full refunds
- manager refund authorization
- receipt and A4 printing
- shift changes
- POS Profile access rules

---

## Known v0.6.8 Limitations

The following edge cases are known in the v0.6.8 baseline and should be considered before production rollout:

1. **Deleted draft invoice reference**
   - A recoverable PPS may still contain `invoice_name` after the linked draft Sales Invoice has been deleted.
   - Some backend paths, including stale-session expiry or payment-link retry, can then raise `LinkValidationError`.
   - The planned correction is to self-heal the stale invoice reference while preserving `draft_payload`.

2. **POSNext automatic draft cleanup**
   - POSNext's `clearCart()` currently calls `cleanup_old_drafts` with `max_age_hours: 1`.
   - The cleanup is not Payment-Hub-aware in the v0.6.8 baseline.
   - Recoverable Payment Hub drafts therefore need explicit protection in the next maintenance release.

3. **Provider remains INITIATED while checkout is unusable**
   - A customer-facing gateway page may become unusable while the provider status API still reports `INITIATED`.
   - The next maintenance release should add safe stale/expired-INITIATED handling before allowing a replacement payment attempt.

4. **Payment captured after original shift closes**
   - Waiting/recovery information survives shift changes, but cross-shift invoice completion needs a stricter completion-shift workflow.
   - The intended behavior is to preserve the original shift for audit and complete under the current open shift rather than reopening a closed shift.

5. **Physical terminal control**
   - Terminal routing exists, but provider-specific SmartPOS/ECR adapters are required for real terminal charging.

These limitations do not change the stable v0.6.8 tag; fixes should be introduced incrementally in a later release.

---

## v0.6.8 Release Notes

### Provider-aware payment-link expiry

v0.6.8 separates Payment Hub recovery time from individual provider checkout lifetime.

- configurable Payment Hub fallback expiry
- optional provider-account fallback expiry
- provider-returned expiry takes priority
- Tap `transaction.expiry` is read automatically
- resend performs a provider status refresh first
- final/captured attempts are not blindly resent

### Manual / Non-Cash channel

Adds `Manual / Non-Cash` for accepted non-gateway methods such as:

- Cheque
- Bank Transfer
- other normal ERPNext non-cash Modes of Payment

Manual/non-cash allocations cannot create change.

### Payment-attempt auditability

Tap retry attempts use the **POS Payment Allocation** as the provider transaction/order reference while retaining the parent **POS Payment Session** in metadata.

### v0.6.7 foundations retained

v0.6.8 retains:

- global and POS Profile-specific mappings
- custom electronic Mode of Payment names
- multi-provider split tender
- cash tender/applied/change separation
- cash-only overpayment/change rule

---

## Security Notes

- API keys and secrets belong in Payment Provider Account password fields.
- Do not expose provider secrets in frontend code.
- Verify provider payment status server-to-server.
- Keep refund authorization enabled for electronic/terminal refunds.
- Test all provider behavior in sandbox before production.
- Use HTTPS for the ERPNext site and webhook endpoints.
- Keep provider refund/reconciliation records for audit.
- Do not assume a browser redirect alone proves that a payment was captured.

---

## Repository

GitHub:

https://github.com/rohitmunger1989/erpnext_payment_hub

Stable release:

```text
v0.6.8
```

---

## License

MIT
