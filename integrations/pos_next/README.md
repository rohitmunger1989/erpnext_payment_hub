# POSNext Integration — Payment Hub v0.6.14

Target: **POSNext v2.0.0 / commit `e0a52c5`**.

## Clean POSNext checkout

Use:

```text
pos_next_payment_hub_v0.6.14_posnext_v2.0.0_e0a52c5.patch
```

This is the complete integration patch and includes the new `POS/src/components/sale/PaymentHubPendingDialog.vue` file.

## Existing v0.6.13 Payment Hub adapter

Use:

```text
pos_next_payment_hub_v0.6.14_from_v0.6.13_live.patch
```

This incremental patch adds the Copy Link UI/function and changes the Waiting queue text from `WhatsApp sent N time(s)` to `WhatsApp attempt N`.

If your live file already contains `Copy Link` and `copyPaymentLink`, do not re-apply the same hunk blindly. Compare first.

## Pre-check

```bash
cd ~/frappe-bench/apps/pos_next
git apply --check PATH_TO_PATCH
echo "CHECK=$?"
```

Apply only when the check is `0`.

## Build

```bash
cd ~/frappe-bench/apps/pos_next/POS
npm run build
npm run copy-html-entry

cd ~/frappe-bench
bench --site erp.bm-kw.com clear-cache
bench restart
```

Hard refresh POS with `Ctrl + Shift + R`.

## v0.6.14 UI behavior

- Waiting/Paid/Failed rows show **Copy Link** when `row.payment_url` exists.
- Copying a failed/expired historical URL does not make it valid again.
- Waiting queue shows the WhatsApp attempt count, timestamp and latest stored WhatsApp status.
- Check Status / Create New Link / Complete & Print continue to follow backend state flags.

## Older patches

Older integration patches are retained for history/rollback only. Do not stack multiple consolidated patches on the same POSNext checkout.
