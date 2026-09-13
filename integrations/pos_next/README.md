# POSNext integration compatibility

ERPNext Payment Hub v0.6.6 supports POSNext v2.0.0 / `e0a52c5` with mapping-aware payment routing.

## Current POSNext v2.0.0 — e0a52c5

For a **clean** POSNext v2.0.0 checkout, use:

`pos_next_payment_hub_v0.6.6_posnext_v2.0.0_e0a52c5.patch`

For a POSNext checkout that already has the Payment Hub **v0.6.5** adapter applied, use the smaller incremental patch:

`pos_next_payment_hub_v0.6.6_from_v0.6.5.patch`

Do not apply both. Always run `git apply --check` first.

### v0.6.6 routing behavior

The adapter reads `payment_method_mappings` from `erpnext_payment_hub.pos.api.get_pos_payment_config`. This means any ERPNext Mode of Payment name can be mapped to an internal Payment Hub channel:

- `Tap Payment` → Electronic Payment → Tap provider account
- `UPayment` → Electronic Payment → UPayments provider account
- `TAP Terminal` → Physical Payment Terminal → Tap terminal/provider mapping
- `UPay Terminal` → Physical Payment Terminal → UPayments terminal/provider mapping
- custom Cash modes → Cash

Electronic and physical-terminal mappings are resolved by the backend; the visible ERPNext Mode of Payment name is never used as a provider-name guess.

> Physical-terminal charging requires the selected provider class to implement its SmartPOS/ECR `create_terminal_payment` adapter. The routing/mapping is included in v0.6.6, but provider-specific terminal protocols still require the merchant/provider terminal API specification.

### Expected source check

```bash
cd ~/frappe-bench/apps/pos_next
git status --short
git rev-parse --short HEAD
git describe --tags --always
```

Clean v2.0.0 expected base:

```text
e0a52c5
v2.0.0
```

### Build after applying

```bash
cd ~/frappe-bench/apps/pos_next/POS
npm run build
npm run copy-html-entry

cd ~/frappe-bench
bench --site erp.bm-kw.com clear-cache
bench restart
```

## Legacy POSNext — fbf8e80

`pos_next_payment_hub_legacy_v0.6.4_fbf8e80.patch` is retained only for the older source base. Never apply it to v2.0.0.

## v0.6.7 adapters

For POSNext v2.0.0 (`e0a52c5`), v0.6.7 adds multi-provider split tender, custom mapped Electronic Payment link behavior, and cash-only overpayment/change handling.

- Clean v2.0.0 checkout: `pos_next_payment_hub_v0.6.7_posnext_v2.0.0_e0a52c5.patch`
- Existing Payment Hub v0.6.6 POSNext adapter: `pos_next_payment_hub_v0.6.7_from_v0.6.6.patch`

Do not apply both patches.

## v0.6.8 adapter

The v0.6.8 POSNext incremental adapter adds `Manual / Non-Cash` mapping awareness so cheque/bank Modes of Payment can participate safely in a mixed Payment Hub checkout together with electronic links or physical terminals. Cash remains the only tender allowed to generate change.

Files:
- Existing Payment Hub v0.6.7 POSNext adapter: `pos_next_payment_hub_v0.6.8_from_v0.6.7.patch`
- Clean POSNext v2.0.0 (`e0a52c5`): `pos_next_payment_hub_v0.6.8_posnext_v2.0.0_e0a52c5.patch`
