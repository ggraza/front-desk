# -*- coding: utf-8 -*-
# Copyright (c) 2025, PMM and contributors
# For license information, please see license.txt
"""Audit Report - the daily folio-level export.

This is the report the front desk runs once per business date and pastes into
the finance spreadsheet, so its column order is a de-facto interface: the 15
original columns keep their names and positions, and new information is either
written into columns that used to be emitted empty (``status``, ``paid_date``)
or appended at the end (``audit_date``).

What each row means
-------------------
One row per reservation that is in house on the report date, plus any
reservation that has non-void activity on that date. Money columns aggregate
only the transactions whose ``audit_date`` equals the report date.

Fixed relative to the previous implementation
---------------------------------------------
``audit_date``      every row is dateable; the report date is no longer implied
                    by the filter used at export time.
``total_amount``    now every non-void credit, so ``Down Payment``, ``Payment``
                    and ``Deposit`` collections are no longer dropped. The old
                    code used two Inn Hotels Setting fields that were both
                    ``Room Payment``.
``status``          was always empty; now Paid / Partial / Unpaid as at the
                    report date, reconstructed from ``audit_date <= date``.
``mode_of_payment`` was a sliced concatenation (``[:-2]``); now clean,
                    comma-joined Mode of Payment values.
``paid_date``       was always empty; now the earliest payment date.
``remark``          unchanged (``Inn Folio.bill_instructions``).

The payment classification rules live in ``inn.helper.revenue_lines`` so this
report and ``Front Desk Daily Revenue Recap`` cannot disagree.
"""

import frappe
from frappe.utils import flt, today

from inn.helper.revenue_lines import (
    LINE_BREAKFAST,
    LINE_COMMISSION,
    LINE_ROOM,
    classify,
    get_line_map,
    get_settings_context,
    unmapped_types,
)

FILTER_FIELD_DATE = "expected_arrival"
FILTER_FIELD_STATUS = "status"

STATUS_RESERVED = "Reserved"
STATUS_IN_HOUSE = "In House"
STATUS_FINISH = "Finish"

STATUS_PAID = "Paid"
STATUS_PARTIAL = "Partial"
STATUS_UNPAID = "Unpaid"

#: A folio whose debits exceed its credits by no more than this is treated as
#: settled. Rupiah rounding leaves sub-unit residue on some folios.
SETTLEMENT_TOLERANCE = 1.0


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {"fieldname": "rsv", "label": "RSV", "fieldtype": "Data", "width": 150},
        {"fieldname": "customer", "label": "Customer", "fieldtype": "Data", "width": 150},
        {"fieldname": "room_type", "label": "Room Type", "fieldtype": "Data", "width": 150},
        {"fieldname": "actual_room", "label": "Actual Room", "fieldtype": "Data", "width": 150},
        {"fieldname": "actual_room_rate", "label": "Actual Room Rate", "fieldtype": "Currency", "width": 150},
        {"fieldname": "actual_room_nett", "label": "Actual Room Nett", "fieldtype": "Currency", "width": 150},
        {"fieldname": "bf_revenue", "label": "BF Revenue", "fieldtype": "Currency", "width": 150},
        {"fieldname": "comission", "label": "Comission", "fieldtype": "Currency", "width": 150},
        {"fieldname": "payment_by", "label": "Payment By", "fieldtype": "Data", "width": 150},
        {"fieldname": "status", "label": "Status", "fieldtype": "Data", "width": 150},
        {"fieldname": "mode_of_payment", "label": "Mode of Payment", "fieldtype": "Data", "width": 150},
        {"fieldname": "total_amount", "label": "Total Amount", "fieldtype": "Currency", "width": 150},
        {"fieldname": "posting_date", "label": "Posting date", "fieldtype": "Date", "width": 150},
        {"fieldname": "paid_date", "label": "Paid Date", "fieldtype": "Date", "width": 150},
        {"fieldname": "remark", "label": "Remark", "fieldtype": "Data", "width": 150},
        # Appended, never inserted: keeps the finance-team paste aligned.
        {"fieldname": "audit_date", "label": "Audit Date", "fieldtype": "Date", "width": 110},
    ]


def get_data(filters):
    filters = filters or frappe._dict()

    on_date = filters.get("date") or today()

    show_mode_of_payment = filters.get("fill_mode_payment")
    if show_mode_of_payment is None:
        show_mode_of_payment = 1

    return get_data_detail(on_date, show_mode_of_payment)


