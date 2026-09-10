# Public Canva Capture Pipeline

## Purpose

The capture subsystem turns an anonymously accessible, fixed-size Canva design into ordered logical pages containing:

- a normalized PNG at up to 2× logical resolution;
- logical width, height, orientation, and zero-based index;
- a unique server page ID;
- best-effort text and image DOM hints.

Capture is the source for both exact image import and editable reconstruction.

An optional authenticated provider supplies the same capture contract through Canva Connect OAuth and official lossless PNG export. Public-link capture remains the anonymous path. See [Canva OAuth + Public Link Trial](CANVA_OAUTH.md).

## Accepted source grammar

The URL policy accepts HTTPS only, standard port 443 only, no user information, and one of:

- `canva.link/<short-token>`;
- `canva.com/design/<design-id>`;
- `canva.com/design/<design-id>/<share-token>`;
- either design form followed by a route such as `view`, `edit`, `watch`, `present`, `play`, `preview`, or `share`;
- the same design paths under a locale prefix such as `/en_us/design/...`.

`www.canva.com` and `canva.com` are accepted for source designs. Canva subdomains are not accepted as source URLs; they are accepted only for validated Open Graph image assets.

Rejected inputs include:

- HTTP, custom ports, userinfo, lookalike domains, and arbitrary Canva pages;
- control characters, backslashes, encoded NUL/newline/slash/backslash separators;
- path or segment lengths above policy limits;
- ambiguous or excessive path segments.

Query strings, fragments, locale, source host alias, and route mode are removed from the canonical capture URL:

```text
https://www.canva.com/design/<design-id>[/<share-token>]/view
```

## Short-link resolution

`canva.link` resolution is manual rather than delegated to a redirect-following client:

1. Validate the current URL.
2. Send a bounded HTTPS GET.
3. Read only a small response prefix.
4. Accept only standard redirect status codes with a location.
5. Resolve relative locations.
6. Revalidate the next host, authority, and path.
7. Reject loops and a redirect count beyond the configured call budget.
8. Canonicalize only after a valid Canva design path is reached.

The source is canonicalized before `CaptureCoordinator.acquire()`, so concurrent `/edit`, `/view`, host-alias, locale, and short-link aliases for the same canonical target share the same duplicate key.

## Browser launch and context

`PortableBrowserRuntime` selects a browser as documented in [Configuration](CONFIGURATION.md). Capture uses:

- headless mode;
- 1,920×1,080 viewport;
- device scale factor 2;
- an isolated browser context;
- blocked `media` resources to avoid video/audio download;
- the browser runtime's native user agent and browser properties rather than a pinned Chrome-version spoof.

This is browser automation, not an official Canva API. Anti-automation challenges and viewer changes can still prevent capture.

These browser-specific limitations do not apply to the OAuth provider. OAuth has different boundaries: account authorization, Canva API availability/permissions/rate limits, supported export formats, and the current absence of a complete native layer-tree endpoint.

## Access classification

After navigation and overlay dismissal, the browser snapshot is classified:

| State | API behavior |
| --- | --- |
| fixed page visible | continue |
| loading | continue to capture attempt |
| login required | `AUTH_REQUIRED` |
| private/access denied | `SOURCE_NOT_PUBLIC` |
| CAPTCHA/security check | `SOURCE_CHALLENGED` |
| rate limited | `SOURCE_RATE_LIMITED` |
| Docs/whiteboard/video text detected | `UNSUPPORTED_SOURCE` |
| no fixed page | `CAPTURE_FAILED` or more specific fallback error |

Classification is evidence-based and can be imperfect if Canva changes wording.

## Fixed-page discovery

Generic discovery considers indexed page markers, canvases, images, image roles, and page/slide/design/viewer/render class hints. Candidate scoring favors:

- explicit page identity;
- large but not viewport-sized fixed bounds;
- canvas/image roles;
- page-related labels;
- useful viewport coverage.

It penalizes toolbar/navigation/thumbnail/menu/dialog hints and whole viewer shells. Candidates must be at least 200×200, remain within aspect ratio 0.15–6.5, and not exceed the viewport shell limits.

When a page number is known, exact identity lookup runs first:

