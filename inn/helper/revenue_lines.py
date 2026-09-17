# -*- coding: utf-8 -*-
# Copyright (c) 2025, PMM and contributors
# For license information, please see license.txt
"""Shared revenue / payment classification for Inn Hotels reports.

This module is the single source of truth for two things:

* which report line each ``Inn Folio Transaction Type`` feeds;
* which transactions count as money received.

Both ``Audit Report`` and ``Front Desk Daily Revenue Recap`` import it, so the
rules cannot drift apart again.

Background
----------
``Audit Report`` used to build its payment set from two ``Inn Hotels Setting``
fields - ``customer_payment_transaction_type`` and
``customer_room_payment_transaction_type`` - which are both configured as
``Room Payment``. Anything posted as ``Down Payment``, ``Payment`` or
``Deposit`` was therefore invisible to it. Measured on the Bandung development
bench for November 2025 that silently dropped 27,150,440 of 551,688,677
collections (~4.9%).

Collections are now detected from ``Inn Folio Transaction.flag = 'Credit'``
instead. In this app credits are money received and debits are charges and
refunds (see ``inn_folio.update_balance``), so the flag needs no configuration
and cannot go stale.

``Inn Folio Transaction Type.is_included`` is deliberately *not* used as a
filter: ``Room Charge``, ``Room Charge Tax/Service`` and
``Breakfast Charge Tax/Service`` are all flagged ``is_included = 0`` yet carry
hundreds of millions every month.
"""

import frappe

# --------------------------------------------------------------------------
# Report lines
# --------------------------------------------------------------------------
# Revenue lines. ``total_revenue`` is the sum of exactly these.
LINE_ROOM = "room"
LINE_BREAKFAST = "breakfast"
LINE_RESTAURANT = "restaurant"
LINE_ROOM_SERVICE = "room_service"
LINE_BANQUET = "banquet"
LINE_LAUNDRY = "laundry"
LINE_OTHER = "other"

# Non-revenue lines. Kept out of ``total_revenue``.
LINE_COMMISSION = "commission"
LINE_REFUND = "refund"

REVENUE_LINES = (
    LINE_ROOM,
    LINE_BREAKFAST,
    LINE_RESTAURANT,
    LINE_ROOM_SERVICE,
    LINE_BANQUET,
    LINE_LAUNDRY,
    LINE_OTHER,
)

#: ``Inn Folio Transaction Type`` name -> report line.
#:
#: Names are the defaults shipped by ``Inn Hotels Setting``'s generator buttons.
#: Any type present in the database but missing here lands in ``LINE_OTHER`` and
#: is surfaced by :func:`unmapped_types`, so a type added at one property can
#: never silently disappear from the numbers.
TRANSACTION_TYPE_LINES = {
    # room
    "Room Charge": LINE_ROOM,
    "Room Charge Tax/Service": LINE_ROOM,
    # breakfast
    "Breakfast Charge": LINE_BREAKFAST,
    "Breakfast Charge Tax/Service": LINE_BREAKFAST,
    # restaurant (POS-posted to the folio)
    "Restaurant Food": LINE_RESTAURANT,
    "Restaurant Beverages": LINE_RESTAURANT,
    "Restaurant Other": LINE_RESTAURANT,
    "FBS -- Service 10 %": LINE_RESTAURANT,
    "FBS -- Tax 11 %": LINE_RESTAURANT,
    "Round Off": LINE_RESTAURANT,
    # room service
    "Room Service Food": LINE_ROOM_SERVICE,
    "Room Service Beverage": LINE_ROOM_SERVICE,
    # banquet
    "Banquet": LINE_BANQUET,
    "Banquet Revenue": LINE_BANQUET,
    # laundry
    "Laundry": LINE_LAUNDRY,
    # everything else that is revenue
    "Additional Charge": LINE_OTHER,
    "Early Checkin": LINE_OTHER,
    "Late Checkout": LINE_OTHER,
    "Cancellation Fee": LINE_OTHER,
    "Package": LINE_OTHER,
    "Package Tax": LINE_OTHER,
    "Credit Card Administration Fee": LINE_OTHER,
    # not revenue
    "Commision": LINE_COMMISSION,
    "Refund": LINE_REFUND,
}

#: ``Inn Hotels Setting`` fieldname -> report line. These win over
#: :data:`TRANSACTION_TYPE_LINES` so a property that renamed its transaction
#: types is still classified correctly.
SETTING_FIELD_LINES = {
    "room_revenue_transaction_type": LINE_ROOM,
    "breakfast_revenue_transaction_type": LINE_BREAKFAST,
    "profit_sharing_transaction_type": LINE_COMMISSION,
}

