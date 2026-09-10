# Project status

The project now provides Canva page-image import only. Public browser capture, optional Canva OAuth export, preview selection, sequential full-resolution transfer, frame creation, cancellation, and rollback remain implemented.

The former model-based editable conversion feature has been removed from the UI, backend, configuration, storage APIs, and supporting tools. No image-analysis credentials are required.

The saved August 2026 capture report records five structurally validated cases containing 106 pages. This historical result is not a fresh compatibility guarantee and does not prove page-by-page visual correspondence.

Current checks are described in TESTING.md. The service remains a local development application with process-local jobs, in-memory image caches, expiring disk artifacts, and no multi-user authentication.

Remaining acceptance work includes real Figma checks, broader live Canva compatibility, extreme-size and large-deck tests, and reliability under crashes, disk errors, and restart.
