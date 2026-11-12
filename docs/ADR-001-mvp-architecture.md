# ADR-001: MVP Architecture and Technology Stack

- **Status:** Accepted (for the MVP)
- **Date:** 2026-11-12
- **Project:** PO Guard Match

## Context

PO Guard Match automates three-way matching between Purchase Orders (PO), Invoices, and Goods Receipt Notes (GRN). Matching decisions affect payments, so they must be reproducible and auditable.

Constraints for the MVP:

- Python backend.
- Free and local tools preferred.
- PDF document processing.
- AI-assisted extraction where needed.
- Deterministic Python matching rules.
- Human-in-the-loop approval.
- Database storage.
- Mock ERP integration.
- Built by one beginner developer, so the architecture must stay simple to understand, debug, and test.

## Decision

### Architecture style

A **modular monolith**: one FastAPI application with separate modules (API and auth, ingestion, extraction, validation, matching engine, review service, ERP adapter, audit trail writer). Modules communicate through ordinary Python function calls, and the database is the shared state.

### Core design principle

AI proposes, Python rules decide, humans approve.

- The LLM is used only in the extraction step.
- Extracted data must pass Pydantic validation before matching.
- The matching engine contains no AI. It applies deterministic rules and records a reason for each rule.
- A human approves every case before posting to the ERP.

### Technology stack

| Concern | Choice | Reason |
|---|---|---|
| Backend framework | FastAPI | Already known, with Pydantic integration built in. |
| Schemas and validation | Pydantic | Defines the fixed extraction schema and catches bad data before matching. |
| Database | SQLite via SQLAlchemy | Zero setup and local. SQLAlchemy keeps a later move to PostgreSQL straightforward. |
| Document storage | Filesystem (`storage/documents/`) | PDFs stay out of the database. The database stores path, SHA-256 hash, and metadata. |
| PDF text extraction | pdfplumber | Free and reads the text layer of digital PDFs. |
| OCR | Tesseract (via pytesseract) | Used only if scanned documents turn out to exist in the real data. |
| AI extraction | Ollama with a local model | Free and local, so no document leaves the machine. Output is forced into the Pydantic schema. |
| Authentication | Simple login with hashed passwords (passlib with bcrypt) | Enforces role separation without building a full auth system. |
| Roles | admin, finance, reviewer | Supports segregation of duties: the uploader should not be the approver. |
| Testing | pytest | Most tests target the matching rules, which are plain Python. |
| ERP integration | Mock ERP behind an adapter interface | The real ERP can replace the mock without changing matching or review. |
| Processing trigger | FastAPI background tasks | Lets the upload return immediately without a queue. |

### Supporting decisions

- **Status-driven workflow.** Each case carries a status: `UPLOADED`, `EXTRACTION_FAILED`, `EXTRACTED`, `REVIEW_REQUIRED`, `MISMATCH_DETECTED`, `MATCHED_PENDING_APPROVAL`, `APPROVED`, `REJECTED`, `POSTED`, `PROCESSING_RETRY`. Every status change writes an audit-trail row with a reason.
- **Duplicate detection.** Files are hashed (SHA-256) on upload, and exact duplicates are rejected.
- **Capped retries.** `PROCESSING_RETRY` has a retry counter and a cap, so a failing ERP call cannot loop forever.
- **Minimal auth.** Three roles and password login. Not included: OAuth, SSO, password-reset flows.

### Open items (not yet decided)

- Review UI: Streamlit or simple HTML templates.
- Which Ollama model to use, to be chosen after measuring extraction accuracy on sample documents.
- How to handle multiple invoices or partial deliveries against one PO (deferred, see Consequences).

## Alternatives considered

### Alternative A: Queue and background workers

The upload API places jobs on a queue (Redis with RQ, or Celery). Separate worker processes run extraction and matching asynchronously.

- **Pros:** best fit for high volume or slow extraction, isolates failures, scales workers independently.
- **Cons:** adds Redis and worker processes, makes distributed failures and duplicate jobs possible, and is harder to debug and test.
- **Why not now:** the MVP handles tens of documents, not thousands. FastAPI background tasks cover the "don't block the upload" need.

### Alternative B: n8n-centred orchestration

n8n handles ingestion and routing, and calls a small Python service exposing `/extract` and `/match`. Exceptions are routed to a human approval step.

- **Pros:** fast to assemble, visual flow, and useful if non-developers need to edit the process.
- **Cons:** workflow state is hidden in the n8n UI, business logic splits across two tools, workflow logic is hard to unit test, and it teaches less core engineering.
- **Why not now:** auditable, testable matching rules are the heart of this product and should live in version-controlled Python.

## Trade-offs of the chosen approach

| Benefit | Cost |
|---|---|
| One process and one log make debugging easy. | A slow extraction step shares resources with the API. |
| Matching rules are plain Python, so they are fully unit-testable. | Throughput is limited compared with a worker-based design. |
| SQLite needs no setup. | Limited concurrent writes, so not suitable for many simultaneous users. |
| A local LLM is free and keeps documents on the machine. | A small local model is likely less accurate on messy invoices than a large hosted one, so the validation-and-review path matters. |
| The ERP adapter interface lets the mock be swapped later. | An interface costs a little upfront design effort. |
| Minimal auth enforces separation of duties. | It is not production-grade security. |

## Consequences

- Module boundaries (extraction, matching, ERP adapter) must stay clean. This is what makes a later move to Alternative A possible, since only how steps are triggered would change.
- Extraction accuracy must be measured on a sample document set before the LLM output is trusted.
- Real-world cases such as multiple invoices per PO and partial deliveries are out of scope for the MVP and need a later iteration.
- Security for real financial data would need a separate review before any production use.

## Revisit this decision if

- Document volume or extraction time makes uploads noticeably slow, which points to Alternative A.
- Several users need to work concurrently, which points to a move from SQLite to PostgreSQL.
- Non-developers need to edit the process flow, which points to reconsidering Alternative B for the orchestration layer.
- Real ERP integration begins, which means implementing the adapter interface against the real system.