#: Human labels for the lines, used by report column definitions.
LINE_LABELS = {
    LINE_ROOM: "Room",
    LINE_BREAKFAST: "Breakfast",
    LINE_RESTAURANT: "Restaurant",
    LINE_ROOM_SERVICE: "Room Service",
    LINE_BANQUET: "Banquet",
    LINE_LAUNDRY: "Laundry",
    LINE_OTHER: "Other",
    LINE_COMMISSION: "Commission",
    LINE_REFUND: "Refund",
}

#: ``Inn Hotels Setting`` fields the reports read. Resolved once per execute().
SETTING_FIELDS = (
    "room_revenue_transaction_type",
    "breakfast_revenue_transaction_type",
    "profit_sharing_transaction_type",
    "customer_payment_transaction_type",
    "customer_room_payment_transaction_type",
    "room_revenue_account",
    "breakfast_revenue_account",
    "profit_sharing_account",
    "guest_account_receiveable",
)


#: Transaction types that carry the tax and service charge element of a
#: revenue line. ``Front Desk Daily Revenue Recap`` splits nett from
#: tax+service using these.
TAX_SERVICE_TYPES = {
    "Room Charge Tax/Service",
    "Breakfast Charge Tax/Service",
}


def get_settings_context():
    """Return the Inn Hotels Setting fields the reports need, as a plain dict.

    Replaces the module-level globals ``Audit Report`` used to mutate through
    ``fill_setting_data()``. Missing values come back as ``None``.
    """
    values = (
        frappe.db.get_value("Inn Hotels Setting", None, list(SETTING_FIELDS), as_dict=True)
        or {}
    )
    return {field: values.get(field) for field in SETTING_FIELDS}


def get_line_map(context=None):
    """Return ``transaction_type -> report line`` for this site.

    Starts from :data:`TRANSACTION_TYPE_LINES`, then applies
    :data:`SETTING_FIELD_LINES` using the configured transaction types.
    """
    context = context if context is not None else get_settings_context()

    line_map = dict(TRANSACTION_TYPE_LINES)
    for field, line in SETTING_FIELD_LINES.items():
        configured = context.get(field)
        if configured:
            line_map[configured] = line
    return line_map


def get_tax_service_types(context=None):
    """Return the transaction types that carry a tax/service element.

    Starts from :data:`TAX_SERVICE_TYPES` and adds
    ``"<configured revenue type> Tax/Service"`` for each configured revenue
    type, so a property that renamed its transaction types still splits
    correctly.
    """
    context = context if context is not None else get_settings_context()

    types = set(TAX_SERVICE_TYPES)
    for field in ("room_revenue_transaction_type", "breakfast_revenue_transaction_type"):
        configured = context.get(field)
        if configured:
            types.add("{0} Tax/Service".format(configured))
    return types


def classify(transaction_type, line_map=None):
    """Map one transaction type to a report line.

    Unknown types return :data:`LINE_OTHER`; call :func:`unmapped_types` to find
    out which ones they were.
    """
    if not transaction_type:
        return LINE_OTHER
    line_map = line_map if line_map is not None else get_line_map()
    return line_map.get(transaction_type, LINE_OTHER)


def unmapped_types(transaction_types, line_map=None):
    """Return the subset of ``transaction_types`` that has no explicit mapping.

    Used to warn rather than silently absorb a newly introduced transaction
    type into ``Other``.
    """
    line_map = line_map if line_map is not None else get_line_map()
    return sorted({t for t in transaction_types if t and t not in line_map})


def get_credit_transaction_types():
    """Return every ``Inn Folio Transaction Type`` whose ``type`` is ``Credit``.

    Diagnostic helper: credits are detected by ``flag`` at query time, but a
    credit type outside this set would mean the type master and the
    transactions disagree.
    """
    return set(
        frappe.get_all(
            "Inn Folio Transaction Type",
            filters={"type": "Credit"},
            pluck="name",
        )
    )


def line_from_account(account, context=None):
    """Best-effort mapping of a GL account to a report line.

    Used by reports that aggregate over ``debit_account``/``credit_account``
    instead of transaction type. Returns ``None`` when the account is unknown.
    """
    context = context if context is not None else get_settings_context()
    for field, line in (
        ("room_revenue_account", LINE_ROOM),
        ("breakfast_revenue_account", LINE_BREAKFAST),
        ("profit_sharing_account", LINE_COMMISSION),
    ):
        if account and account == context.get(field):
            return line
    return None
