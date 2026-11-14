#!/usr/bin/env python3
"""
Prove that the database safeguards work.

Each test tries to do something that SHOULD be refused. A test passes when
the database rejects it. Every attempt is rolled back, so your data is never
changed. Run this AFTER seed.py:

    python check_constraints.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "po_guard.db"

# (description, SQL, parameters). Every one of these must be rejected.
BAD_OPERATIONS = [
    ("Foreign key: PO line pointing at a PO that doesn't exist",
     "INSERT INTO po_lines (po_id, line_number, description, quantity, unit_price_minor, unit_of_measure) "
     "VALUES (9999, 1, 'Ghost item', 1, 100, 'each')", ()),
    ("RESTRICT: delete a vendor that has purchase orders",
     "DELETE FROM vendors WHERE id = 1", ()),
    ("RESTRICT: delete a PO that has lines",
     "DELETE FROM purchase_orders WHERE po_number = 'PO-1001'", ()),
    ("CHECK: PO line quantity must be greater than 0",
     "INSERT INTO po_lines (po_id, line_number, description, quantity, unit_price_minor, unit_of_measure) "
     "VALUES (1, 99, 'Zero qty', 0, 100, 'each')", ()),
    ("CHECK: negative unit price",
     "INSERT INTO invoice_lines (invoice_id, po_line_id, quantity, unit_price_minor) "
     "VALUES (1, 1, 1, -5)", ()),
    ("CHECK: currency must be 3 uppercase letters",
     "INSERT INTO purchase_orders (po_number, vendor_id, order_date, currency) "
     "VALUES ('PO-BAD1', 1, '2026-11-14', 'inr')", ()),
    ("CHECK: impossible calendar date (30 February)",
     "INSERT INTO purchase_orders (po_number, vendor_id, order_date, currency) "
     "VALUES ('PO-BAD2', 1, '2026-02-30', 'INR')", ()),
    ("CHECK: invalid invoice status",
     "UPDATE invoices SET status = 'MAYBE' WHERE id = 1", ()),
    ("UNIQUE: same vendor reusing an invoice number",
     "INSERT INTO invoices (invoice_number, vendor_id, po_id, invoice_date, currency, total_minor) "
     "VALUES ('INV-88', 1, 1, '2026-11-14', 'INR', 100)", ()),
    ("UNIQUE: duplicate PO number",
     "INSERT INTO purchase_orders (po_number, vendor_id, order_date, currency) "
     "VALUES ('PO-1001', 1, '2026-11-14', 'INR')", ()),
    ("NOT NULL: invoice without a PO",
     "INSERT INTO invoices (invoice_number, vendor_id, po_id, invoice_date, currency, total_minor) "
     "VALUES ('INV-NOPO', 1, NULL, '2026-11-14', 'INR', 100)", ()),
    ("Trigger: audit_log rows cannot be updated",
     "UPDATE audit_log SET reason = 'tampered' WHERE id = 1", ()),
    ("Trigger: audit_log rows cannot be deleted",
     "DELETE FROM audit_log WHERE id = 1", ()),
]


def main() -> int:
    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH.name} not found. Run seed.py first.", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")  # required on every connection
    failures = 0

    try:
        for description, sql, params in BAD_OPERATIONS:
            try:
                conn.execute(sql, params)
            except sqlite3.IntegrityError as exc:
                print(f"PASS  {description}\n        refused with: {exc}")
            else:
                print(f"FAIL  {description}\n        the database ACCEPTED it!")
                failures += 1
            finally:
                conn.rollback()  # undo anything that slipped through
    finally:
        conn.close()

    total = len(BAD_OPERATIONS)
    print(f"\n{total - failures}/{total} safeguards behaved correctly.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())