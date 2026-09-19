# ERPNext Payment Hub v0.6.13 Upgrade

Target: working Payment Hub **v0.6.12** + the live Payment Hub POSNext v0.6.12 adapter on POSNext v2.0.0 / `e0a52c5`.

## What changes

- Invoice Details shows **Check Refund**, **Retry Refund** and **Complete Return** when applicable.
- Manual Review / Pending refunds can be re-checked from Invoice Details.
- Only definitively `Failed` provider refunds are retryable.
- Retry preserves the old failed Gateway Transaction and keeps the same return/refund allocation audit trail.
- Provider refund identifiers are stored on POS Refund Allocation for reporting and WhatsApp templates.
- Refund WhatsApp confirmation is sent only after `Completed`.
- README / POSNext integration documentation is updated with the current release behavior.

## 1. Pre-check

Backend:

```bash
cd ~/frappe-bench/apps/erpnext_payment_hub
grep "__version__" erpnext_payment_hub/__init__.py
git apply --check erpnext_payment_hub_v0.6.13_from_live_v0.6.12.patch
echo "BACKEND CHECK=$?"
```

Expected source version before applying: `0.6.12` and `BACKEND CHECK=0`.

POSNext:

```bash
cd ~/frappe-bench/apps/pos_next
git apply --check pos_next_payment_hub_v0.6.13_from_v0.6.12_live.patch
echo "POS CHECK=$?"
```

Expected: `POS CHECK=0`.

## 2. Apply backend

```bash
cd ~/frappe-bench/apps/erpnext_payment_hub
git apply erpnext_payment_hub_v0.6.13_from_live_v0.6.12.patch

grep "__version__" erpnext_payment_hub/__init__.py
```

Expected: `__version__ = "0.6.13"`.

## 3. Apply POSNext

```bash
cd ~/frappe-bench/apps/pos_next
git apply pos_next_payment_hub_v0.6.13_from_v0.6.12_live.patch
```

## 4. Build / migrate

```bash
cd ~/frappe-bench/apps/pos_next/POS
npm run build
npm run copy-html-entry

cd ~/frappe-bench
./env/bin/pip install -e apps/erpnext_payment_hub
bench --site erp.bm-kw.com migrate
bench --site erp.bm-kw.com clear-cache
bench restart
```

Hard refresh POS with `Ctrl + Shift + R`.

## 5. Refund workflow after upgrade

Open a Payment Hub-managed return invoice from POS invoice history/details.

- `Pending` / `Processing` / `Manual Review` -> **Check Refund**
- `Failed` -> **Check Refund** and **Retry Refund**
- `Completed` + Draft return -> **Complete Return**
- submitted return -> read-only completed history

**Retry Refund** first verifies the previous failed provider attempt when the provider supports refund-status lookup. If the result is uncertain, Payment Hub returns to Manual Review instead of sending another blind refund.

## 6. WhatsApp template

Reference DocType: `POS Refund Allocation`.

Core field list:

```text
amount,mode_of_payment,actual_payment_method,provider,refund_gateway_transaction,source_gateway_transaction,original_invoice,return_invoice,status
```

Optional v0.6.13 provider detail fields:

```text
provider_refund_id,provider_reference,provider_auth_no,refund_provider_transaction_id,source_provider_transaction_id,source_provider_payment_id
```

If `amount` already renders like `9.500 KWD`, do not append a second currency placeholder in the approved template.

## 7. Test before GitHub tag

Test at least:

- completed Tap/MyFatoorah/UPayments refund
- failed refund -> Retry Refund -> new Gateway Transaction
- Pending/Manual Review -> Check Refund without duplicate refund
- refund becomes Completed after Check Refund
- Complete Return submits only after all refund allocations are Completed
- refund WhatsApp sent after Completed only
- customer without mobile completes refund without WhatsApp failure
- provider audit identifiers display when the provider supplied them

Do not move the existing `v0.6.11` tag. Push/tag v0.6.13 only after live testing passes.
