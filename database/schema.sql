-- =====================================================================
-- PO Guard Match: core MVP schema (SQLite)
-- Implements ADR-001 and the approved database design.
--
-- Conventions
--   * Money is stored as INTEGER minor units (paise / cents). Never floats.
--   * Quantities are REAL (fractional units allowed). Compare with a
--     tolerance in the matching engine, not with exact equality.
--   * Dates are ISO-8601 TEXT (YYYY-MM-DD). Timestamps are UTC ISO-8601.
--   * Every foreign key is ON DELETE RESTRICT: a parent row cannot be
--     deleted while child rows exist. Prefer statuses / is_active over
--     deleting anything.
--   * Foreign keys are NOT enforced by SQLite unless each connection runs
--     PRAGMA foreign_keys = ON (the application code does this).
--   * AUTOINCREMENT guarantees ids are never reused after a delete, so
--     old audit_log rows can never point at a different, newer row.
-- =====================================================================


-- ---------------------------------------------------------------------
-- vendors: supplier master data, stored once.
-- ---------------------------------------------------------------------
CREATE TABLE vendors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL CHECK (length(trim(name)) > 0),
    tax_id      TEXT    UNIQUE,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);


-- ---------------------------------------------------------------------
-- purchase_orders: header of what was ordered.
-- ---------------------------------------------------------------------
CREATE TABLE purchase_orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    po_number   TEXT    NOT NULL UNIQUE CHECK (length(trim(po_number)) > 0),
    vendor_id   INTEGER NOT NULL
                REFERENCES vendors (id) ON DELETE RESTRICT,
    order_date  TEXT    NOT NULL
                CHECK (date(order_date) IS NOT NULL AND date(order_date) = order_date),
    currency    TEXT    NOT NULL
                CHECK (length(currency) = 3 AND currency = upper(currency)),
    status      TEXT    NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'closed', 'cancelled')),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX idx_purchase_orders_vendor ON purchase_orders (vendor_id);


-- ---------------------------------------------------------------------
-- po_lines: each ordered item. The hub that invoice_lines and grn_lines
-- both point to; this is what makes line-level three-way matching work.
-- ---------------------------------------------------------------------
CREATE TABLE po_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id            INTEGER NOT NULL
                     REFERENCES purchase_orders (id) ON DELETE RESTRICT,
    line_number      INTEGER NOT NULL CHECK (line_number > 0),
    description      TEXT    NOT NULL CHECK (length(trim(description)) > 0),
    quantity         REAL    NOT NULL CHECK (quantity > 0),
    unit_price_minor INTEGER NOT NULL CHECK (unit_price_minor >= 0),
    unit_of_measure  TEXT    NOT NULL CHECK (length(trim(unit_of_measure)) > 0),
    UNIQUE (po_id, line_number)
);
-- The UNIQUE (po_id, line_number) constraint already indexes po_id lookups.


-- ---------------------------------------------------------------------
-- invoices: header of a vendor's bill. One invoice per PO (po_id required).
-- total_minor is the vendor's CLAIMED total; the matching engine compares
-- it with the sum of the lines.
-- ---------------------------------------------------------------------
CREATE TABLE invoices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number  TEXT    NOT NULL CHECK (length(trim(invoice_number)) > 0),
    vendor_id       INTEGER NOT NULL
                    REFERENCES vendors (id) ON DELETE RESTRICT,
    po_id           INTEGER NOT NULL
                    REFERENCES purchase_orders (id) ON DELETE RESTRICT,
    invoice_date    TEXT    NOT NULL
                    CHECK (date(invoice_date) IS NOT NULL AND date(invoice_date) = invoice_date),
    currency        TEXT    NOT NULL
                    CHECK (length(currency) = 3 AND currency = upper(currency)),
    total_minor     INTEGER NOT NULL CHECK (total_minor >= 0),
    status          TEXT    NOT NULL DEFAULT 'UPLOADED'
                    CHECK (status IN (
                        'UPLOADED',
                        'EXTRACTION_FAILED',
                        'EXTRACTED',
                        'REVIEW_REQUIRED',
                        'MISMATCH_DETECTED',
                        'MATCHED_PENDING_APPROVAL',
                        'APPROVED',
                        'REJECTED',
                        'POSTED',
                        'PROCESSING_RETRY'
                    )),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    -- Two different vendors may both use invoice number "1001";
    -- the same vendor reusing a number is the duplicate we block.
    UNIQUE (vendor_id, invoice_number)
);

