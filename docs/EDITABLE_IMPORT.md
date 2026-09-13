# Editable PDF import

The plugin converts one selected Canva page at a time. Existing public-link capture and page-image import remain available. An official PDF export requires Connect Canva and permission to export the selected design; a public link alone does not grant API access.

## Conversion and appearance

The backend resolves the design from its stored capture URL, requests the selected page as a regular-quality PDF, polls the export, and downloads it without exposing signed URLs or credentials to Figma. PDFium runs in a separate subprocess.

The version-1 scene contains ordered text, vector and image objects, page geometry, font requirements, asset references, and explicit fallback reasons. Figma builds a temporary frame and exports its preview; final import moves that frame into view. Cancellation and fatal rendering errors remove the draft. The plugin removes its abandoned draft frames on the current page when another editable session begins.

Supported conversions include ordinary Latin text runs with matching fonts, solid paths, simple strokes, transformed paths, independent embedded images, and simple nested forms. PDF crop boxes and page rotation are respected. Text-run boundaries are retained rather than reconstructing Canva paragraphs.

Complex text shaping, rotated/skewed text, clipping, complex strokes, transparency groups and unsupported effects use raster fallbacks. Text already outlined in the PDF may become editable vectors; it cannot become editable characters without reliable text data. Missing fonts use the rendered text crop rather than guessing glyph outlines. Recoverable text and font identity are retained as node metadata.

The worker compares composited isolated-object renderings with its reference render. Backdrop-dependent effects that fail this check cause whole-page fallback, avoiding duplicated objects over a full screenshot. This check validates separation, not native Figma font fidelity. Native text layout can differ from PDF ink positions; large width differences trigger fallback for automatic matches, while explicit substitutions remain user-reviewed. Always compare the actual Figma preview before importing.

Google Fonts are matched through the fonts available in the user's Figma editor. Downloading a font into the backend or plugin UI does not register it as a native Figma font. Family/style normalization and PDF subset-name handling precede matching. Replacement fonts are optional and are never silently applied from an AI suggestion.

## Optional AI font suggestions

Set `OPENAI_API_KEY` in the backend environment or existing `.env` and restart. `FONT_AI_MODEL` defaults to `gpt-4.1-mini`. Missing configuration only disables the suggestion buttons.

The user must click **Suggest matching fonts** beside a detected font. This sends a resized crop of that text and its font metadata/recoverable text to the OpenAI Responses API, with no tools and a strict candidate schema. It does not send the PDF, Canva link, token, or complete design. Text visible in the crop is untrusted evidence, never model instructions.

At most three candidates are returned. Available candidates are rendered in Figma for visual comparison; unavailable fonts are labeled. Suggestions are approximate and require selection plus an updated page preview. Nonrecoverable text is not invented. Normal conversions never call AI.

The request uses `store: false`; this is not a claim of zero provider retention. Applicable API data policies still apply. API use may incur charges. Missing keys, refusals, quota errors, model access failures, invalid responses and timeouts do not invalidate the PDF scene. An ambiguously completed billable request is not automatically retried. Identical font requests are cached for the conversion's lifetime.

## Limits and lifecycle

| Resource | Default |
| --- | --- |
| Editable jobs | One active conversion |
| Selected pages | One |
| PDF download | 50 MiB |
| Overall deadline | 300 seconds, checked between network stages |
| Extraction subprocess | 60 seconds |
| Worker memory | Terminated when monitored RSS exceeds 1 GiB |
| Object count / nesting | 10,000 / 32 |
| Individual render | 32 megapixels; maximum 8192 pixels per dimension |
| Asset size / aggregate | 25 MiB / 512 MiB |
| Scene JSON | Existing MAX_API_RESPONSE_BYTES, default 64 MiB |
| Plugin asset chunks | 512 KiB |
| Plugin scene chunks | 131,072 characters |
| AI | One active request, 45-second socket timeout, ten requests per job |
| Artifacts | Existing ARTIFACT_TTL_SECONDS, default one hour |

Cancellation is checked during export polling, download chunks and subprocess execution. A blocked network read can take up to its socket timeout to return. Cancelling a suggestion cannot recall a crop already transmitted or guarantee avoiding its charge; stale responses are ignored by the UI.

Source PDFs are removed when the conversion worker finishes. Failed/cancelled conversions discard generated assets. Completed scenes and font-suggestion caches expire together; active conversions and active suggestion calls are protected from sweeping. Backend restart marks interrupted jobs failed; Canva user tokens remain memory-only, so reconnect after restart.

## Acceptance status

Automated tests cover actual PDFium extraction and subprocess execution, dimensions, text/vector/image order, clipping, page/text rotation, nested forms, backdrop blending, corrupt input, job cancellation, artifact scoping/expiry/restart, and AI request validation/cache/quota. JavaScript tests cover both import flows, native-node creation through a Figma mock, fonts, preview lifecycle, chunks, duplicate commit protection and rollback.

Mock Figma tests do not establish production Figma rendering fidelity. Live Canva OAuth export, actual Figma font positioning, manual edits, and a billable cloud suggestion require separately recorded live acceptance. Until that is completed, treat editable import as an initial implementation with conservative fallbacks.