def get_data_detail(on_date, is_show_mode_payment=True):
    context = get_settings_context()
    line_map = get_line_map(context)

    reservations = get_reservations(on_date)
    orphan_folios = get_folios_without_reservation(on_date)

    folio_names = sorted(
        {row.folio for row in reservations if row.folio}
        | {folio.name for folio in orphan_folios}
    )

    if not folio_names:
        return [
            build_row(row, empty_detail(), "", is_show_mode_payment, on_date)
            for row in reservations
        ]

    detail, unmapped = summarise_transactions(
        get_transactions_for_date(folio_names, on_date), line_map
    )
    warn_unmapped_types(unmapped)

    settlement = get_settlement_status(folio_names, on_date)

    return [build_row(row, detail.get(row.folio) or empty_detail(),
                      settlement.get(row.folio, ""), is_show_mode_payment, on_date)
            for row in reservations] + [
        build_orphan_row(folio, detail.get(folio.name) or empty_detail(),
                         settlement.get(folio.name, ""), is_show_mode_payment, on_date)
        for folio in orphan_folios
    ]


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def get_reservations(on_date):
    """Reservations in house on ``on_date``, plus any with activity that day.

    The original predicate (status plus expected dates) is preserved. The extra
    branch guarantees a folio that moved money on the date is never dropped -
    the spreadsheet these rows feed is a record of that day's postings.
    """
    active = get_reservations_with_activity(on_date)

    query = """
        select ir.name, ir.status, ir.customer_id, ir.room_type, ir.actual_room_id,
               ir.channel, ir.actual_room_rate, folio.name as folio,
               folio.bill_instructions
        from `tabInn Reservation` as ir
        left join `tabInn Folio` as folio on folio.reservation_id = ir.name
        where
            (ir.status = %s and ir.expected_arrival <= %s)
            or (ir.status = %s and ir.expected_arrival <= %s and ir.expected_departure > %s)
    """
    params = [
        STATUS_IN_HOUSE,
        on_date,
        STATUS_FINISH,
        on_date,
        on_date,
    ]

    if active:
        query += " or ir.name in ({0})".format(", ".join(["%s"] * len(active)))
        params.extend(active)

    query += " order by ir.name"

    return frappe.db.sql(query, tuple(params), as_dict=True)


def get_reservations_with_activity(on_date):
    rows = frappe.db.sql(
        """
        select distinct folio.reservation_id
        from `tabInn Folio Transaction` as trx
        inner join `tabInn Folio` as folio on folio.name = trx.parent
        where trx.audit_date = %s
          and trx.is_void = 0
          and folio.reservation_id is not null
          and folio.reservation_id != ''
        """,
        (on_date,),
    )
    return [row[0] for row in rows]


def get_folios_without_reservation(on_date):
    """Folios that moved money on ``on_date`` but belong to no reservation.

    ``Inn Folio.type`` is ``Guest``, ``Master`` or ``Desk``. Desk and Master
    folios carry no ``reservation_id``, so a reservation-driven report silently
    drops their money - 912,500 of one November on the Bandung bench. They are
    emitted as their own rows so the day's collections still tie out.
    """
    return frappe.db.sql(
        """
        select folio.name, folio.type, folio.customer_id, folio.channel,
               folio.bill_instructions
        from `tabInn Folio Transaction` as trx
        inner join `tabInn Folio` as folio on folio.name = trx.parent
        left join `tabInn Reservation` as res on res.name = folio.reservation_id
        where trx.audit_date = %s
          and trx.is_void = 0
          and res.name is null
        group by folio.name, folio.type, folio.customer_id, folio.channel,
                 folio.bill_instructions
        order by folio.name
        """,
        (on_date,),
        as_dict=True,
    )


def get_transactions_for_date(folio_names, on_date):
    placeholders = ", ".join(["%s"] * len(folio_names))
    return frappe.db.sql(
        """
        select parent, transaction_type, flag, amount, mode_of_payment,
               creation, actual_room_rate
        from `tabInn Folio Transaction`
        where parent in ({0})
          and audit_date = %s
          and is_void = 0
        order by creation, name
        """.format(placeholders),
        tuple(folio_names) + (on_date,),
        as_dict=True,
    )