CREATE INDEX idx_invoices_po ON invoices (po_id);
-- vendor_id lookups are covered by the UNIQUE (vendor_id, invoice_number) index.


-- ---------------------------------------------------------------------
-- invoice_lines: each billed item, tied to the PO line it claims to bill.
-- NOTE: the rule "this po_line must belong to the invoice's PO" is
-- enforced in application code and in the matching engine (see ADR-001
-- discussion), not by the schema.
-- ---------------------------------------------------------------------
CREATE TABLE invoice_lines (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id        INTEGER NOT NULL
                      REFERENCES invoices (id) ON DELETE RESTRICT,
    po_line_id        INTEGER NOT NULL
                      REFERENCES po_lines (id) ON DELETE RESTRICT,
    quantity          REAL    NOT NULL CHECK (quantity > 0),
    unit_price_minor  INTEGER NOT NULL CHECK (unit_price_minor >= 0)
);

CREATE INDEX idx_invoice_lines_invoice ON invoice_lines (invoice_id);
CREATE INDEX idx_invoice_lines_po_line ON invoice_lines (po_line_id);


-- ---------------------------------------------------------------------
-- grns: header of a goods receipt (goods physically received).
-- ---------------------------------------------------------------------
CREATE TABLE grns (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    grn_number     TEXT    NOT NULL UNIQUE CHECK (length(trim(grn_number)) > 0),
    po_id          INTEGER NOT NULL
                   REFERENCES purchase_orders (id) ON DELETE RESTRICT,
    received_date  TEXT    NOT NULL
                   CHECK (date(received_date) IS NOT NULL AND date(received_date) = received_date),
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX idx_grns_po ON grns (po_id);


-- ---------------------------------------------------------------------
-- grn_lines: quantity actually received per PO line. The "third way".
-- A PO line may receive goods across several GRNs (partial deliveries);
-- the matching engine sums them. Zero is allowed (item not delivered).
-- ---------------------------------------------------------------------
CREATE TABLE grn_lines (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    grn_id             INTEGER NOT NULL
                       REFERENCES grns (id) ON DELETE RESTRICT,
    po_line_id         INTEGER NOT NULL
                       REFERENCES po_lines (id) ON DELETE RESTRICT,
    quantity_received  REAL    NOT NULL CHECK (quantity_received >= 0)
);

CREATE INDEX idx_grn_lines_grn     ON grn_lines (grn_id);
CREATE INDEX idx_grn_lines_po_line ON grn_lines (po_line_id);


-- ---------------------------------------------------------------------
-- audit_log: append-only history of who did what and when.
-- Deliberately has NO foreign keys: it records events about any table,
-- and must never block (or be removed by) changes elsewhere.
-- ---------------------------------------------------------------------
CREATE TABLE audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type  TEXT    NOT NULL
                 CHECK (entity_type IN (
                     'vendor', 'purchase_order', 'po_line',
                     'invoice', 'invoice_line', 'grn', 'grn_line'
                 )),
    entity_id    INTEGER NOT NULL,
    action       TEXT    NOT NULL CHECK (length(trim(action)) > 0),
    old_status   TEXT,
    new_status   TEXT,
    actor        TEXT    NOT NULL CHECK (length(trim(actor)) > 0),
    reason       TEXT,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX idx_audit_log_entity ON audit_log (entity_type, entity_id);

-- Append-only enforcement: any UPDATE or DELETE on audit_log is refused.
CREATE TRIGGER audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: UPDATE is not allowed');
END;

CREATE TRIGGER audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: DELETE is not allowed');
END;