// Copyright (c) 2025, PMM and contributors
// For license information, please see license.txt

frappe.query_reports["Front Desk Daily Revenue Recap"] = {
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
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["Date", "Date + Channel"],
			default: "Date",
		},
		{
			fieldname: "include_zero_rows",
			label: __("Include Empty Days"),
			fieldtype: "Check",
			default: 1,
		},
	],

	onload: function (report) {
		// Default to the last business date that was closed, not today: night
		// audit runs after midnight, so "today" is usually not closed yet.
		frappe.call({
			method:
				"inn.inn_hotels.report.front_desk_daily_revenue_recap.front_desk_daily_revenue_recap.get_default_dates",
			callback: function (r) {
				if (!r.message || !r.message.from_date) {
					return;
				}
				report.set_filter_value("from_date", r.message.from_date);
				report.set_filter_value("to_date", r.message.to_date || r.message.from_date);
			},
		});
	},

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (column.fieldname === "business_date" && value) {
			value = `<span style="font-weight:600">${value}</span>`;
		}

		// Make the reconciliation line self-explanatory in the grid.
		if (column.fieldname === "difference" && data && data.difference) {
			const colour = data.difference > 0 ? "#b7791f" : "#2f855a";
			value = `<span style="color:${colour}">${value}</span>`;
		}

		return value;
	},
};
