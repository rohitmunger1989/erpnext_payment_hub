# ERPNext Payment Hub v0.6.14 — Full Setup

This package contains the complete Payment Hub backend source plus POSNext integration patches and documentation.

## Package targets

- Payment Hub: `v0.6.14`
- Frappe / ERPNext: `15` or `16`
- POSNext: `v2.0.0`, commit `e0a52c5`
- Site used in the examples: `erp.bm-kw.com`

## Important before changing the live server

Make a database backup and inspect Git state first:

```bash
cd ~/frappe-bench
bench --site erp.bm-kw.com backup --with-files

echo '=== PAYMENT HUB ==='
cd ~/frappe-bench/apps/erpnext_payment_hub
git status --short
grep '__version__' erpnext_payment_hub/__init__.py

echo '=== POSNEXT ==='
cd ~/frappe-bench/apps/pos_next
git status --short
git rev-parse --short HEAD
git describe --tags --exact-match 2>/dev/null || git describe --tags --always
```

Do not use `git reset --hard` on the live repositories unless you have intentionally backed up local changes.

---

# A. Upgrade an existing v0.6.13 installation

Use this path when Payment Hub is already installed and currently reports `0.6.13`.

## A1. Backend pre-check

Place this file in `~/frappe-bench/apps/erpnext_payment_hub/`:

```text
erpnext_payment_hub_v0.6.14_from_v0.6.13.patch
```

Then:

```bash
cd ~/frappe-bench/apps/erpnext_payment_hub

git apply --check erpnext_payment_hub_v0.6.14_from_v0.6.13.patch
echo "BACKEND CHECK=$?"
```

Expected for an untouched v0.6.13 source:

```text
BACKEND CHECK=0
```

If it is not `0`, stop. The live source may already contain one or more v0.6.14 hotfixes. Do not force-apply the patch.

## A2. POSNext pre-check

First check whether Copy Link is already present:

```bash
grep -n 'Copy Link\|copyPaymentLink\|WhatsApp attempt' \
  ~/frappe-bench/apps/pos_next/POS/src/components/sale/PaymentHubPendingDialog.vue
```

If `Copy Link` and `copyPaymentLink` are already present, **do not apply the incremental POS patch just to get Copy Link**. The v0.6.14 final UI also changes the status text from `WhatsApp sent N time(s)` to `WhatsApp attempt N`; that one-line wording may be applied later after comparing the file.

For an untouched v0.6.13 Payment Hub POS adapter, place:

```text
pos_next_payment_hub_v0.6.14_from_v0.6.13_live.patch
```

in `~/frappe-bench/apps/pos_next/`, then:

```bash
cd ~/frappe-bench/apps/pos_next

git apply --check pos_next_payment_hub_v0.6.14_from_v0.6.13_live.patch
echo "POS CHECK=$?"
```

Expected:

```text
POS CHECK=0
```

## A3. Apply only after checks pass

Backend:

```bash
cd ~/frappe-bench/apps/erpnext_payment_hub
git apply erpnext_payment_hub_v0.6.14_from_v0.6.13.patch
```

POSNext, only if its check passed and the patch is actually needed:

```bash
cd ~/frappe-bench/apps/pos_next
git apply pos_next_payment_hub_v0.6.14_from_v0.6.13_live.patch
```

## A4. Build / migrate / restart

If any POSNext source changed:

```bash
cd ~/frappe-bench/apps/pos_next/POS
npm run build
npm run copy-html-entry
```

Then:

```bash
cd ~/frappe-bench
./env/bin/pip install -e apps/erpnext_payment_hub
bench --site erp.bm-kw.com migrate
bench --site erp.bm-kw.com clear-cache
bench restart
```

Hard refresh POS with `Ctrl + Shift + R`.

## A5. Verify

