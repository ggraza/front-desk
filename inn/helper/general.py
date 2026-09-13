from werkzeug.wrappers import Response
import json
import frappe

@frappe.whitelist()
def get_role():
    return frappe.get_roles(frappe.session.user)


@frappe.whitelist()
def get_default_company():
    company = frappe.db.get_single_value("Global Defaults", "default_company")
    data = {
        "data" : {
            "default_company": company
        }
    }
    response = Response()
    response.data = json.dumps(data)
    response.headers["Content-Type"] = "application/json"
    return response


@frappe.whitelist()
def get_default_company_doc():
    """Return the site's default Company as a full doctype (not just its name).

    Used by print/PDF headers (e.g. Report PNL) that need the Company's
    display title (company_name). Frappe auto-serializes the returned
    Document to JSON for the client.
    """
    company = frappe.db.get_single_value("Global Defaults", "default_company")
    return frappe.get_doc("Company", company)


@frappe.whitelist()
def get_print_header_settings():
    """Return the Print Template Settings fields from Inn Hotels Setting.

    Used by print/PDF headers (e.g. Report PNL) so the header text is not
    hardcoded per site. Missing/unset fields are returned as empty strings
    rather than None, so templates can render them directly without a
    None/"None" leaking into the output.
    """
    settings = frappe.db.get_value(
        "Inn Hotels Setting",
        None,
        ["address", "city", "phone", "fax", "email", "website_url"],
        as_dict=True,
    )
    return {key: (value or "") for key, value in settings.items()}