def get_settlement_status(folio_names, on_date):
    """Paid / Partial / Unpaid per folio, as at ``on_date``.

    Reconstructed from the transactions up to and including ``on_date`` rather
    than read from ``Inn Folio.balance``, which only reflects the current state
    and would be wrong whenever the report is re-run for a past date.
    """
    placeholders = ", ".join(["%s"] * len(folio_names))
    rows = frappe.db.sql(
        """
        select parent,
               sum(case when flag = 'Debit' then amount else 0 end) as debit,
               sum(case when flag = 'Credit' then amount else 0 end) as credit
        from `tabInn Folio Transaction`
        where parent in ({0})
          and audit_date <= %s
          and is_void = 0
        group by parent
        """.format(placeholders),
        tuple(folio_names) + (on_date,),
        as_dict=True,
    )

    settlement = {}
    for row in rows:
        debit = flt(row.debit)
        credit = flt(row.credit)
        if debit - credit <= SETTLEMENT_TOLERANCE:
            settlement[row.parent] = STATUS_PAID
        elif credit > 0:
            settlement[row.parent] = STATUS_PARTIAL
        else:
            settlement[row.parent] = STATUS_UNPAID
    return settlement


# ---------------------------------------------------------------------------
# Row shaping
# ---------------------------------------------------------------------------
def empty_detail():
    return {
        "actual_room_rate": 0,
        "actual_room_nett": 0,
        "breakfast_revenue": 0,
        "comission": 0,
        "total_amount": 0,
        "modes": set(),
        "creations": [],
    }


def summarise_transactions(transactions, line_map):
    detail = {}
    charge_types = set()

    for trx in transactions:
        bucket = detail.setdefault(trx.parent, empty_detail())
        amount = flt(trx.amount)

        # Collections: every credit is money received (payment types are not
        # revenue lines and are deliberately not classified). Refunds are debits
        # in this app, so they are naturally excluded.
        if (trx.flag or "").strip().lower() == "credit":
            bucket["total_amount"] += amount
            if trx.mode_of_payment:
                bucket["modes"].add(trx.mode_of_payment)
            if trx.creation:
                bucket["creations"].append(trx.creation)
            continue

        charge_types.add(trx.transaction_type)
        line = classify(trx.transaction_type, line_map)

        if line == LINE_ROOM:
            bucket["actual_room_nett"] += amount
            if trx.actual_room_rate:
                bucket["actual_room_rate"] = flt(trx.actual_room_rate)
        elif line == LINE_BREAKFAST:
            bucket["breakfast_revenue"] += amount
        elif line == LINE_COMMISSION:
            bucket["comission"] += amount

    # Only charge types can be "unmapped"; a payment type has no revenue line by
    # design and must not be reported as a gap.
    unmapped = unmapped_types(charge_types, line_map)
    return detail, unmapped


def warn_unmapped_types(unmapped):
    if not unmapped:
        return
    frappe.msgprint(
        msg="Transaction types with no revenue mapping, counted as Other: {0}".format(
            ", ".join(unmapped)
        ),
        title="Unmapped transaction types",
        indicator="orange",
    )


def build_orphan_row(folio, detail, settlement_status, is_show_mode_payment, on_date):
    """Row for a Desk/Master folio that has no reservation.

    ``rsv`` carries the folio name so the row stays traceable, and the folio
    type is stated in the remark. Same 16 columns as every other row.
    """
    remark = "{0} folio {1}".format(folio.type or "Non-guest", folio.name)
    if folio.bill_instructions:
        remark = "{0}: {1}".format(remark, folio.bill_instructions)

    return build_row(
        frappe._dict(
            name=folio.name,
            customer_id=folio.customer_id,
            room_type="",
            actual_room_id="",
            channel=folio.channel,
            bill_instructions=remark,
        ),
        detail,
        settlement_status,
        is_show_mode_payment,
        on_date,
    )


def build_row(reservation, detail, settlement_status, is_show_mode_payment, on_date):
    creations = detail.get("creations") or []
    mode_of_payment = ", ".join(sorted(detail.get("modes") or [])) if is_show_mode_payment else ""

    return [
        reservation.name,
        reservation.customer_id,
        reservation.room_type,
        reservation.actual_room_id,
        detail.get("actual_room_rate", 0),
        detail.get("actual_room_nett", 0),
        detail.get("breakfast_revenue", 0),
        detail.get("comission", 0),
        reservation.channel,
        settlement_status,
        mode_of_payment,
        detail.get("total_amount", 0),
        ", ".join(str(creation) for creation in creations),
        creations[0].date().isoformat() if creations else "",
        reservation.bill_instructions,
        on_date,
    ]
