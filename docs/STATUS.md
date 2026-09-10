# Current Project Status

Status date: 29 August 2026

## Executive assessment

The project is a **local MVP for public Canva-to-Figma page-image import**, a **local trial for Canva OAuth official page export**, and a **functional alpha for editable AI reconstruction**.

The exact-image path is end-to-end: URL submission, asynchronous browser capture, multi-page previews, page selection, full-resolution transfer, Figma frame creation, cancellation, and rollback are implemented. The editable path is also wired end-to-end, but its fidelity is not yet validated broadly enough to call it production-ready or Codia-equivalent.

No implementation can guarantee that every arbitrary Canva link works through anonymous browser capture. Private/login-only sources, access challenges, deleted links, unsupported document types, and Canva UI changes are real external boundaries. The code handles these as explicit errors rather than claiming universal access.

## Capability scorecard

Percentages express engineering maturity for the declared local scope, not lines of code.

| Area | Maturity | Evidence | Remaining work |
| --- | ---: | --- | --- |
| Flask/service foundation | 95% | validated settings, structured errors, lifecycle, limits, tests | production WSGI/deployment, observability |
| Design IR V1 | 97% | strict Pydantic model, gradients/effects, generated schema, cross-reference tests | version negotiation/migrations, advanced blend/effect vocabulary |
| Canva URL policy | 92% | common routes, localization, short-link redirects, canonicalization tests | continuous compatibility monitoring |
| Public Canva capture | 82% | real browser path, multi-page identity/navigation guards, live structural validation | broader live matrix and visual page-to-thumbnail proof |
| Canva OAuth acquisition | 70% | PKCE, memory-only tokens, design picker, official multipage PNG export, shared capture pipeline | real integration credentials/live acceptance, hosted tenant token storage, Canva review |
| Exact page-image Figma import | 92% | preview/select/stream/import/rollback and isolated Figma API tests | manual Figma acceptance matrix, very-large-deck UX |
| Editable reconstruction | 84% | OpenAI Vision OCR/layout, original assets, typography normalization, geometry hierarchy, gradients/effects, safe vectors | complete-corpus Figma acceptance, complex masks/effects, hierarchy calibration |
| Visual QA | 75% | backend metrics plus automatic real-Figma frame export, pixel comparison, persisted threshold report, and corpus collector | run/calibrate the five owned real designs and commit passing evidence |
| Local beta hardening | 75% | cancellation, timeouts, bounded workers, TTL storage, limits | restart recovery, logging/metrics, soak and failure testing |
| Hosted/production product | 15% | async contracts and replaceable boundaries | auth, isolation, durable queue/storage, quotas, deployment |

## What is implemented

### 1. Foundation and API

- Python/Flask application factory and explicit local entry point.
- Loopback-only bind validation.
- Environment validation with bounded integers, explicit booleans, and validated OpenAI model IDs.
- JSON errors for malformed JSON, validation, unknown routes, wrong methods, media type, oversized body, service errors, and unexpected exceptions.
- Error sanitization that bounds messages and redacts OpenAI keys, common credential fields, Canva short links, and Canva share tokens.
- CORS for Figma HTTPS origins, loopback origins, no-origin/null plugin contexts; security response headers.
- Health endpoint exposing provider/browser readiness and runtime limits.
- Bounded capture and reconstruction worker pools.
- Idempotent service shutdown and cooperative job cancellation.

### 2. Design IR V1

- Node types: group, text, image, rectangle, ellipse, vector, raster fallback.
- Geometry, rotation, opacity, visibility, lock state, hierarchy, z-order, fills, strokes, corner radius, confidence, and source metadata.
- Text runs with font, size, color, spacing, line height, and decoration.
- Page orientation derived from dimensions.
- QA reference and conversion metrics on every editable page.
- Unique page/node IDs, asset reference validation, complete contiguous text-run coverage, group-only children, required node fields, node-count limits, and finite numeric validation.
- Image MIME/signature matching and exact one-of embedded data or URL.
- SVG XML validation and rejection of scripts, event handlers, external references, and active content.
- Generated JSON Schema with a drift test.

### 3. Public Canva acquisition

