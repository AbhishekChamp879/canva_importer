# Canva OAuth + Public Link Trial

## Purpose

The importer now has two independent source-acquisition paths:

1. **Paste Canva link** keeps the zero-login public-viewer workflow.
2. **Connect Canva** uses Canva Connect OAuth and the official export API for designs available to the connected account.

Both paths produce the same `CapturedPage` model. Exact-image import and OpenAI editable reconstruction therefore remain shared and unchanged after capture.

OAuth improves access and source reliability; it does **not** return Canva's complete internal layer tree. Editable output still comes from structured export evidence where available, OpenAI vision analysis, deterministic reconstruction, Design IR validation, and regional fallback.

## Canva Developer Portal setup

1. Create a Canva Connect integration at the Canva Developer Portal.
2. Enable only `design:meta:read` and `design:content:read`.
3. Add this exact development redirect URL:

   ```text
   http://127.0.0.1:3000/api/canva/oauth/callback
   ```

   Canva permits `127.0.0.1` for local development but does not permit `localhost` as the registered local redirect.
4. Copy `.env.example` to `.env` and set:

   ```dotenv
   CANVA_CLIENT_ID=your-client-id
   CANVA_CLIENT_SECRET=your-client-secret
   CANVA_REDIRECT_URI=http://127.0.0.1:3000/api/canva/oauth/callback
   ```

5. Restart Flask. The health endpoint's `canvaOAuth.configured` field must be `true`.
6. Reload the development plugin, select **Connect Canva**, approve access in the browser, return to Figma, select a design, and select **Load**.

The plugin may still call Flask at `http://localhost:3000`; only Canva's registered callback must use `127.0.0.1`.

## Runtime sequence

```text
Figma UI
  → POST /api/canva/oauth/start
  ← Canva authorization URL
  → Figma main thread opens the trusted Canva URL
Browser / Canva
  → GET /api/canva/oauth/callback?code=...&state=...
Flask
  → validates one-time state and PKCE verifier
  → exchanges code using backend-only client credentials
Figma UI
  → polls /api/canva/oauth/status
  → lists /api/canva/designs
  → POST /api/canva/oauth/capture-jobs
Worker
  → reads official metadata
  → requests lossless multi-page PNG export
  → validates and normalizes every page
  → stores the standard capture
```

## Security properties

- Authorization Code with PKCE/SHA-256 and a high-entropy, one-time, ten-minute state.
- Client secret, access token, refresh token, and PKCE verifier never enter plugin messages or API responses.
- Tokens are memory-only for this local trial and disappear when Flask stops.
- Refresh-token rotation is serialized to prevent concurrent reuse.
- Disconnect clears local tokens first and then attempts remote revocation.
- Only Canva HTTPS API and export hosts are accepted; redirects are manually bounded and revalidated.
- JSON, image bytes, decoded dimensions, page count, and total capture bytes are bounded.
- OAuth and public-link jobs share the same bounded capture worker pool and cancellation flow.

## Scope and limitations

- Fixed-size designs only. Docs, whiteboards, sheets, and video designs remain outside the current converter scope.
- Canva currently limits page metadata to page numbers up to 500; this implementation follows that external platform ceiling rather than adding a smaller product-specific page limit.
- Canva's page-metadata endpoint is a preview API. Capture falls back to official export order and decoded PNG dimensions if that metadata call is unavailable.
- SVG export is not currently available through Canva's export-job API.
- Official PNG export supplies authoritative pixels and page order, not native Canva layers.
- Tokens are not durable or multi-user. A hosted product needs encrypted tenant-scoped token storage, user sessions, CSRF/session binding, durable jobs, production HTTPS redirects, and Canva integration review.
