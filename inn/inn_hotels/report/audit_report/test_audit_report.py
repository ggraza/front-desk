# -*- coding: utf-8 -*-
# Copyright (c) 2025, PMM and Contributors
# See license.txt
"""Tests for the Audit Report's row shaping and money aggregation.

These cover the regressions that made the report disagree with the finance
spreadsheet, so they are deliberately DB-free: the classification rules are
passed in rather than read from Inn Hotels Setting.
"""

from __future__ import unicode_literals

import unittest
from unittest import mock

import frappe

from inn.helper.revenue_lines import (
    LINE_BREAKFAST,
    LINE_COMMISSION,
    LINE_OTHER,
    LINE_REFUND,
    LINE_ROOM,
)
from inn.inn_hotels.report.audit_report import audit_report

LINE_MAP = {
    "Room Charge": LINE_ROOM,
    "Room Charge Tax/Service": LINE_ROOM,
    "Breakfast Charge": LINE_BREAKFAST,
    "Commision": LINE_COMMISSION,
    "Refund": LINE_REFUND,
    "Restaurant Food": LINE_OTHER,
}

ON_DATE = "2025-11-05"


def trx(transaction_type, flag, amount, mode=None, rate=None, creation=None):
    return frappe._dict(
        parent="F-1",
        transaction_type=transaction_type,
        flag=flag,
        amount=amount,
        mode_of_payment=mode,
        actual_room_rate=rate,
        creation=creation,
    )


class TestAuditReportColumns(unittest.TestCase):
    def test_column_count_and_audit_date_appended(self):
        columns = audit_report.get_columns()
        self.assertEqual(len(columns), 16)
        self.assertEqual(columns[-1]["fieldname"], "audit_date")
        for column in columns:
            self.assertTrue(column.get("label"), column)

    def test_original_column_order_preserved(self):
        """The finance team's paste depends on these positions."""
        fieldnames = [column["fieldname"] for column in audit_report.get_columns()]
        self.assertEqual(
            fieldnames[:15],
            [
                "rsv",
                "customer",
                "room_type",
                "actual_room",
                "actual_room_rate",
                "actual_room_nett",
                "bf_revenue",
                "comission",
                "payment_by",
                "status",
                "mode_of_payment",
                "total_amount",
                "posting_date",
                "paid_date",
                "remark",
            ],
        )