```bash
cd ~/frappe-bench

echo '=== PAYMENT HUB VERSION ==='
grep '__version__' apps/erpnext_payment_hub/erpnext_payment_hub/__init__.py

echo '=== COPY LINK API ==='
grep -n -A4 -B2 'row\["payment_url"\]' \
  apps/erpnext_payment_hub/erpnext_payment_hub/pos/api.py

echo '=== UPAYMENTS ATTEMPT REFERENCE ==='
grep -n -A16 -B3 'attempt_reference' \
  apps/erpnext_payment_hub/erpnext_payment_hub/providers/upayments.py

echo '=== POS COPY LINK ==='
grep -n 'Copy Link\|copyPaymentLink\|WhatsApp attempt' \
  apps/pos_next/POS/src/components/sale/PaymentHubPendingDialog.vue
```

Expected source version:

```text
__version__ = "0.6.14"
```

---

# B. Clean POSNext v2.0.0 integration

Use this only when POSNext is a clean `e0a52c5` checkout with no Payment Hub POS modifications.

Verify:

```bash
cd ~/frappe-bench/apps/pos_next
git status --short
git rev-parse --short HEAD
git describe --tags --exact-match 2>/dev/null || git describe --tags --always
```

Expected base:

```text
e0a52c5
v2.0.0
```

Check the consolidated patch:

```bash
git apply --check \
  ../erpnext_payment_hub/integrations/pos_next/pos_next_payment_hub_v0.6.14_posnext_v2.0.0_e0a52c5.patch

echo "POS CLEAN CHECK=$?"
```

If `0`, apply:

```bash
git apply \
  ../erpnext_payment_hub/integrations/pos_next/pos_next_payment_hub_v0.6.14_posnext_v2.0.0_e0a52c5.patch
```

Build:

```bash
cd ~/frappe-bench/apps/pos_next/POS
npm install
npm run build
npm run copy-html-entry

cd ~/frappe-bench
bench --site erp.bm-kw.com clear-cache
bench restart
```

The consolidated patch includes the complete Payment Hub POS integration, including the new `PaymentHubPendingDialog.vue` file.

---

# C. Fresh Payment Hub installation

Recommended production method: put the v0.6.14 source in your Git repository, test it, then install/upgrade using Git/Bench.

For a site where Payment Hub is not installed yet:

```bash
cd ~/frappe-bench
bench get-app https://github.com/rohitmunger1989/erpnext_payment_hub.git --branch main
bench --site erp.bm-kw.com install-app erpnext_payment_hub
bench --site erp.bm-kw.com migrate
bench --site erp.bm-kw.com clear-cache
bench restart
```

After v0.6.14 is tagged on GitHub, pin deployments to the tested tag rather than an arbitrary later `main` commit.

The supplied `erpnext_payment_hub_v0.6.14_full.zip` is the complete source snapshot for backup/review/GitHub publication. It intentionally does not contain `.git` history.

---

# D. Recommended configuration

```text
Pending Sale Retention (Hours): 24
Fallback Payment Link Lifetime (Minutes): blank/0 unless a provider needs a fallback
Auto Expire Stale Pending Sales: enabled if desired
```

Provider expiry priority:

```text
provider expiry → provider-account fallback → global fallback → provider-native behavior
```

For WhatsApp, verify your connected WhatsApp Business Account is in good billing standing. Meta error `131042` means the WhatsApp Business account has unsettled payments; settle the Meta balance before retrying delivery.

---

# E. v0.6.14 live test checklist

Test before committing/tagging:

- Cash and Manual / Non-Cash
- Tap / MyFatoorah / UPayments new payment links
- UPayments retry/new link after a failed/expired attempt
- Copy Link in Waiting / Paid / Failed
- Waiting queue WhatsApp attempt/status display
- split tender and cash change
- stale/deleted draft self-heal
- shift closure while payment is pending
- capture after shift closure
- WhatsApp delivered/read/failed
- draft receipt/A4 and final receipt printing
- completed refunds
- Check Refund / Retry Refund / Complete Return
- customer without mobile during refund completion
- POS Profile isolation
- reports/reconciliation

Do not tag/push v0.6.14 until these tests pass.
