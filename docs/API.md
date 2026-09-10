# HTTP API

Local development base URL:

```text
http://localhost:3000
```

All API errors are JSON objects with an `error.code` and bounded `error.message`. Unknown routes, unsupported methods, oversized requests, malformed payloads, and unsupported source URLs do not return Flask HTML error pages.

All errors use this shape:

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable explanation"
  }
}
```

## Protocol conventions

- Requests and responses use camelCase JSON aliases.
- POST request bodies must use `Content-Type: application/json`.
- Request bodies are limited to 2 MB.
- Resource IDs in paths are UUIDs; malformed and expired IDs return the same scoped not-found response.
- Capture and reconstruction records expire according to `ARTIFACT_TTL_SECONDS`.
- Large JSON is limited by `MAX_API_RESPONSE_BYTES`.
- Binary page/asset endpoints are private-cacheable and carry an ETag.
- Cancellable work is cooperative. A DELETE marks the job cancelled immediately, while an in-flight browser/provider operation can take time to observe its signal.
- The current reconstruction runner uses `queued → analyzing → terminal`; Figma rendering happens after the backend job completes.

## `GET /api/health`

```json
{
  "status": "ok",
  "service": "canva-converter",
  "runtime": "python-flask",
  "schemaVersion": 1,
  "aiConfigured": true,
  "providers": {
    "ocr": "openai-vision",
    "layout": "openai",
    "ocrConfigured": true,
    "layoutConfigured": true
  },
  "browser": {
    "portable": true,
    "autoInstall": true,
    "overrideConfigured": false
  },
  "canvaOAuth": {
    "configured": true,
    "connected": false,
    "scopes": ["design:meta:read", "design:content:read"]
  },
  "limits": {
    "captureConcurrency": 2,
    "reconstructionConcurrency": 2,
    "captureTimeoutMs": 1800000,
    "maxCaptureBytes": 536870912,
    "maxApiResponseBytes": 67108864,
    "artifactTtlSeconds": 3600
  }
}
```

`aiConfigured` is true when the OpenAI Platform key used by both editable stages is present. `providers.ocrConfigured` and `providers.layoutConfigured` expose the same backend credential readiness. Exact page-image import works without AI credentials.
The browser object reports portable browser resolution. `overrideConfigured` is normally false because an OS-specific executable path is no longer required.

`canvaOAuth.configured` checks local credential presence only. `connected` reflects the memory-only user token held by the running Flask process.

## Canva OAuth trial endpoints

### `GET /api/canva/oauth/status`

Returns the `canvaOAuth` object shown above. It never returns tokens, client credentials, PKCE verifier, state, or user data.

### `POST /api/canva/oauth/start`

Returns a short-lived Canva authorization URL. The backend stores a one-time PKCE verifier and state for ten minutes.

```json
{ "authorizationUrl": "https://www.canva.com/api/oauth/authorize?..." }
```

### `GET /api/canva/oauth/callback`

The exact Canva Developer Portal redirect. It validates `state`, exchanges `code` using backend-only client authentication and PKCE, stores rotating tokens in memory, and returns a small browser completion page. It is not called by plugin JavaScript.

### `DELETE /api/canva/oauth/disconnect`

Clears local pending state and tokens, then best-effort revokes the remote refresh token. Returns `204` even if no account was connected.

### `GET /api/canva/designs`

Lists up to 50 designs available to the connected user. Optional `query` and opaque `continuation` query parameters support search/pagination. The plugin currently displays the first result page.

```json
{
  "items": [{
    "id": "DESIGN_ID",
    "title": "Canva design",
    "pageCount": 12,
    "designTypes": ["presentation"]
  }],
  "continuation": null
}
```

### `POST /api/canva/oauth/capture-jobs`

Request:

```json
{ "designId": "DESIGN_ID" }
```

Returns the standard `202 {"jobId":"..."}` capture-job contract. It uses official lossless per-page PNG export and the same bounded worker pool, status endpoint, cancellation endpoint, artifact store, preview, exact image import, and editable reconstruction flow as public-link capture.

Common OAuth errors include `CANVA_OAUTH_NOT_CONFIGURED`, `CANVA_NOT_CONNECTED`, `CANVA_OAUTH_STATE_INVALID`, `CANVA_PERMISSION_DENIED`, `CANVA_RATE_LIMITED`, `CANVA_EXPORT_FAILED`, `CANVA_EXPORT_TIMEOUT`, and `CANVA_EXPORT_DOWNLOAD_FAILED`.

## `POST /api/capture-jobs`

Starts the cancellable capture workflow used by the Figma plugin.

Request:

```json
{
  "url": "https://www.canva.com/design/DESIGN_ID/SHARE_TOKEN/edit"
}
```

Response: `202 Accepted`

```json
{ "jobId": "UUID" }
```

## `GET /api/capture-jobs/{jobId}`

Returns `queued`, `capturing`, `completed`, `failed`, or `cancelled`, plus progress, current page, total pages, message, and the completed `captureId`.

Example:

```json
{
  "id": "job-uuid",
  "url": "https://www.canva.com/design/DESIGN_ID/SHARE_TOKEN/view",
  "status": "capturing",
  "progress": 52,
  "currentPage": 8,
  "totalPages": 18,
  "message": "Page 8: 794×1123 (portrait).",
  "createdAt": "2026-08-24T12:00:00Z",
  "updatedAt": "2026-08-24T12:00:30Z"
}
```

`currentPage` is progress display data, not a page identity. Use capture page `index` and `id` after completion.

Failed/cancelled jobs include a nested `error` object containing a stable code and bounded message.

## `DELETE /api/capture-jobs/{jobId}`

Requests cancellation and returns `202 Accepted` with `{ "status": "cancelled" }`.

## `GET /api/captures/{captureId}`

Returns capture title and page preview metadata. Full-resolution PNG bytes remain behind each page's `imageUrl`.

The response shape matches successful `POST /api/captures`. It intentionally excludes source URL, internal expiry, screenshots as base64, and DOM hints.

## `POST /api/captures` (compatibility)

The original blocking capture endpoint remains for local API compatibility. The Figma plugin uses the asynchronous capture-job endpoints above.

Request:

```json
{
  "url": "https://www.canva.com/design/DESIGN_ID/SHARE_TOKEN/edit"
}
```

Successful response: `201 Created`

```json
{
  "captureId": "capture-uuid",
  "title": "Canva design title",
  "pages": [
    {
      "id": "page-uuid",
      "index": 0,
      "width": 794,
      "height": 1123,
      "orientation": "portrait",
      "thumbnail": "data:image/jpeg;base64,...",
      "imageUrl": "/api/captures/capture-uuid/pages/page-uuid/image"
    }
  ]
}
```

The endpoint accepts trusted Canva design links ending in `/view`, `/edit`, `/present`, or `/watch`, base/token-only Canva design links, and `canva.link` short links. Query strings and fragments are removed, and supported routes are canonicalized to `/view` before capture. The endpoint returns all detected pages. `orientation` is `portrait`, `landscape`, or `square` and is calculated independently for every page, so mixed-orientation Canva documents are preserved. `thumbnail` is a compact preview and must not be used for the final Figma image.

Common capture errors:

| Code | Meaning |
| --- | --- |
| `INVALID_SOURCE` | URL is not a trusted Canva design/share route or `canva.link` URL |
| `SOURCE_NOT_PUBLIC` | Design is private or inaccessible anonymously |
| `AUTH_REQUIRED` | Canva requires login |
| `UNSUPPORTED_SOURCE` | Design type is outside the fixed-size scope |
| `SOURCE_CHALLENGED` | Canva presented an automation challenge |
| `SOURCE_RATE_LIMITED` | Canva rate-limited capture |
| `SOURCE_UNAVAILABLE` | Canva failed to load before the deadline |
| `CAPTURE_DUPLICATE` | The same URL is already being captured |
| `CAPTURE_BUSY` | Capture concurrency is full |
| `CAPTURE_CANCELLED` | User cancelled the asynchronous capture |
| `CAPTURE_JOB_NOT_FOUND` | Capture job expired or does not exist |
| `CAPTURE_SETUP` | Browser configuration is invalid |
| `CAPTURE_INCOMPLETE` | Canva reported more pages than could be captured |
| `CAPTURE_DUPLICATE_PAGE` | A page repeated without authoritative evidence that Canva advanced; the import is rejected instead of returning duplicated pages |
| `MULTI_PAGE_CAPTURE_UNAVAILABLE` | Only a cover preview was available for a multi-page design |
| `CAPTURE_LIMIT_EXCEEDED` | Page dimensions or bytes exceed configured limits |
| `API_RESPONSE_LIMIT_EXCEEDED` | Preview or editable result metadata exceeds the configured response limit |

## `GET /api/captures/{captureId}/pages/{pageId}/image`

Response: `200 OK`, `Content-Type: image/png`

The endpoint is capture-scoped, supports an ETag, and uses private caching until the capture expires. Missing or expired pages return `404` with `CAPTURE_NOT_FOUND`.

Clients should send `Accept: image/png`, enforce the 25 MB page limit, and decode the PNG before giving it to Figma. Conditional requests may return `304 Not Modified`.

## `POST /api/reconstruction-jobs`

Request:

```json
{
  "captureId": "capture-uuid",
  "pageIds": ["page-uuid-1", "page-uuid-2"]
}
```

Successful response: `202 Accepted`

```json
{
  "jobId": "job-uuid"
}
```

This endpoint requires `OPENAI_API_KEY`. The same backend-only credential powers schema-constrained vision OCR and layout analysis. A missing credential returns `503` with `AI_NOT_CONFIGURED`.
When both reconstruction workers are occupied, the endpoint returns `429` with `RECONSTRUCTION_BUSY` instead of creating an unbounded queue.

## `GET /api/reconstruction-jobs/{jobId}`

In-progress example:

```json
{
  "id": "job-uuid",
  "captureId": "capture-uuid",
  "pageIds": ["page-uuid"],
  "status": "analyzing",
  "progress": 42,
  "currentPage": 1,
  "totalPages": 1,
  "message": "Reconstructing Canva page 1",
  "createdAt": "2026-08-21T00:00:00Z",
  "updatedAt": "2026-08-21T00:00:05Z"
}
```

Possible statuses are `queued`, `capturing`, `analyzing`, `rendering`, `completed`, `failed`, and `cancelled`. A completed response includes `result`, a validated `DesignDocumentV1`.

Completed image assets report `source=canva` when a trusted original was recovered and `source=crop` when screenshot pixels were preserved. Image node `reason` distinguishes no match, unavailable original, unsupported crop/stretching, and unsupported masks. Rectangular clipping uses the optional backward-compatible `clipsContent` group field.

Backward-compatible V1 nodes may also contain two-stop linear/radial gradient fills and bounded drop-shadow/layer-blur effects. Unsupported or incomplete visual effects remain regional raster fallbacks with explicit reasons instead of being silently approximated.

The current backend emits `queued`, `analyzing`, and a terminal status. The broader status union is retained for contract evolution.

Each completed Design IR page includes quality metrics:

```json
{
  "nativeCoverage": 0.86,
  "fallbackCoverage": 0.14,
  "exactTextRate": 0.98,
  "visualSimilarity": 0.96,
  "pixelDifference": 0.04,
  "missingRegionRate": 0,
  "duplicateTextBlocks": 0,
  "missingFonts": [],
  "warnings": []
}
```

Raw pixel comparison covers backend-renderable backgrounds, image crops, rectangles, and ellipses. Text and SVG pixels are excluded from that score and assessed through text accuracy, coverage, duplicate detection, and the final Figma render.

## `DELETE /api/reconstruction-jobs/{jobId}`

Successful response: `202 Accepted`

```json
{
  "status": "cancelled"
}
```

## `GET /api/reconstruction-jobs/{jobId}/assets/{assetId}`

Streams one expiring reconstructed image or QA asset. Completed job JSON uses these URLs instead of embedding base64 images.

Asset IDs are resolved within the requested reconstruction job. Missing jobs and missing assets return `JOB_NOT_FOUND` and `ASSET_NOT_FOUND` respectively. Assets above 25 MB are refused.

## `POST /api/reconstruction-jobs/{jobId}/figma-qa`

The plugin calls this after Figma has rendered and exported every page. The request must contain each reconstructed page exactly once:

```json
{
  "pages": [{
    "pageId": "page-uuid",
    "exportWidth": 1920,
    "exportHeight": 1080,
    "comparedWidth": 1920,
    "comparedHeight": 1080,
    "exportByteLength": 123456,
    "exportSha256": "64-lowercase-hex-characters",
    "pixelDifference": 0.03,
    "visualSimilarity": 0.97,
    "mismatchRate": 0.08
  }]
}
```

The response is a versioned page-level acceptance report. It combines final Figma pixels with backend text, coverage, missing-region, and duplicate metrics; exact export-dimension checks; thresholds; and a top-level `passed` result. `sourceFingerprint` is a SHA-256 of the captured source string so evidence can be associated without exposing a Canva token.

The endpoint rejects incomplete or duplicated page sets, pages outside the job, inconsistent similarity/difference values, wrong aspect ratios, oversized exports, non-completed jobs, and expired captures. Submitted reports expire with their reconstruction job.

## `GET /api/reconstruction-jobs/{jobId}/figma-qa`

Returns the persisted final-Figma QA report. It returns `FIGMA_QA_NOT_FOUND` until a valid report has been submitted. `python -m scripts.phase1_report` uses this endpoint to collect a sanitized five-category baseline.

## CORS and network access

The local service accepts loopback origins and Figma origins. The development Figma manifest permits only `http://localhost:3000`. Production hosting requires an explicit hosted-domain manifest update.

CORS is not authentication. The API is intended only for a loopback development process.

## General HTTP errors

| HTTP | Code | Meaning |
| ---: | --- | --- |
| 400 | `INVALID_REQUEST` | malformed JSON or request validation |
| 404 | `NOT_FOUND` | unknown endpoint |
| 405 | `METHOD_NOT_ALLOWED` | unsupported method |
| 413 | `REQUEST_TOO_LARGE` | request body exceeds 2 MB |
| 413 | `API_RESPONSE_LIMIT_EXCEEDED` | generated JSON exceeds configured response limit |
| 415 | `INVALID_REQUEST` | request is not JSON |
| 500 | `INTERNAL_ERROR` | unexpected error; internal details are not returned |

Service-specific capture/reconstruction codes are described with their endpoints. Error messages are sanitized and bounded; clients should branch on `error.code`, not parse message text.
