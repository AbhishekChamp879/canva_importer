# Security and Privacy Model

## Deployment assumption

The current system is a single-user local development service. It binds to loopback and is called by a Figma development plugin. It is not designed for exposure to a LAN or the public internet.

Do not deploy `python run.py` publicly. A hosted service requires authentication, tenant isolation, authorization, rate limits, durable isolated storage, a production WSGI/runtime, secret management, and security review.

## Assets and secrets

Sensitive data includes:

- Canva share URLs/tokens;
- Canva OAuth client secret, authorization codes, access tokens, refresh tokens, PKCE verifier, and state;
- captured page pixels and text/image hints;
- OpenAI API keys;
- reconstructed image crops and QA references;
- Figma content after import.

Controls:

- `.env` and `.jobs/` are ignored by Git.
- Provider credentials remain in backend process configuration.
- The plugin receives no provider keys.
- Public error sanitization redacts OpenAI keys, common secret key/value patterns, sensitive query parameters, Canva short-link paths, and Canva share-token path segments.
- Error messages are flattened and limited to 1,200 characters.
- Artifacts expire according to TTL.

Limitations:

- Artifacts are local files, not encrypted by the application.
- There is no per-user encryption or access control.
- Editable reconstruction sends screenshots to OpenAI for both OCR and layout analysis.
- Local process users with filesystem access may read `.env` and `.jobs`.

## URL and SSRF controls

Source validation requires:

- HTTPS;
- default port 443;
- no username/password;
- exact `canva.link`, `canva.com`, or `www.canva.com` source hosts;
- a constrained design/share path;
- bounded URL/path/segment length;
- no control characters, backslashes, or encoded path separators.

Short links are resolved one hop at a time. Every intermediate and final redirect is revalidated; untrusted hosts, loops, and excessive hops fail.

Open Graph images require HTTPS, an allowed Canva asset subdomain, an image content type, a final allowed asset host, a 25 MB limit, and successful image decoding.

Original editable assets require HTTPS Canva asset hosts and every redirect is revalidated. Responses must use a supported image MIME type, match the decoded format/signature, stay within 25 MB, 32,768 pixels per dimension, and 100 million decoded pixels. WebP/AVIF inputs are decoded and normalized to PNG before entering IR. Signed Canva asset URLs remain in local capture evidence only: the URL field is removed before OpenAI layout analysis and is never sent to the Figma plugin.

## Canva OAuth trial

- Uses Authorization Code with PKCE/SHA-256.
- Each authorization has a high-entropy state and verifier; state is one-time and expires after ten minutes.
- The client secret is read only from backend configuration.
- Authorization codes, access/refresh tokens, state, and verifier are never returned to the plugin.
- User tokens are memory-only and are removed on disconnect or process restart.
- Refresh-token exchange is serialized because Canva rotates refresh tokens.
- Only the two read scopes required for design metadata and export are requested.
- Official export downloads allow HTTPS Canva hosts only, manually validate every redirect, limit redirects, validate MIME and decoded image content, and enforce per-page and total limits.
- This local single-user token model is not suitable for hosting. Production requires authenticated session binding, encrypted tenant-scoped storage, CSRF controls, auditability, key rotation, production HTTPS redirects, and Canva review.

Residual risk:

- DNS resolution is delegated to the operating system; the code does not pin Canva IP ranges.
- Canva content itself is untrusted and runs inside an isolated browser context, but Playwright/browser vulnerabilities are outside application validation.
- The browser uses `--no-sandbox` for deployment compatibility. This weakens Chromium process isolation and is unsuitable for a public multi-tenant service.

## Local API boundary

`HOST` accepts loopback only. CORS allows:

- missing origin and `null`, required by some plugin/local contexts;
- loopback HTTP origins;
- `figma.com` and its HTTPS subdomains.

Lookalike hosts, credentialed origins, paths, queries, and fragments are rejected.

Responses add:

- `X-Content-Type-Options: nosniff`;
- `Referrer-Policy: no-referrer`;
- a specific allowed origin and `Vary: Origin` when accepted.

CORS is not authentication. Any local process can call an unauthenticated loopback port, and allowed null-origin contexts are broad. Keep the service stopped when not in use and do not bind it through a proxy without adding authentication.

## Request and response limits

- Flask request body: 2 MB.
- JSON API response: configurable, default 64 MB.
- Logical captured page: 8,192 pixels per dimension.
- Captured/imported image: 25 MB.
- Total capture: configurable, default 512 MB.
- Embedded IR asset: 25 MB.
- Total embedded IR assets: 512 MB.
- IR nodes per page: 5,000.
- OpenAI OCR blocks: 1,000.
- OpenAI layout elements: 1,000.
- Original Canva asset: 25 MB encoded response, 32,768 pixels per axis, 100 million decoded pixels, and five redirects.
- Capture/reconstruction concurrency is bounded.
- Capture/provider/install operations have deadlines.

These controls reduce memory/disk exhaustion but do not replace host-level quotas.

## Model output boundary

OpenAI OCR and layout model output is untrusted:

1. Response must parse as a JSON object.
2. It must match a closed, smaller `ReconstructedPage` schema.
3. Malformed output is retried once.
4. Coordinates, IDs, hierarchy, duplicates, colors, confidence, and element types are normalized deterministically.
5. The final document must validate as strict `DesignDocumentV1`.
6. Invalid/uncertain regions become raster fallbacks.

The model cannot specify arbitrary code, filesystem paths, HTTP requests, or Figma API calls.

## SVG boundary

Vectors are validated before entering Design IR. The validator rejects:

- malformed XML or non-SVG roots;
- doctype/entity declarations;
- script, foreignObject, iframe, object, or embed elements;
- event attributes;
- HTTP, data, JavaScript, or protocol-relative references;
- external `url(...)` paint references and `@import`.

Only internal fragment references are allowed. The Figma runtime receives the validated string.

## Plugin UI and document mutations

- UI uses DOM creation and `textContent`, not untrusted `innerHTML`.
- Full-resolution page paths must begin with the capture-scoped API prefix.
- Page metadata and byte length are revalidated in the Figma main thread.
- Exact image imports use session IDs, expected page IDs, single-use pages, acknowledgements, and rollback.
- Editable import validates its top-level contract and rolls back created frames on fatal failure.
- Provider/source metadata is stored as strings in plugin data, not executed.

## Data retention

Default retention is one hour. Capture records use an absolute `expiresAt`. Capture jobs and reconstruction jobs expire relative to their last update. The sweeper removes expired metadata and asset directories.

Shutdown does not immediately delete unexpired data. To remove local data manually, stop the service and delete only the repository's `.jobs` directory after verifying the path.

## Production security backlog

- Remove `--no-sandbox` and use container/browser isolation.
- Authenticate every API request and bind artifacts to principals/tenants.
- Replace local files with encrypted object storage and enforce lifecycle policies.
- Use a managed secret store and provider-side key restrictions.
- Add quotas, abuse prevention, rate limiting, audit logs, and anomaly detection.
- Add dependency/container scanning and patch policy.
- Define privacy disclosures and user consent for sending page images to OpenAI.
- Review Canva terms, Figma policies, copyright, and commercial usage.
- Perform threat modeling and penetration testing before public launch.
