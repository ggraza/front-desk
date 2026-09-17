// Copyright (c) 2016, Core Initiative and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["Report PNL"] = {
	"filters": [
		{
            fieldname: 'date',
            label: __('Date'),
			fieldtype: 'Date'
		},
		{
            fieldname: 'fiscal_year',
            label: __('Fiscal Year'),
			fieldtype: 'Link',
			options: 'Fiscal Year'
		},
		// Hidden fields carrying the print/PDF header values, populated in
		// onload() from Inn Hotels Setting + the default Company. They are
		// never shown to the user; the report template reads them via
		// `filters.<name>`. See report_pnl.html.
		{
			fieldname: 'company_title',
			label: __('Company Title'),
			fieldtype: 'Data',
			hidden: 1
		},
		{
			fieldname: 'company_address',
			label: __('Company Address'),
			fieldtype: 'Data',
			hidden: 1
		},
		{
			fieldname: 'company_city',
			label: __('Company City'),
			fieldtype: 'Data',
			hidden: 1
		},
		{
			fieldname: 'company_phone',
			label: __('Company Phone'),
			fieldtype: 'Data',
			hidden: 1
		},
		{
			fieldname: 'company_fax',
			label: __('Company Fax'),
			fieldtype: 'Data',
			hidden: 1
		},
		{
			fieldname: 'company_email',
			label: __('Company Email'),
			fieldtype: 'Data',
			hidden: 1
		},
	],
	"onload": function() {
		frappe.xcall('inn.helper.general.get_default_company_doc').then(function(company) {
			frappe.query_report.set_filter_value({
				company_title: company.company_name || ''
			});
		});

		frappe.xcall('inn.helper.general.get_print_header_settings').then(function(settings) {
			frappe.query_report.set_filter_value({
				company_address: settings.address || '',
				company_city: settings.city || '',
				company_phone: settings.phone || '',
				company_fax: settings.fax || '',
				company_email: settings.email || ''
			});
		});
	},
	"formatter": function(value, row, column, data, default_formatter) {
		if (column.fieldname=="account") {
			value = data.account;
			column.is_tree = true;
		}

		value = default_formatter(value, row, column, data);

		if (!data.parent_account) {
			value = $(`<span>${value}</span>`);

			var $value = $(value).css("font-weight", "bold");
			if (data.warn_if_negative && data[column.fieldname] < 0) {
				$value.addClass("text-danger");
			}

			value = $value.wrap("<p></p>").parent().html();
		}

		return value;
	},
	"tree": true,
	"name_field": "account",
	"parent_field": "parent_account",
	"initial_depth": 2,
}