- Accepts trusted Canva design/share routes and `canva.link`; rejects lookalike hosts, credentials, non-HTTPS/custom-port sources, encoded separators, invalid paths, and excessive lengths.
- Canonicalizes equivalent source forms before duplicate/concurrency coordination.
- Revalidates every short-link redirect and detects loops/budget exhaustion.
- Portable Playwright browser selection with optional override, system channel discovery, and automatic managed Chromium installation.
- Isolated 1,920×1,080 context at device scale factor 2; media resources are blocked.
- Best-effort overlay dismissal and navigation stabilization.
- Access-state classification for login, private, challenge, rate limit, unsupported, and missing fixed-page states.
- Page-count detection from page indicators, indexed metadata, and thumbnail/page-list structures.
- Separate behavior for multi-page rendered canvases and one-page slideshow viewers.
- Indexed page lookup and explicit page identity.
- Navigation through exact numbered controls or ArrowRight, followed by page-number, identity, or visual-change proof.
- Duplicate screenshot rejection unless distinct Canva page identity is authoritative.
- Stable screenshots, logical-scale recovery, up-to-2× normalized PNGs, mixed orientation, text hints, and image hints.
- Open Graph preview only as a single-page fallback; never repeated to fabricate a multi-page result.
- No small fixed page-count cap such as 10 or 18; the detector has a 10,000-page parsing safety ceiling and resource/time limits still apply.

### 3A. Canva OAuth acquisition trial

- Authorization Code + PKCE/SHA-256 with one-time ten-minute state.
- Optional Canva account connection alongside the unchanged public-link workflow.
- Backend-only client secret and memory-only rotating user tokens.
- Authenticated design search/selection in the Figma plugin.
- Official lossless PNG export with one ordered file per page.
- Fixed-size metadata, mixed-orientation preservation, strict Canva export URL validation, image normalization, and capture limits.
- OAuth pages enter the same preview, page selection, exact image, editable reconstruction, Design IR, renderer, and QA paths.
- Does not claim access to Canva's native layer tree; editable construction remains vision/IR based.

### 4. Artifact storage and jobs

- Capture PNGs and editable assets stored as files under `.jobs`.
- Persisted JSON stores metadata/file references, avoiding huge base64 job records.
- Atomic metadata writes and incomplete-capture cleanup.
- TTL cleanup thread and on-access sweeps.
- Capture and reconstruction status/progress polling.
- Capture and reconstruction cancellation endpoints.
- Compact JPEG previews and separate conditional full-resolution PNG endpoints with ETags/private caching.
- Completed editable results expose expiring asset URLs.

### 5. Exact Figma import

- Page thumbnail preview, selection, select-all/clear-all.
- Sequential full-resolution page download and per-page Figma acknowledgement.
- One logical-size frame and full-frame image rectangle per selected page.
- Portrait, landscape, square, and mixed dimensions retained per frame.
- Responsive grid with distinct positions.
- Session validation, unique page IDs, per-image 25 MB limit.
- Cancellation/fatal-error rollback of every frame created in the session.
- Capture ID, page ID, orientation, mode, and source metadata stored as plugin data.

### 6. Editable reconstruction

- Replaceable OCR/layout protocols.
- OpenAI Responses API image input for OCR and layout, each with a strict JSON Schema output contract and one malformed-response retry.
- OCR receives the decoded source-pixel dimensions and returns bounded source-pixel text boxes, exact text candidates, reading order, and model-estimated confidence.
- Provider timeout: 90 seconds per OpenAI attempt.
- OCR coordinate scaling, deduplication, punctuation-aware line grouping, and OCR/model matching.
- DOM hint matching for family, weight, italic style, size, color, spacing, line height, alignment, and decoration.
- Deterministic CSS font-stack normalization, bounded font metrics, and explicit Figma substitution reporting.
- Bounds clipping, ID uniquification, invalid-parent/cycle repair, deterministic z-order.
- Conservative geometry-based parent inference for confident multi-element containers.
- Source-pixel background/shape color sampling.
- Image/unsupported crops and low-confidence regional raster fallbacks.
- Deterministic DOM-image/layout matching and trusted original Canva asset recovery.
- Canva-only asset redirects, MIME/signature/decoded-size validation, modern-format PNG normalization, byte and pixel limits.
- Backend-only signed asset URLs; model image hints contain geometry/style evidence without URL tokens.
- Backward-compatible rectangular group clipping and validated image crop-transform IR.
- Explicit crop, unavailable-original, and unsupported-mask fallback reasons.
- Native two-stop gradients, drop shadows, layer blur, and safe compound SVG primitives; incomplete/complex evidence remains a labelled regional fallback.
- Suppression of OCR text inside raster-owned regions to avoid visible duplicates.
- Per-page failure converted to a full-page fallback.

