# v0.6.14 Release Notes

v0.6.14 is a consolidation release built on v0.6.13.

## Included changes

1. **Copy Link queue action** — Waiting/Paid/Failed cards can copy the stored payment URL.
2. **Queue API fix** — `get_sales_queue()` now returns `payment_url` from the latest electronic PPA or PGT.
3. **UPayments/KNET duplicate reference fix** — new payment attempts use the PPA as the unique merchant attempt reference.
4. **WhatsApp wording** — queue text says `WhatsApp attempt N` and shows the webhook status separately.
5. **Packaging correction** — the clean POSNext v2.0.0 consolidated patch now includes the complete `PaymentHubPendingDialog.vue` file.
6. **Documentation refresh** — README, install guide, changelog and POSNext integration documentation now describe the actual current release.

## No intentional change to

- refund authorization/security model
- provider refund source locking
- print-format selection
- mapping priority
- reports
- permissions
- recovery-window rules
- provider-reported expiry priority
- captured-payment safety

## Important test focus

UPayments retries and new links should no longer reuse the PPS merchant reference. Confirm that separate attempts show separate `PPA-...` references provider-side.

A copied failed/expired URL remains historical; copying does not reactivate it.
