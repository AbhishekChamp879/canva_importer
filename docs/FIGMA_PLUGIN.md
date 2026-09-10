# Figma Plugin Architecture

## Files and responsibilities

| File | Responsibility |
| --- | --- |
| `manifest.json` | plugin identity, Figma editor support, UI/main entry points, network policy |
| `ui.html` | user interface, Flask requests, job polling, previews, selection, image streaming, summaries |
| `code.js` | Figma document mutations, native IR renderer, image import sessions, rollback |
| `tests/figma/renderer.test.js` | dependency-free Figma Plugin API simulation tests |

No bundler or npm dependency is required. The UI and runtime are plain JavaScript so the development manifest can be imported directly.

## UI workflow

1. User either pastes a public Canva URL or connects Canva and selects an authenticated design.
2. UI creates the corresponding capture job and polls every 700 ms.
3. On completion, UI fetches capture metadata and renders safe DOM-created thumbnail cards.
4. All pages are selected by default; the user may change selection.
5. User chooses exact images or editable AI.

The UI never uses untrusted `innerHTML`. Status, errors, page labels, and summaries are assigned with `textContent` or DOM node creation.

The OAuth button asks the Figma main thread to call `figma.openExternal` only after validating the exact Canva authorization URL prefix. The plugin never receives the client secret or Canva tokens. It polls the local status endpoint and populates the design picker using DOM-created `Option` nodes.

## HTTP behavior

`BACKEND_BASE` is fixed to `http://localhost:3000`. JSON API requests:

- use a 30-second AbortController deadline;
- send `application/json`;
- parse structured backend errors;
- ignore stale polling responses after job IDs change.

Capture and reconstruction can last much longer than 30 seconds because each poll is short. Full page PNG downloads use a dedicated fetch and are limited to 25 MB by the UI and renderer.

## UI ↔ main-thread message protocol

### Exact image messages

| Direction | Type | Important fields |
| --- | --- | --- |
| UI → main | `begin-page-image-import` | session ID, capture ID, title, selected page metadata |
| main → UI | `page-image-import-ready` | session ID |
| UI → main | `append-page-image` | session ID, page ID, Uint8Array |
| main → UI | `page-image-import-page` | session ID, page ID |
| UI → main | `finish-page-image-import` | session ID |
| UI → main | `cancel-page-image-import` | session ID |
| main → UI | `import-completed` | session ID, report |
| main → UI | `import-failed` | session ID, message |
| UI → main | `open-external` | validated Canva authorization URL |

The UI permits one outstanding page acknowledgement. It downloads and imports pages sequentially and waits up to 60 seconds for each main-thread acknowledgement.

### Editable messages

| Direction | Type | Important fields |
| --- | --- | --- |
| UI → main | `import-design` | completed `DesignDocumentV1`, reconstruction job ID |
| main → UI | `figma-qa-export` | job/page IDs, logical dimensions, exported PNG `Uint8Array` |
| main → UI | `import-completed` | editable report |
| main → UI | `import-failed` | message |

The editable UI has a five-minute renderer watchdog. It restores controls and reports a timeout if the main thread never responds.

## Exact image transaction

`beginPageImageImport()` validates:

- nonempty page list;
- finite positive logical dimensions ≤8,192;
- orientation consistency;
- unique page IDs;
- unique session ID.

It calculates a centered grid with up to five columns. `appendPageImage()` accepts each expected page once, validates byte length, creates a Figma image, frame, and full-frame rectangle, and stores metadata.

`finishPageImageImport()` succeeds only after every selected page ID was imported. Any message error with a session ID calls cancellation, which removes all frames created in the session.

Frame plugin data:

- `canva-importer.capture-id`;
- `canva-importer.page-id`;
- `canva-importer.orientation`;
- `canva-importer.mode=page-image`.

Image layer plugin data:

- `canva-importer.page-id`;
- `canva-importer.source=captured-page-full-resolution`.

## Editable renderer

