frappe.query_reports["Payment Hub Pending Failed Transactions"] = {
  filters: [
    { fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -7) },
    { fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today() },
    { fieldname: "pos_profile", label: __("POS Profile"), fieldtype: "Link", options: "POS Profile" },
    { fieldname: "search", label: __("Search"), fieldtype: "Data" },
    { fieldname: "status_group", label: __("Status Group"), fieldtype: "Select", options: "
Pending
Failed
All", default: "All" },
  ],
};
