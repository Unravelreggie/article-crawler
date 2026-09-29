# Architecture and operational state

This repository is independently deployable and contains no bundled user data or third-party PDF content.

- Search: FastAPI calls official structured APIs and records the query and source-specific hits in PostgreSQL.
- PDF references: the API stores the uploaded PDF in RustFS and enqueues a Celery task. Text extraction and citation matching run in the worker.
- Full text: after user selection or match confirmation, Celery looks for a licensed OA PDF, validates it, and records its SHA-256 and provenance in PostgreSQL; bytes live in RustFS.
- UI: React/TypeScript/Vite. Nginx proxies /api to FastAPI. Compose exposes only the local web port.
- Failure state: each document and download has a visible status and error. API source failures return a partial result warning.

The initial release is for local research use. Authentication and OIDC, multi-user roles, OCR for scanned PDFs, and production deployment are out of scope. A public deployment requires server-side identity and access control, pinned service image digests, backup/restore validation, and acceptance testing.