`renderDocument()` validates the top-level schema version/pages/assets before rendering. Full validation has already occurred in Python; the JavaScript check protects against missing fundamental structure.

Pages are rendered transactionally:

1. Create a frame at a distinct grid position.
2. Apply logical dimensions and page background.
3. Store document/page/orientation/metric plugin data.
4. Sort root nodes by `zIndex`.
5. Recursively create native nodes.
6. Add a hidden locked QA reference.
7. Export each completed frame at 1× for final-Figma QA when a reconstruction job ID is present.
8. Select and zoom to all created page frames.

If a fatal error escapes, all newly created frames are removed.

## IR-to-Figma mapping

| IR node | Figma output |
| --- | --- |
| group | transparent Figma frame with optional rectangular clipping |
| text | Figma text node with range formatting |
| image | rectangle with image fill |
| rectangle | rectangle with solid/image/gradient fills, stroke, radius, shadow, and blur |
| ellipse | ellipse with solid/image/gradient fills, stroke, shadow, and blur |
| vector | `createNodeFromSvg` result |
| raster-fallback | image rectangle plus optional warning label |

Geometry, rotation, opacity, visibility, lock state, name, source identity, confidence, extraction, and z-index are applied or retained as plugin data.

## Font behavior

Each text run requests its normalized family/style with `figma.loadFontAsync`. Failure triggers a deterministic candidate sequence:

1. Compatible style aliases such as `Semi Bold`/`Semibold`.
2. Same-family Regular or Italic.
3. Inter with the requested style.
4. Inter Regular.

The report records both the missing requested font and the exact substitution. Font substitution preserves editability but can still change line breaks and visual metrics.

## Node-level failure behavior

Failures creating a vector, image, or other individual IR node do not necessarily abort the page. The renderer creates a labeled warning rectangle where possible, stores `canva-importer.render-error`, increments `failedNodes`, and continues.

Fatal page/document errors trigger whole-import rollback. This distinction preserves maximum useful output without pretending failed nodes succeeded.

## Hidden QA reference

Editable pages contain `QA Reference · hidden`:

- same dimensions as the page;
- full captured screenshot as an image fill;
- hidden and locked;
- `canva-importer.qa-reference=true`.

It is never a visible background beneath editable text. Users may reveal it manually for comparison.

## Editable report

The renderer reports:

- page count;
- raster fallback count;
- failed node count;
- missing fonts;
- exact font substitutions;
- native gradient and effect counts;
- warnings;
- area-weighted native/fallback coverage;
- area-weighted text, visual similarity, pixel difference, and missing-region metrics;
- total duplicate text blocks.

The UI displays the summary and up to eight individual warnings.

## Final-Figma pixel QA

Editable imports carry the reconstruction job ID into the renderer. After each frame is complete, `code.js` calls Figma `exportAsync` with PNG, 1× scale, and sRGB. The hidden QA reference is invisible and therefore is not part of the export. Export bytes are capped at 25 MB and sent to the UI.

The UI decodes both the Figma export and the full captured reference, verifies exact logical export dimensions, downsamples both uniformly to at most 2,048 pixels on the longest side, and computes mean RGB difference plus a materially-different-pixel rate. It also hashes the original exported PNG. When all selected pages are compared, the UI submits a complete page set to the reconstruction job's `/figma-qa` endpoint and displays the final similarity, Phase 1 pass/fail result, and QA job ID needed by the corpus collector.

If any export, decode, reference fetch, comparison, or submission fails, the editable import remains available but its report contains an explicit warning and no Phase 1 pass claim.

## Development constraints

- Figma Desktop is required for real document testing.
- The isolated Figma API simulation validates message/export control flow but cannot establish real font metrics, actual SVG rendering, image decoding fidelity, or real Figma export pixels.
- Production network access is `none`; only the development localhost domain is allowed.
- Any backend port/domain change requires both UI and manifest changes.
- The plugin currently supports the Figma design editor only.