- zero-based `data-page-id`;
- zero-based `data-page-index`;
- one-based `data-page-number`;
- one-based Page ARIA labels;
- ordered opaque `data-page-id` wrappers when the complete list is present.

An indexed marker may be expanded up to seven ancestors to find its fixed-size page container.

## Page count

The count detector takes the maximum plausible total from:

- visible “N / total” or “N of total” page indicators;
- body lines that exactly resemble a page counter;
- distinct indexed page markers;
- page/slide thumbnail list length.

Values must be between 1 and 10,000. This 10,000 check protects parsing; it is not a promise that a 10,000-page design fits capture time or byte limits.

## Multi-page strategy

Before iteration, capture distinguishes:

1. **Multi-page rendered document:** more than one full-size page root is present. Capture locates each indexed page, scrolls it into view, and waits for a stable screenshot.
2. **One-page-at-a-time viewer:** only the current page is rendered. Before page N is located, capture advances through an exact numbered page/timeline control or ArrowRight.

Generic “next page” buttons are intentionally not clicked because the user's design can contain an interactive element with the same accessible label.

## Advancement and duplicate proof

After navigation, one of these must prove advancement:

- the visible page counter equals the expected page;
- page identity matches the expected metadata;
- page identity changed from the previous full-size page;
- the current page screenshot changed.

After the normalized PNG is produced, its SHA-256 is compared with earlier pages. Equal bytes are accepted only when Canva supplies authoritative distinct page evidence: a confirmed expected page number or different page identity. Without that evidence, capture fails with `CAPTURE_DUPLICATE_PAGE` instead of silently importing page 1 repeatedly.

This rule permits intentionally duplicated Canva pages while rejecting unproven duplicate navigation.

## Stable capture and dimensions

For an identified page:

1. Scroll it into view.
2. Sample screenshots every 500 ms, up to 12 reads.
3. Return after two consecutive equal hashes, or use the latest valid result at the deadline.
4. Recover Canva's internal logical scale from fixed content bounds when available.
5. Divide browser bounds by logical scale.
6. Normalize to `min(2× logical size, 8192 maximum dimension)`.
7. Encode optimized PNG.
8. Derive portrait/landscape/square from logical dimensions, treating a ≤1% difference as square.

The source may be portrait, landscape, square, or mixed page by page.

## DOM hints

Text hint extraction reads visible nearby text elements and computed typography. It deduplicates approximate duplicates and caps the result at 200. Image hints read visible page-contained `img` elements, current source URL, natural dimensions, CSS object fit/position, and corner radius, then cap at 100. Data/blob sources and page-sized flattened viewer images are excluded.

These hints are not an authoritative Canva layer tree. They exist only to improve editable reconstruction. Exact image import ignores them.

## Open Graph fallback

If page 1 has no fixed locator, capture may fetch Canva's HTTPS Open Graph image after validating both initial and final asset hosts, content type, size, and decoded image. This supports a single public cover preview.

It is never repeated for a multi-page design. If Canva reports multiple pages but exposes only a cover, capture fails with `MULTI_PAGE_CAPTURE_UNAVAILABLE`.

## Limits

- Whole capture timeout: configurable, default 30 minutes.
- Logical page dimension: 8,192 pixels per side.
- Encoded PNG: 25 MB per page.
- Total decoded capture bytes: configurable, default 512 MB.
- Page candidate scan: at most 1,000 DOM candidates.
- Text hints: 200 per page.
- Image hints: 100 per page.
- Simultaneous captures: configurable, default 2.

There is no fixed product page-count limit. The timeout and byte limits determine practical capacity.

## What the capture does not guarantee

- Access to private or login-only designs.
- Compatibility with every future Canva viewer DOM.
- Animated timeline semantics or video frames.
- Complete or authoritative Canva layer data, fonts, masks, or effects. Trusted DOM hints may recover some original image assets, but ambiguous or canvas-rendered regions remain screenshot crops.
- Semantic proof that a byte-distinct screenshot is the correct requested page when Canva exposes no identity.
- Pixel identity to Canva export; capture represents the settled public viewer render.

For live validation procedures, see [Testing](TESTING.md).
