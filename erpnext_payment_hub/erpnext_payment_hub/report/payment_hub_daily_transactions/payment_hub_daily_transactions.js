frappe.query_reports["Payment Hub Daily Transactions"] = {
  filters: [
    {
      fieldname: "from_date",
      label: __("From Date"),
      fieldtype: "Date",
      default: frappe.datetime.get_today(),
      reqd: 1,
    },
    {
      fieldname: "to_date",
      label: __("To Date"),
      fieldtype: "Date",
      default: frappe.datetime.get_today(),
      reqd: 1,
    },
    {
      fieldname: "pos_profile",
      label: __("POS Profile"),
      fieldtype: "Link",
      options: "POS Profile",
    },
    {
      fieldname: "cashier",
      label: __("Cashier"),
      fieldtype: "Link",
      options: "User",
    },
    {
      fieldname: "transaction_type",
      label: __("Type"),
      fieldtype: "Select",
      options: "\nPayment\nRefund",
    },
    {
      fieldname: "channel",
      label: __("Channel"),
      fieldtype: "Select",
      options: "\nCash\nPhysical Payment Terminal\nElectronic Payment\nOther",
    },
    {
      fieldname: "provider",
      label: __("Provider"),
      fieldtype: "Data",
    },
    {
      fieldname: "status",
      label: __("Status"),
      fieldtype: "Data",
    },
    {
      fieldname: "search",
      label: __("Search"),
      fieldtype: "Data",
      description: __("Invoice, customer, mobile, gateway transaction or refund ID"),
    },
  ],
}