class TestAuditReportAggregation(unittest.TestCase):
    def test_every_credit_counts_as_collection(self):
        """Regression: Down Payment / Payment / Deposit used to be dropped."""
        detail, _ = audit_report.summarise_transactions(
            [
                trx("Room Payment", "Credit", 100, mode="Cash"),
                trx("Down Payment", "Credit", 200, mode="BCA TRANSFER"),
                trx("Payment", "Credit", 300, mode="BCA EDC"),
                trx("Deposit", "Credit", 400, mode="Voucher"),
            ],
            LINE_MAP,
        )
        self.assertEqual(detail["F-1"]["total_amount"], 1000)

    def test_debits_are_not_collections(self):
        detail, _ = audit_report.summarise_transactions(
            [
                trx("Room Charge", "Debit", 500),
                trx("Refund", "Debit", 50),
                trx("Room Payment", "Credit", 500, mode="Cash"),
            ],
            LINE_MAP,
        )
        self.assertEqual(detail["F-1"]["total_amount"], 500)
        self.assertEqual(detail["F-1"]["actual_room_nett"], 500)

    def test_refund_is_not_room_revenue(self):
        detail, _ = audit_report.summarise_transactions([trx("Refund", "Debit", 75)], LINE_MAP)
        self.assertEqual(detail["F-1"]["actual_room_nett"], 0)
        self.assertEqual(detail["F-1"]["breakfast_revenue"], 0)

    def test_mode_of_payment_is_clean_and_deduplicated(self):
        """Regression: the old code emitted 'BY BCA EDC 550.000, ' with [:-2]."""
        detail, _ = audit_report.summarise_transactions(
            [
                trx("Room Payment", "Credit", 100, mode="BCA EDC"),
                trx("Room Payment", "Credit", 100, mode="BCA EDC"),
                trx("Payment", "Credit", 100, mode="Cash"),
            ],
            LINE_MAP,
        )
        self.assertEqual(detail["F-1"]["modes"], {"BCA EDC", "Cash"})

    def test_tax_service_lines_are_room_and_breakfast_revenue(self):
        detail, _ = audit_report.summarise_transactions(
            [
                trx("Room Charge Tax/Service", "Debit", 121),
                trx("Breakfast Charge", "Debit", 80),
            ],
            LINE_MAP,
        )
        self.assertEqual(detail["F-1"]["actual_room_nett"], 121)
        self.assertEqual(detail["F-1"]["breakfast_revenue"], 80)

    def test_actual_room_rate_is_taken_from_the_room_charge(self):
        detail, _ = audit_report.summarise_transactions(
            [
                trx("Room Charge", "Debit", 500, rate=550000),
                trx("Room Charge", "Debit", 500, rate=None),
            ],
            LINE_MAP,
        )
        self.assertEqual(detail["F-1"]["actual_room_rate"], 550000)

    def test_commission_is_collected_separately(self):
        detail, _ = audit_report.summarise_transactions(
            [trx("Commision", "Debit", 90)], LINE_MAP
        )
        self.assertEqual(detail["F-1"]["comission"], 90)
        self.assertEqual(detail["F-1"]["total_amount"], 0)

    def test_unmapped_type_is_reported(self):
        _, unmapped = audit_report.summarise_transactions(
            [trx("Brand New Charge", "Debit", 10)], LINE_MAP
        )
        self.assertEqual(unmapped, ["Brand New Charge"])

    def test_payment_types_are_not_reported_as_unmapped(self):
        """Regression: credits have no revenue line by design.

        Classifying them produced a warning on every run naming Room Payment,
        Down Payment, Payment and Deposit as unmapped.
        """
        _, unmapped = audit_report.summarise_transactions(
            [
                trx("Room Payment", "Credit", 100, mode="Cash"),
                trx("Down Payment", "Credit", 100, mode="Cash"),
                trx("Payment", "Credit", 100, mode="Cash"),
                trx("Deposit", "Credit", 100, mode="Cash"),
                trx("Room Charge", "Debit", 400),
            ],
            LINE_MAP,
        )
        self.assertEqual(unmapped, [])

    def test_collections_still_counted_when_classification_skipped(self):
        """Credits bypass classification, so the credit branch must still run."""
        detail, _ = audit_report.summarise_transactions(
            [trx("Room Payment", "Credit", 250, mode="BCA EDC")], LINE_MAP
        )
        self.assertEqual(detail["F-1"]["total_amount"], 250)
        self.assertEqual(detail["F-1"]["modes"], {"BCA EDC"})


class TestAuditReportRowShaping(unittest.TestCase):
    def setUp(self):
        self.detail = audit_report.empty_detail()
        self.detail["total_amount"] = 150
        self.detail["modes"] = {"Cash"}
        self.detail["actual_room_nett"] = 500
        self.detail["actual_room_rate"] = 550000

    def test_row_matches_column_count(self):
        row = audit_report.build_row(
            frappe._dict(
                name="RSV-1",
                customer_id="CUST-1",
                room_type="Deluxe",
                actual_room_id="R-101",
                channel="Walk In",
                bill_instructions="note",
            ),
            self.detail,
            audit_report.STATUS_PAID,
            1,
            ON_DATE,
        )
        self.assertEqual(len(row), len(audit_report.get_columns()))

    def test_audit_date_and_paid_date_are_populated(self):
        import datetime

        self.detail["creations"] = [
            datetime.datetime(2025, 11, 6, 0, 30),
            datetime.datetime(2025, 11, 6, 1, 45),
        ]
        row = audit_report.build_row(
            frappe._dict(name="RSV-1", customer_id="C", room_type="", actual_room_id="",
                         channel="", bill_instructions=None),
            self.detail, audit_report.STATUS_PAID, 1, ON_DATE,
        )
        self.assertEqual(row[15], ON_DATE)
        self.assertEqual(row[13], "2025-11-06")
        # posting_date keeps the original postings, oldest first
        self.assertTrue(row[12].startswith("2025-11-06 00:30"))

    def test_mode_suppressed_when_filter_off(self):
        row = audit_report.build_row(
            frappe._dict(name="RSV-1", customer_id="C", room_type="", actual_room_id="",
                         channel="", bill_instructions=None),
            self.detail, audit_report.STATUS_PAID, 0, ON_DATE,
        )
        self.assertEqual(row[10], "")

    def test_orphan_folio_row_keeps_contract_and_names_the_folio(self):
        row = audit_report.build_orphan_row(
            frappe._dict(name="F-16280", type="Desk", customer_id=None, channel=None,
                         bill_instructions="misc"),
            self.detail, audit_report.STATUS_PAID, 1, ON_DATE,
        )
        self.assertEqual(len(row), len(audit_report.get_columns()))
        self.assertEqual(row[0], "F-16280")
        self.assertIn("Desk folio F-16280", row[14])
        self.assertIn("misc", row[14])

    def test_reservation_with_no_folio_yields_zero_row(self):
        """A reservation with no folio and blank status: zeros, not an error."""
        row = audit_report.build_row(
            frappe._dict(name="RSV-NOFOLIO", customer_id="CUST-1", room_type="Deluxe",
                         actual_room_id="R-101", channel="Walk In", bill_instructions=None),
            audit_report.empty_detail(), "", 1, ON_DATE,
        )
        self.assertEqual(len(row), len(audit_report.get_columns()))
        self.assertEqual(row[0], "RSV-NOFOLIO")
        for index in (4, 5, 6, 7, 11):
            self.assertEqual(row[index], 0, "column %d should be zero" % index)
        self.assertEqual(row[9], "")
        self.assertEqual(row[10], "")
        self.assertEqual(row[12], "")
        self.assertEqual(row[13], "")
        self.assertEqual(row[15], ON_DATE)


