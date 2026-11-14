#!/usr/bin/env python3
"""
Create the PO Guard Match SQLite database and load realistic sample data.

Usage (run from the folder that contains this file and schema.sql):
    python seed.py             # create po_guard.db and seed it
    python seed.py --reset     # back up any existing database, then start fresh
    python seed.py --db other.db

Design notes:
  * Plain sqlite3 (standard library). SQLAlchemy belongs to the application
    layer later; for schema + seed data, raw SQL keeps the mechanics visible.
  * Foreign keys are enforced: PRAGMA foreign_keys = ON on the connection.
  * All seed data is inserted in ONE transaction: if anything fails, nothing
    is saved.
  * Money is INTEGER minor units (paise / cents). All names, tax IDs and
    numbers below are fictional.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "po_guard.db"
SCHEMA_FILE = HERE / "schema.sql"


class SeedError(Exception):
    """A problem we can explain to the user in plain language."""


# ---------------------------------------------------------------------------
# Connection and database preparation
# ---------------------------------------------------------------------------

def connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with foreign key enforcement switched on."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # SQLite ignores foreign keys unless this is run on EVERY connection.
    conn.execute("PRAGMA foreign_keys = ON")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        conn.close()
        raise SeedError(
            "Could not enable foreign key enforcement. "
            "Your SQLite build may not support foreign keys."
        )
    return conn


def prepare_database(db_path: Path, reset: bool) -> sqlite3.Connection:
    """Create a fresh database from schema.sql and return a connection."""
    if not SCHEMA_FILE.exists():
        raise SeedError(f"Schema file not found: {SCHEMA_FILE}")

    if db_path.exists():
        if not reset:
            raise SeedError(
                f"{db_path.name} already exists, so nothing was changed.\n"
                "  Re-run with --reset to back it up and start fresh."
            )
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = db_path.with_name(f"{db_path.name}.bak-{stamp}")
        shutil.move(str(db_path), str(backup))  # move, never delete
        print(f"Existing database backed up to: {backup.name}")

    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
    except sqlite3.Error as exc:
        conn.close()
        raise SeedError(f"Failed to create the schema from {SCHEMA_FILE.name}: {exc}") from exc
    return conn


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def minor(amount: str) -> int:
    """Convert a decimal money string like '200.00' to integer minor units."""
    return int(Decimal(amount) * 100)


def insert(conn: sqlite3.Connection, table: str, **values) -> int:
    """
    Insert one row and return its new id.

    Table and column names come from our own code (never from user input);
    the VALUES are always passed as bound parameters.
    """
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
    try:
        cursor = conn.execute(sql, tuple(values.values()))
    except sqlite3.IntegrityError as exc:
        raise SeedError(
            f"Insert into '{table}' was rejected by the database: {exc}\n"
            f"  Values: {values}"
        ) from exc
    return cursor.lastrowid


def add_po(conn, vendor_id, po_number, order_date, currency, lines):
    """Insert a PO and its lines. Returns (po_id, [po_line_id, ...])."""
    po_id = insert(conn, "purchase_orders", po_number=po_number, vendor_id=vendor_id,
                   order_date=order_date, currency=currency)
    line_ids = []
    for number, (description, qty, price_minor, uom) in enumerate(lines, start=1):
        line_ids.append(insert(conn, "po_lines", po_id=po_id, line_number=number,
                               description=description, quantity=qty,
                               unit_price_minor=price_minor, unit_of_measure=uom))
    return po_id, line_ids


def add_grn(conn, grn_number, po_id, received_date, receipts):
    """Insert a goods receipt. receipts = [(po_line_id, quantity_received), ...]."""
    grn_id = insert(conn, "grns", grn_number=grn_number, po_id=po_id,
                    received_date=received_date)
    for po_line_id, qty in receipts:
        insert(conn, "grn_lines", grn_id=grn_id, po_line_id=po_line_id,
               quantity_received=qty)
    return grn_id


def add_invoice(conn, invoice_number, vendor_id, po_id, invoice_date, currency,
                lines, status):
    """
    Insert an invoice and its lines. lines = [(po_line_id, qty, unit_price_minor), ...].
    The header total is the sum of the line amounts (this is the vendor's claimed total).
    """
    total_minor = sum(int(round(qty * price)) for _, qty, price in lines)
    invoice_id = insert(conn, "invoices", invoice_number=invoice_number,
                        vendor_id=vendor_id, po_id=po_id, invoice_date=invoice_date,
                        currency=currency, total_minor=total_minor, status=status)
    for po_line_id, qty, price in lines:
        insert(conn, "invoice_lines", invoice_id=invoice_id, po_line_id=po_line_id,
               quantity=qty, unit_price_minor=price)
    return invoice_id


def add_audit(conn, entity_type, entity_id, action, actor,
              old_status=None, new_status=None, reason=None):
    return insert(conn, "audit_log", entity_type=entity_type, entity_id=entity_id,
                  action=action, actor=actor, old_status=old_status,
                  new_status=new_status, reason=reason)


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

def seed(conn: sqlite3.Connection) -> None:
    """Insert all sample data inside a single transaction."""
    # `with conn:` commits if the block succeeds and rolls back if it raises.
    with conn:
        # --- Vendors (fictional) ------------------------------------------
        acme = insert(conn, "vendors", name="Acme Supplies Pvt Ltd", tax_id="29ABCDE1234F1Z5")
        bharat = insert(conn, "vendors", name="Bharat Office Mart", tax_id="33FGHIJ5678K1Z2")
        coastal = insert(conn, "vendors", name="Coastal Packaging Co", tax_id="27LMNOP9012Q1Z8")

        # --- PO-1001: Acme, INR. Two GRNs (partial delivery), invoice over-bills line 2.
        po1, (po1_bolts, po1_gloves) = add_po(
            conn, acme, "PO-1001", "2026-10-28", "INR",
            [("Steel bolts", 10.5, minor("200.00"), "kg"),
             ("Safety gloves", 100, minor("150.00"), "pair")],
        )
        add_grn(conn, "GRN-501", po1, "2026-11-03",
                [(po1_bolts, 10.5), (po1_gloves, 60)])
        add_grn(conn, "GRN-502", po1, "2026-11-09",
                [(po1_gloves, 30)])
        inv1 = add_invoice(
            conn, "INV-88", acme, po1, "2026-11-10", "INR",
            [(po1_bolts, 10.5, minor("200.00")), (po1_gloves, 100, minor("150.00"))],
            status="MISMATCH_DETECTED",
        )

        # --- PO-1002: Bharat, INR. Fully received, invoice matches exactly.
        po2, (po2_paper, po2_stapler) = add_po(
            conn, bharat, "PO-1002", "2026-11-01", "INR",
            [("A4 paper ream", 50, minor("320.00"), "ream"),
             ("Heavy-duty stapler", 10, minor("450.00"), "each")],
        )
        add_grn(conn, "GRN-503", po2, "2026-11-06",
                [(po2_paper, 50), (po2_stapler, 10)])
        inv2 = add_invoice(
            conn, "INV-2026-045", bharat, po2, "2026-11-08", "INR",
            [(po2_paper, 50, minor("320.00")), (po2_stapler, 10, minor("450.00"))],
            status="MATCHED_PENDING_APPROVAL",
        )

        # --- PO-1003: Coastal, USD. Invoice arrived, goods not received yet (no GRN).
        po3, (po3_cartons, po3_tape) = add_po(
            conn, coastal, "PO-1003", "2026-11-05", "USD",
            [("Corrugated carton", 200, minor("2.50"), "each"),
             ("Packing tape roll", 48, minor("1.75"), "roll")],
        )
        inv3 = add_invoice(
            conn, "INV-C-311", coastal, po3, "2026-11-12", "USD",
            [(po3_cartons, 200, minor("2.50")), (po3_tape, 48, minor("1.75"))],
            status="EXTRACTED",
        )

        # --- Audit log: a few realistic status-change events ---------------
        add_audit(conn, "invoice", inv1, "status_change", "system",
                  "UPLOADED", "EXTRACTED", "Extraction and validation succeeded")
        add_audit(conn, "invoice", inv1, "status_change", "system",
                  "EXTRACTED", "MISMATCH_DETECTED",
                  "PO line 2 (Safety gloves): billed 100, received 90")
        add_audit(conn, "invoice", inv2, "status_change", "system",
                  "EXTRACTED", "MATCHED_PENDING_APPROVAL", "All matching rules passed")
        add_audit(conn, "invoice", inv3, "status_change", "system",
                  "UPLOADED", "EXTRACTED",
                  "Extraction succeeded; waiting for goods receipt")
        add_audit(conn, "purchase_order", po3, "created", "finance_user_demo",
                  reason="PO entered for Coastal Packaging Co")


# ---------------------------------------------------------------------------
# Post-load checks and report
# ---------------------------------------------------------------------------

def report(conn: sqlite3.Connection) -> None:
    print("\nRow counts")
    for table in ("vendors", "purchase_orders", "po_lines", "invoices",
                  "invoice_lines", "grns", "grn_lines", "audit_log"):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:<16} {count}")

    problems = conn.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        raise SeedError(f"foreign_key_check found {len(problems)} broken reference(s).")
    print("\nforeign_key_check: OK (no broken references)")

    print("\nLine-level three-way view (ordered / received / billed)")
    print(f"  {'PO':<9}{'Ln':<4}{'Item':<20}{'Ordered':>9}{'Received':>10}{'Billed':>8}  Result")
    rows = conn.execute(
        """
        SELECT po.po_number, pl.line_number, pl.description, pl.quantity AS ordered,
               COALESCE((SELECT SUM(gl.quantity_received) FROM grn_lines gl
                         WHERE gl.po_line_id = pl.id), 0) AS received,
               COALESCE((SELECT SUM(il.quantity) FROM invoice_lines il
                         WHERE il.po_line_id = pl.id), 0) AS billed
        FROM po_lines pl
        JOIN purchase_orders po ON po.id = pl.po_id
        ORDER BY po.po_number, pl.line_number
        """
    ).fetchall()
    for r in rows:
        if r["billed"] == 0:
            result = "not billed"
        elif abs(r["received"] - r["billed"]) < 1e-9 and abs(r["ordered"] - r["billed"]) < 1e-9:
            result = "match"
        elif r["received"] < r["billed"]:
            result = "billed > received"
        else:
            result = "check"
        print(f"  {r['po_number']:<9}{r['line_number']:<4}{r['description']:<20}"
              f"{r['ordered']:>9g}{r['received']:>10g}{r['billed']:>8g}  {result}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Create and seed the PO Guard Match database.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help="path of the SQLite file (default: po_guard.db next to this script)")
    parser.add_argument("--reset", action="store_true",
                        help="back up an existing database file and start fresh")
    args = parser.parse_args()

    conn = None
    try:
        conn = prepare_database(args.db, args.reset)
        seed(conn)
        print(f"Database created and seeded: {args.db}")
        report(conn)
        return 0
    except SeedError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        if conn is not None:
            print("  Nothing from the seed data was saved (the transaction was rolled back).\n"
                  "  Fix the problem above, then re-run with --reset.", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(f"\nUNEXPECTED DATABASE ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())