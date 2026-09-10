# HTTP API

Base URL: `http://localhost:3000`. Errors use `{"error":{"code":"CODE","message":"Description"}}`. Requests with JSON bodies must use `Content-Type: application/json`. Request bodies are limited to 2 MiB.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | /api/health | Service identity, browser settings, OAuth status, capture limits |
| POST | /api/capture-jobs | Submit `{"url":"https://www.canva.com/design/..." }`; returns 202 and jobId |
| GET | /api/capture-jobs/{jobId} | Status, progress, page count, message, captureId or error |
| DELETE | /api/capture-jobs/{jobId} | Request cancellation; returns 202 |
| POST | /api/captures | Synchronous capture of the same URL body; returns 201 |
| GET | /api/captures/{captureId} | Title and page previews |
| GET | /api/captures/{captureId}/pages/{pageId}/image | Full-resolution PNG |
| GET | /api/canva/oauth/status | configured, connected, and scopes |
| POST | /api/canva/oauth/start | authorizationUrl |
| GET | /api/canva/oauth/callback | Browser callback with state and code |
| DELETE | /api/canva/oauth/disconnect | Clear local tokens and attempt revocation |
| GET | /api/canva/designs | Search using query and optional continuation |
| POST | /api/canva/oauth/capture-jobs | Submit `{"designId":"..." }`; returns 202 and jobId |

Capture-job states are queued, capturing, completed, failed, and cancelled. Worker capacity is bounded; busy requests return 429.

A capture response contains captureId, title, and pages. Each page has id, index, width, height, orientation, thumbnail, and imageUrl. Indices are zero-based and image paths are capture-scoped. PNG responses provide ETags and private caching.

Missing or expired captures/jobs return 404. Validation failures return 400. Unsupported sources, access challenges, and acquisition failures have specific codes. Unknown endpoints return a structured 404.

Use the asynchronous capture endpoint in interactive clients. The health endpoint describes configuration and does not perform a live capture.
