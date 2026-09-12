# POSNext integration compatibility

ERPNext Payment Hub v0.6.5 supports multiple POSNext source bases through version-specific frontend patches.

## POSNext v2.0.0 — e0a52c5

Use:

`pos_next_payment_hub_v0.6.5_posnext_v2.0.0_e0a52c5.patch`

Expected clean source:

```bash
cd ~/frappe-bench/apps/pos_next
git status --short
git rev-parse --short HEAD
git describe --tags --always
```

Expected commit/tag:

```text
e0a52c5
v2.0.0
```

Apply only after the safety check succeeds:

```bash
git apply --check pos_next_payment_hub_v0.6.5_posnext_v2.0.0_e0a52c5.patch
git apply pos_next_payment_hub_v0.6.5_posnext_v2.0.0_e0a52c5.patch
```

The v2.0.0 patch changes only:

- `POS/src/components/invoices/InvoiceDetailDialog.vue`
- `POS/src/components/pos/POSHeader.vue`
- `POS/src/components/sale/PaymentDialog.vue`
- `POS/src/components/sale/PaymentHubPendingDialog.vue` (new)
- `POS/src/components/sale/ReturnInvoiceDialog.vue`
- `POS/src/pages/POSSale.vue`

It does not modify `POS/components.d.ts` or `pos_next/fixtures/custom_docperm.json`.

After applying:

```bash
cd ~/frappe-bench/apps/pos_next/POS
npm run build
npm run copy-html-entry

cd ~/frappe-bench
bench --site erp.bm-kw.com clear-cache
bench restart
```

## Legacy POSNext — fbf8e80

Use the legacy patch only for a checkout matching its older base:

`pos_next_payment_hub_legacy_v0.6.4_fbf8e80.patch`

Do not apply the legacy patch to v2.0.0, and never apply both patches to the same POSNext checkout.

## Validation performed for the v2.0.0 patch

- generated against clean POSNext v2.0.0 / `e0a52c5`
- `git diff --check` passed
- JavaScript in all changed Vue script blocks passed `node --check`
- patch `git apply --check` passed against a second untouched clean v2.0.0 copy
- applied output hashes matched the adapted source for all six files

A full Vite build was not run in the packaging environment because the clean git archive does not include `node_modules`; run the build commands above on the ERPNext/POSNext server after applying.