class TestAuditReportWithoutFolios(unittest.TestCase):
    """The branch where reservations exist but none of them has a folio.

    No such reservation existed on the dev bench for November 2025, so this
    path is exercised with mocks rather than assumed to work.
    """

    def test_reservations_without_any_folio_still_produce_rows(self):
        reservations = [
            frappe._dict(name="RSV-NOFOLIO-A", status="In House", customer_id="CUST-1",
                         room_type="Deluxe", actual_room_id="R-101", channel="Walk In",
                         actual_room_rate=550000, folio=None, bill_instructions=None),
            frappe._dict(name="RSV-NOFOLIO-B", status="Finish", customer_id="CUST-2",
                         room_type="Superior", actual_room_id="R-202", channel="Traveloka",
                         actual_room_rate=410000, folio=None, bill_instructions=None),
        ]

        with mock.patch.object(audit_report, "get_reservations", return_value=reservations), \
                mock.patch.object(audit_report, "get_folios_without_reservation", return_value=[]):
            rows = audit_report.get_data_detail(ON_DATE, 1)

        self.assertEqual(len(rows), len(reservations))
        for row, reservation in zip(rows, reservations):
            self.assertEqual(len(row), len(audit_report.get_columns()))
            self.assertEqual(row[0], reservation.name)
            self.assertEqual(row[11], 0)
            self.assertEqual(row[9], "")
            self.assertEqual(row[15], ON_DATE)

    def test_no_folios_and_no_reservations_returns_nothing(self):
        with mock.patch.object(audit_report, "get_reservations", return_value=[]), \
                mock.patch.object(audit_report, "get_folios_without_reservation", return_value=[]):
            self.assertEqual(audit_report.get_data_detail(ON_DATE, 1), [])

    def test_orphan_folio_alone_is_still_reported(self):
        """A Desk folio with activity but no reservation must not be lost."""
        orphan = frappe._dict(name="F-16280", type="Desk", customer_id=None,
                              channel=None, bill_instructions="misc")

        detail = audit_report.empty_detail()
        detail["total_amount"] = 912500
        detail["modes"] = {"Cash"}

        with mock.patch.object(audit_report, "get_reservations", return_value=[]), \
                mock.patch.object(audit_report, "get_folios_without_reservation", return_value=[orphan]), \
                mock.patch.object(audit_report, "get_transactions_for_date", return_value=[]), \
                mock.patch.object(audit_report, "summarise_transactions", return_value=({"F-16280": detail}, [])), \
                mock.patch.object(audit_report, "get_settlement_status", return_value={"F-16280": audit_report.STATUS_UNPAID}):
            rows = audit_report.get_data_detail(ON_DATE, 1)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "F-16280")
        self.assertEqual(rows[0][11], 912500)
        self.assertEqual(rows[0][9], audit_report.STATUS_UNPAID)
