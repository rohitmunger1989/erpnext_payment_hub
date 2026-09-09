frappe.query_reports["Payment Hub Transaction History"] = {
  filters: [
    { fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -30) },
    { fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today() },
    { fieldname: "pos_profile", label: __("POS Profile"), fieldtype: "Link", options: "POS Profile" },
    { fieldname: "cashier", label: __("Cashier"), fieldtype: "Link", options: "User" },
    { fieldname: "provider", label: __("Provider"), fieldtype: "Data" },
    { fieldname: "search", label: __("Search"), fieldtype: "Data", description: __("Invoice, customer, mobile, gateway transaction or refund ID") },
  ],
};