### 7. Native Figma renderer and QA

- Native frames, groups, editable text, image rectangles, rectangles, ellipses, SVG vectors, and raster fallback layers.
- Original-image, screenshot-crop, and clipped-group counts in the conversion report.
- Font loading with Inter Regular substitution and missing-font reporting.
- Compatible style/same-family font fallback attempts plus exact substitution reporting.
- Native gradient, shadow, blur, and safe compound-vector rendering with report counts.
- Failed nodes become labeled warning rectangles with render-error plugin data.
- Hidden, locked, complete `QA Reference` frame on editable pages.
- Page/node Canva and IR metadata preserved.
- Transactional page-set rollback on fatal renderer failure.
- Backend approximation of solid/image backgrounds, images, fallback crops, rectangles, and ellipses.
- Native/fallback coverage, text score, pixel difference, visual similarity, missing-region estimate, and duplicate-text metrics.
- Area-weighted conversion summary shown in the plugin and metrics stored on page frames.
- Automatic 1× PNG export of every completed editable Figma frame.
- Browser-side comparison of actual Figma pixels with the full captured reference, including export hash, mean RGB difference, similarity, and mismatch rate.
- Persisted page-level `/figma-qa` acceptance reports combining final-Figma and backend quality metrics.
- Validated five-category Phase 1 manifest/report collector that keeps Canva source URLs out of checked-in evidence.

## Verification evidence

Current repository checks:

- 113 Python tests pass, including OAuth PKCE/state and official-export capture contracts, plus document-global IDs, original-asset recovery/security, typography, hierarchy, gradients/effects, safe vectors, crop fallback, clipping, and reconstruction contracts.
- The committed Design IR schema matches the Pydantic source model.
- `figma-plugin/code.js` parses with Node.
- The embedded UI script parses.
- Figma renderer tests cover all IR node types, mixed-orientation page-image imports, unique frame placement, metadata, warning behavior, and cancellation rollback.
- Python compilation and dependency consistency checks pass in the current environment.

Live evidence gathered during development:

- Multiple real user-provided Canva URL forms resolved to canonical trusted `/view` URLs.
- A fresh direct-edit design completed through the real asynchronous Flask capture API with 40 ordered page identities; all 40 image endpoints passed PNG MIME, byte, decoded-dimension, orientation, ID, and index validation.
- That 40-page run produced 27 distinct byte hashes. Equal hashes are not automatically an error because Canva can contain intentionally identical pages; authoritative page identities allow them.
- On 28 August 2026, all five user-supplied public evaluation sources completed fresh capture through the real Flask/Playwright path: 40, 13, 18, 10, and 25 pages (106 total). The runs preserved four 1920×1080 landscape designs and one 794×1123 portrait design, and none triggered repeated-first-page, incomplete, or unproven-advancement errors.
- The first evaluation source contained four duplicate PNG hash groups with two pages each. Their distinct Canva page identities allowed intentional duplicates while the same guard continued rejecting unproven repeated captures.
- On 29 August 2026, a fresh real reconstruction of one image-bearing page from the owned flyer source completed through Flask, Playwright, the asset-security boundary, and the configured OpenAI providers. It embedded one validated 1600×1067 Canva-hosted PNG as an original asset and deliberately retained one uncertain region as a screenshot crop. The sanitized result is `evaluation/phase2-asset-report.json`.
- The live run exposed a partially off-page DOM image hint; capture now excludes such hints so ambiguous viewer/chrome imagery cannot be promoted to an original design layer.

What that live evidence does **not** prove:

- It does not prove every one of those 40 PNGs visually matches its corresponding Canva thumbnail.
- It does not prove every public Canva viewer variant works after future Canva DOM changes.
- It does not establish editable fidelity targets across posters, decks, flyers, and social designs.
- It does not provide a five-category final-Figma baseline yet. The owned corpus and capture baseline exist, but all five designs have not completed editable plugin import and final-Figma QA.
- It does not yet prove rectangular clipping and crop transforms in an actual Figma document; those paths pass deterministic renderer/QA tests but still need manual Figma acceptance.

