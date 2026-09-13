# Project status

The project provides page-image import and a new one-page editable PDF import. Public browser capture, Canva OAuth, sequential full-resolution image transfer, frame creation, cancellation, and rollback remain implemented.

The former AI reconstruction pipeline remains removed. New PDF conversion extracts structured objects deterministically and uses conservative image fallbacks. Optional user-triggered cloud font suggestions are separate and require OPENAI_API_KEY; neither normal image import nor PDF conversion requires that key.

The saved August 2026 capture report records five structurally validated cases containing 106 pages. This historical result is not a fresh compatibility guarantee and does not prove page-by-page visual correspondence.

Current checks are described in TESTING.md. The service remains a local development application with process-local jobs, in-memory image caches, expiring disk artifacts, and no multi-user authentication.

Editable import is implemented and covered by PDFium/subprocess tests and mocked Figma tests. Live Canva/Figma acceptance and real cloud font identification remain unverified in this implementation session. See EDITABLE_IMPORT.md for supported content, known fidelity limits and the acceptance checklist.
