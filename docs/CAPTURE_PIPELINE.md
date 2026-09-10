# Capture pipeline

## Public sources

Supported inputs are HTTPS Canva design/share routes and canva.link URLs. Canonicalization normalizes design routes to the public viewer. Source URLs reject untrusted hosts, credentials, custom ports, malformed paths, and encoded separators. Short-link redirects are validated one hop at a time.

Playwright opens an isolated browser context, dismisses common overlays, and classifies access state. Fixed-page candidate scoring considers page identity and visible geometry. Public capture supports both multiple rendered pages and one-page slideshow viewers.

Page advancement must be demonstrated by page number, authoritative identity, or visual change. Duplicate screenshots require distinct authoritative page identity. When the remaining pages cannot be established, capture fails instead of returning a fabricated deck.

Screenshots are normalized at up to 2× logical resolution, capped at 8,192 pixels per axis. A single-page Open Graph preview is a fallback when the page itself cannot be located. It is not repeated to manufacture multiple pages.

## OAuth sources

The Canva connection uses official per-page lossless PNG exports. Fixed-size designs accessible to the connected account use the same preview and image-import pipeline. Documents, whiteboards, sheets, and videos are outside the supported scope. OAuth designs have a 500-page safety limit.

## Limits and failures

Individual images are limited to 25 MiB; captures default to 512 MiB total. Public capture has a configurable deadline and bounded concurrency. Cancellation is cooperative.

Login-only pages, deleted links, private access, rate limiting, challenges, unsupported document types, and Canva viewer changes may prevent public capture. Structural validation does not prove every screenshot visually matches its intended Canva page; manual page-by-page checking remains useful.
