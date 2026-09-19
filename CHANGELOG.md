# Changelog

All notable Payment Hub release changes are summarized here.

## v0.6.14 — 2026-09-19

### Added
- Copy Link action in Payment Hub Waiting/Paid/Failed queue records when a stored payment URL exists.
- Complete POSNext v2.0.0 (`e0a52c5`) consolidated integration patch including `PaymentHubPendingDialog.vue`.
- GitHub-ready installation, integration and release documentation.

### Fixed
- `get_sales_queue()` now returns the latest electronic `payment_url`, allowing the Copy Link UI to render.
- UPayments/KNET uses the PPA attempt reference for `order.id`, `order.reference` and `reference.id`, preventing duplicate merchant reference errors when a PPS gets a new link.
- Waiting queue wording now says `WhatsApp attempt N` rather than implying delivery before the provider webhook confirms it.

### Preserved
- v0.6.13 refund recovery/status/retry workflow.
- v0.6.11/v0.6.9 payment recovery, stale draft self-healing and cross-shift safety.
- v0.6.8 mapping/split tender/refund/printing/WhatsApp/reporting foundation.

## v0.6.13

- Added Check Refund / Retry Refund / Complete Return actions in POS invoice details.
- Added fail-closed refund retry behavior and provider refund status checks.
- Added provider refund audit identifiers to POS Refund Allocation.
- Refund WhatsApp confirmation is sent only after refund completion.

## v0.6.12

- Added refund retry foundations and refund WhatsApp configuration.
- Kept uncertain external refund results in Manual Review instead of blind duplicate retries.

## v0.6.11

- Added customer-friendly payment return status page.
- Prevented Create New Link actions on closed/expired sessions.
- Made global/provider fallback payment-link lifetime optional; blank/0 no longer forces 1440 minutes.
- Preserved recovery and stale draft fixes from the v0.6.9 maintenance line.

## v0.6.9 maintenance

- Added stale deleted-draft invoice self-healing.
- Protected recoverable Payment Hub drafts from POSNext cleanup.
- Improved cross-shift captured-payment recovery.
- Improved payment-attempt status/recovery handling.

## v0.6.8

- Stable rollback baseline.
- Provider-aware link expiry and fallback configuration.
- Manual / Non-Cash channel.
- Mapping-aware multi-provider split tender.
- Cash tender/applied/change separation.
- Original-provider refund controls, reporting, printing and WhatsApp integration.