Use `python -m scripts.live_validate` for fresh structural capture checks. The repository stores no recorded Canva response or screenshot fixtures; production capture/reconstruction never reads test data.

## Known limitations and engineering risks

### Capture compatibility

- Page discovery and navigation are still coupled to observable Canva DOM/accessibility structures.
- Page count is heuristic; a Canva UI change can undercount or overcount.
- Visual-change proof can distinguish frames without identifying semantic page number when Canva exposes no identity.
- Intentionally identical Canva pages cannot be distinguished by bytes; distinct platform identity is the proof.
- Animations are captured as settled static frames. Video is intentionally unsupported and network media is blocked.
- Open Graph fallback supports one page only.

### Editable fidelity

- OpenAI Vision OCR returns schema-constrained, model-estimated text boxes and confidence; unlike a dedicated deterministic OCR engine, exact transcription and pixel localization must be measured on the owned evaluation corpus.
- Editable reconstruction currently makes separate OCR and layout image-analysis calls, increasing per-page latency and API cost.
- Original assets are used only when Canva exposes a trusted DOM image URL and deterministic geometry/fit checks pass; canvas-rendered and ambiguous images remain screenshot crops.
- Rectangular clipped groups, two-stop gradients, ordinary drop shadows, layer blur, and safe compound SVG primitives are modeled. Non-rectangular masks, clipping paths, multi-stop/mesh gradients, blends, advanced effects, embedded SVG images/text, and filters remain unsupported.
- Geometry can repair omitted group membership conservatively, but semantic hierarchy and Auto Layout still depend on stronger evidence and final-Figma calibration.
- OCR text can be exact while line wrapping, font metrics, and typography still differ in Figma.
- Missing fonts are substituted, affecting layout.
- Native coverage is area-based, not a semantic editability score.

### Local runtime

- The Flask development server is used; no production WSGI server is configured.
- Jobs are thread-pool tasks in one process. They are persisted for inspection but are not resumed after restart.
- No central structured logging, metrics, tracing, alerting, or audit trail.
- The local API has no authentication. Loopback binding and CORS reduce browser exposure but do not isolate it from other local processes.
- The plugin base URL is compiled as `http://localhost:3000`. Changing `PORT` alone breaks the plugin until `ui.html` and `manifest.json` are updated.
- Automatic browser installation requires outbound access and writable Playwright browser storage. Linux may need system packages installed separately.

### Test coverage

- Deterministic acquisition tests exercise stored DOM metadata, not a live Canva contract.
- Provider tests isolate HTTP calls; normal CI makes no paid OpenAI calls.
- There is no unattended Figma Desktop launcher/harness. When the plugin runs in Figma, final frame export and comparison are automatic.
- The five-design local corpus and capture metadata are established in `docs/evaluation/phase1-capture-report.json`. OpenAI Vision OCR and layout report configured when `OPENAI_API_KEY` is present, but editable Figma imports and `/figma-qa` evidence have not run yet.
- No load, soak, crash-recovery, disk-full, or long-running large-deck benchmark suite exists.
- Live validation now uses the same uniform up-to-2× image scaling rule as production capture, including pages that hit the 8,192-pixel maximum on one axis.

## Definition of “done” by mode

### Exact image local MVP

Implemented, subject to source accessibility and external Canva compatibility:

- all detected pages shown before import;
- selected pages stream as valid full-resolution images;
- page size/orientation/order are retained;
- no silent partial or unproven duplicate capture;
- cancellation/failure does not leave a partial Figma import.

### Editable AI product

Not yet done at Codia-like quality. Completion requires:

1. A curated, repeatable real-design evaluation set.
2. Final Figma render/export comparison against Canva references.
3. Measured acceptance thresholds per design category.
4. Better original asset recovery, masks/effects, hierarchy, vectors, and typography.
5. Production job/storage/auth/observability architecture if shipped beyond local development.

## Recommended next sequence

1. Supply the five-category owned live evaluation corpus without adding runtime link hardcoding.
2. Run the documented automatic export/compare protocol for all five cases.
3. Commit the sanitized passing baseline and classify the largest measured fidelity failures.
4. Improve original image recovery and clipping/mask representation.
5. Improve typography and hierarchy deterministically before adding more model complexity.
6. Only then design hosted deployment, authentication, persistence, quotas, and billing.
