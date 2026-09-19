# ERPNext Payment Hub

**Provider-agnostic payment orchestration for ERPNext and POSNext**

![Version](https://img.shields.io/badge/version-v0.6.14-blue.svg)
![Frappe](https://img.shields.io/badge/Frappe-15%20%7C%2016-5e64ff.svg)
![ERPNext](https://img.shields.io/badge/ERPNext-15%20%7C%2016-0089ff.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

ERPNext Payment Hub provides one payment layer for ERPNext/POSNext with provider routing, POS payment sessions, asynchronous hosted-payment links, split tender, WhatsApp delivery, print flows, refunds, recovery, reconciliation and audit records.

The POS flow works with **Payment Hub channels** rather than hardcoding provider names into the cashier UI.

## Current release

**v0.6.14**

- Frappe: `>=15,<17`
- ERPNext: `>=15,<17`
- Python: `>=3.10`
- POSNext integration target: `v2.0.0` / commit `e0a52c5`
- Permanent rollback baseline retained separately: `v0.6.8`

> POSNext source can be tagged `v2.0.0` at commit `e0a52c5` while `bench version` still reports package metadata such as `pos_next 1.17.0 HEAD`.

## What is new in v0.6.14

v0.6.14 consolidates the tested v0.6.13 refund work and the post-v0.6.13 maintenance fixes into one GitHub-ready source package.

### Copy payment link

The Payment Hub **Waiting / Paid / Failed** queue can show **Copy Link** whenever the latest electronic allocation or Gateway Transaction has a stored `payment_url`.

- Waiting: copies the current stored link.
- Paid: copies the historical stored link for audit/support use.
- Failed: copies the historical stored link; copying it does not make an expired/failed link reusable.
- No stored URL: the button is hidden.

The backend queue now returns `payment_url` explicitly so the POS button can render reliably.

### UPayments duplicate merchant reference fix

UPayments/KNET payment attempts now use the **POS Payment Allocation (PPA)** as the merchant attempt reference instead of reusing the parent **POS Payment Session (PPS)** for every retry.

This prevents a new payment attempt from reusing the same merchant-side reference and addresses provider errors such as:

```text
IPAY01000305 - Duplicate Merchant Track Id
```

The PPS is still retained in the description/audit context.

### Clearer WhatsApp attempt wording

The Waiting queue now labels the counter as **WhatsApp attempt N** instead of implying that every attempt was successfully delivered. The actual WhatsApp status is still shown when available (`sent`, `delivered`, `read`, `failed`).

## Core design

### Payment Hub channels

| Channel | Purpose | Can create change? |
| --- | --- | --- |
| Cash | Normal cash tender | Yes |
| Manual / Non-Cash | Cheque, bank transfer, other accepted offline non-cash methods | No |
| Electronic Payment | Hosted/provider payment links | No |
| Physical Payment Terminal | SmartPOS/ECR terminal route | No |

Any ERPNext **Mode of Payment** can be mapped to a channel. Mappings may be global or POS Profile-specific; a POS Profile-specific mapping takes priority.

Example:

| ERPNext Mode of Payment | Channel | Provider Account |
| --- | --- | --- |
| Cash | Cash | — |
| Cheque | Manual / Non-Cash | — |
| Bank Transfer | Manual / Non-Cash | — |
| Tap Payment | Electronic Payment | Tap account |
| MyFatoorah | Electronic Payment | MyFatoorah account |
| UPayment | Electronic Payment | UPayments account |
| TAP Terminal | Physical Payment Terminal | terminal/provider mapping |

## Supported hosted providers

Included adapters:

- Tap Payments
- MyFatoorah
- UPayments

Provider account states include:

- Test
- Active
- Refund Only
- Disabled

`Refund Only` allows historical transactions to be refunded without allowing new sales through that account.

> Physical SmartPOS/ECR charging still requires a real provider-specific terminal implementation. Hosted checkout adapters must not be treated as terminal protocols.

## POSNext integration

The v0.6.14 integration targets **POSNext v2.0.0 / `e0a52c5`** and includes:

- Payment Hub Waiting / Paid / Failed queues
- Copy Link on queue records with a stored payment URL
- Current Shift → Last Shift → Today scope fallback
- overnight shift/business-date handling
- configurable pending-sale recovery
- recovery across shift closure
- POS Profile isolation for normal cashiers
- broader manager/admin visibility when authorized
- original cashier/original shift display
- Previous Shift indicator
- Receipt/Thermal and A4 draft printing
- configurable print formats
- asynchronous electronic payment
- WhatsApp payment-link delivery/status
- split tender
- transaction history and daily reports
- return/refund workflow
- Check Refund / Retry Refund / Complete Return
- backend invoice ownership/refund protection
- POS draft cleanup protection for recoverable Payment Hub sessions

### POSNext patch choices

For a **clean POSNext v2.0.0 / `e0a52c5` checkout**, use:

```text
integrations/pos_next/pos_next_payment_hub_v0.6.14_posnext_v2.0.0_e0a52c5.patch
```

For an existing **v0.6.13 Payment Hub POS integration without Copy Link**, use:

```text
integrations/pos_next/pos_next_payment_hub_v0.6.14_from_v0.6.13_live.patch
```

If your current `PaymentHubPendingDialog.vue` already contains both `Copy Link` and `copyPaymentLink`, skip the incremental POS patch.

**Never apply both POS patches. Always run `git apply --check` first.**

## Payment data model

### POS Payment Session (PPS)

One logical POS sale. It stores the customer, POS Profile, original opening shift, business date, totals, recovery deadline, draft payload, linked invoice and completion state.

### POS Payment Allocation (PPA)

One payment allocation/attempt inside a PPS. A PPS may have multiple PPAs, including multiple electronic attempts after abandonment/failure/expiry.

### Gateway Transaction (PGT)

Provider-facing audit record containing provider IDs, payment URL, local/provider status, response data and payment/refund metadata.

## Split tender and change

Payment Hub supports mixed payment methods in one sale.

```text
Sale total:       KWD 5.000
Electronic:       KWD 1.000
Terminal:         KWD 1.000
Bank Transfer:    KWD 1.000
Cash tendered:    KWD 5.000
Cash applied:     KWD 2.000
Change:           KWD 3.000
```

Rules:

- non-cash allocations may be partial
- non-cash cannot exceed the remaining invoice balance
- non-cash never creates change
- cash is the only tender that may exceed the remaining balance
- only the cash actually applied is posted to the invoice
- excess cash is recorded as change

ERPNext/POSNext Customer Credit is an accounting feature and is not treated as a captured manual payment unless represented by an actual Mode of Payment mapping.

## Payment links and recovery

Payment Hub intentionally separates **sale recovery lifetime** from **provider payment-link lifetime**.

### Pending sale retention

Configure:

```text
Payment Hub Settings → Pending Sale Retention (Hours)
```

Recommended/default new-install value:

```text
24
```

This controls how long an incomplete sale stays recoverable. Captured money is never auto-expired by this recovery timer.

### Payment-link expiry priority

Expiry resolution is:

```text
provider-reported expiry
        ↓
Payment Provider Account fallback (if > 0)
        ↓
Payment Hub Settings fallback (if > 0)
        ↓
no synthetic expiry; rely on provider-native behavior
```

`Fallback Payment Link Lifetime (Minutes)` is optional. Leave it blank/0 when you want provider-native behavior and the provider already controls expiry.

### Retry safety

Before resending or replacing a payment attempt, Payment Hub refreshes provider state where applicable. Final/captured attempts are not blindly reused.

A new link creates a new PPA while preserving the original PPS for audit continuity.

## Cross-shift recovery

A pending PPS can survive shift closure.

- The old closed shift is not reopened or modified.
- Original shift and business date remain in audit history.
- If payment captures and a compatible current shift is open, the sale can be completed under the current shift while preserving origin data.
- If no shift is open, the session remains/enters **Ready to Complete** until a cashier opens the required shift.
- Captured funds are not discarded because the original recovery window ended.

## WhatsApp payment links

Payment Hub can send hosted payment links through the connected `frappe_whatsapp` app.

Typical configuration:

- WhatsApp Integration
- WhatsApp Account
- Payment Link WhatsApp Template
- Custom WhatsApp Sender Method
- Payment Link Message Template

A WhatsApp `wamid...` means Meta accepted the API request; it does **not** by itself prove delivery. Final webhook status may become `sent`, `delivered`, `read` or `failed`.

### Meta billing failure example

If webhook logs show:

```text
131042 - Business eligibility payment issue
Message failed to send because your WhatsApp Business account has unsettled payments.
```

resolve the outstanding balance/payment method in Meta Business billing, then send a new message. This is a WhatsApp Business billing issue, not a Payment Hub payment failure.

## Printing

Configurable fields include:

- Default POS Print Format
- Draft A4 Print Format
- Draft Receipt Print Format
- Final Receipt Print Format
- Default Draft Print Type

Draft options include Receipt/Thermal, A4 and Ask Each Time.

## Refunds and returns

Refunds are tied to the original payment source. Payment Hub preserves the original provider/account and applies refund authorization rules before sending provider refunds.

Supported controls include:

- force original provider for refund
- partial refunds
- manager authorization for electronic refunds
- manager authorization for physical-terminal refunds
- optional manager authorization for cash refunds
- authorized refund-method override
- short-lived refund authorization
- audited override flow
- backend Sales Invoice return validation
- invoice ownership protection
- Pending/Processing/Manual Review handling
- Check Refund
- Retry Refund only after a definitive provider failure
- Complete Return only after required refund allocations are completed

v0.6.13+ also stores available provider refund identifiers on `POS Refund Allocation` for reporting and WhatsApp templates.

## Reports and reconciliation

Included reports/workflows cover:

- Payment Hub Transaction History
- Daily Transactions
- Payment Method Summary
- Cashier / Branch Summary
- Provider Reconciliation
- Pending / Failed Transactions
- Refund Audit

Scheduler reconciliation runs at low frequency as a fallback for provider webhooks and handles pending payment checks and stale-session/link maintenance.

## Installation

See **[INSTALL_v0.6.14.md](INSTALL_v0.6.14.md)** for both clean installation and upgrade steps.

### Backend app

After the repository is installed in `apps/erpnext_payment_hub`:

```bash
cd ~/frappe-bench
./env/bin/pip install -e apps/erpnext_payment_hub
bench --site YOUR_SITE install-app erpnext_payment_hub   # fresh site only
bench --site YOUR_SITE migrate
bench --site YOUR_SITE clear-cache
bench restart
```

For an already-installed site, do not run `install-app` again; use `migrate`, `clear-cache`, and restart after updating source.

### Verify version

```bash
cd ~/frappe-bench
grep '__version__' apps/erpnext_payment_hub/erpnext_payment_hub/__init__.py
bench version | grep -E 'erpnext_payment_hub|pos_next'
```

Expected Payment Hub source version:

```text
__version__ = "0.6.14"
```

## Configuration checklist

Open these DocTypes:

1. Payment Hub Settings
2. Payment Provider Account
3. Payment Method Mapping / settings mapping table
4. Payment Terminal
5. POS Station
6. POS Payment Session
7. POS Payment Allocation
8. Gateway Transaction

Recommended recovery defaults:

```text
Pending Sale Retention (Hours): 24
Fallback Payment Link Lifetime (Minutes): blank/0 unless needed
Auto Expire Stale Pending Sales: enabled if you want stale unpaid sessions closed automatically
```

Provider-reported expiry always wins over fallback values.

## Provider setup notes

### Tap

Configure Secret Key, Merchant ID, Test Mode/Status, and any account-specific fallback expiry. Tap's returned expiry is authoritative when present.

### MyFatoorah

Configure the API token/key, Test Mode/Status and Base URL if required by the merchant environment.

### UPayments

Configure API key/token, Test Mode/Status and Base URL. v0.6.14 uses the PPA payment attempt as the merchant-side attempt reference for new POS links.

## API examples

### Create a payment

```python
from erpnext_payment_hub.api import create_payment

result = create_payment(
    reference_doctype="Sales Invoice",
    reference_name="ACC-SINV-2026-00001",
    amount=1.000,
    payment_method="KNET",
    currency="KWD",
)
```

### Refresh a Gateway Transaction

```python
from erpnext_payment_hub.api import refresh_transaction

refresh_transaction("PGT-2026-00001")
```

### Refund a captured transaction

```python
from erpnext_payment_hub.api import refund_transaction

refund_transaction(
    transaction_name="PGT-2026-00001",
    amount=0.500,
    reason="Partial return",
)
```

## Test checklist before tagging/releasing

At minimum test:

- Cash sale
- Manual / Non-Cash sale
- Tap payment link
- MyFatoorah payment link
- UPayments payment link
- UPayments failed/expired attempt → Create New Link without duplicate merchant Track ID
- Copy Link in Waiting / Paid / Failed when a URL exists
- split tender
- provider success / failure / abandonment / expiry
- stale/deleted draft recovery
- shift close while payment is pending
- capture after old shift closes
- WhatsApp `sent/delivered/read/failed` states
- receipt/A4 printing
- completed refund
- Pending/Manual Review → Check Refund
- failed refund → Retry Refund
- Complete Return only after required refunds complete
- refund WhatsApp only after Completed
- POS Profile isolation
- reports/reconciliation

Do not move the permanent `v0.6.8` rollback tag. Create/push the `v0.6.14` tag only after live testing passes.

## Repository

GitHub: `https://github.com/rohitmunger1989/erpnext_payment_hub`

## License

MIT
