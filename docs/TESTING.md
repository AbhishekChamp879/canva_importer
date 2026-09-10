# Testing and Quality Strategy

## Test layers

The project intentionally separates deterministic tests from live external validation.

| Layer | Command | What it proves |
| --- | --- | --- |
| Python contracts/unit integration | `python -m unittest discover -s tests -v` | validation, routes, jobs, storage, acquisition helpers, providers, reconstruction, QA |
| Schema drift | included in Python suite; regenerate with `python -m scripts.export_schema` | committed JSON Schema matches Pydantic |
| Figma runtime syntax | `node --check figma-plugin/code.js` | main-thread JS parses |
| Figma API simulation | `node tests/figma/renderer.test.js` | renderer mappings, reports, metadata, sessions, rollback |
| UI embedded script parse | test suite/extraction check | plugin UI JavaScript parses |
| Live capture | `python -m scripts.live_validate <URL...>` | real Flask job + Playwright + Canva + PNG endpoints |
| Manual Figma acceptance | documented below | actual Figma node behavior and visible result |

At the status date, 113 Python tests pass and the Figma renderer tests pass.

## Python test inventory

- `test_foundation.py`: environment parsing and public error redaction.
- `test_url_policy.py`: supported routes, canonicalization, hostile inputs, redirects.
- `test_browser_runtime.py`: managed browser, system channels, auto-install, override failures.
- `test_acquisition.py`: candidate scoring, identity proof, page count, navigation helpers, logical scaling, normalization, access states.
- `test_asset_fetch.py`: trusted Canva hosts/redirects, MIME/signature validation, response limits, and hostile URL rejection.
- `test_api.py`: health, structured errors, CORS, capture/image contracts, async jobs, reconstruction through HTTP.
- `test_canva_oauth.py`: PKCE/state secrecy, one-time callback validation, token exchange boundary, and ordered mixed-orientation official export capture.
- `test_store.py`: file-backed capture persistence and total-byte limits.
- `test_jobs.py`: bounded reconstruction capacity.
- `test_design_ir.py`: valid/invalid IR, references, runs, finite geometry, gradients/effects, safe compound SVGs, image safety, page/node/asset limits, schema drift.
- `test_providers.py`: OpenAI Vision OCR and layout requests, strict structured-output retry, and refusal handling.
- `test_reconstruction.py`: normalization, OCR/layout dedupe, font metrics, geometry hierarchy, gradients/effects, safe-vector fallback, colors, coordinates, original assets, clipping, labelled fallbacks, and duplicate suppression.
- `test_qa.py`: visual similarity, missing regions, duplicate text.
- `test_figma_plugin.py`: manifest, network restriction, safe UI, full-resolution path, IR node coverage.
- `test_phase1_report.py`: owned-corpus manifest rules, five-job mapping, sanitized evidence, and acceptance failure behavior.

## Test isolation

Deterministic tests isolate browser, provider, Flask, storage, and Figma API boundaries so normal CI is fast, repeatable, and does not consume OpenAI quota. No recorded Canva response or screenshot fixture is stored in the repository.

Critical rule: production modules never read test data or recorded provider responses. Every user-provided URL runs through live URL resolution and Playwright capture.

## Live validator

Start the backend, then:

```powershell
python -m scripts.live_validate "https://www.canva.com/design/DESIGN_ID/SHARE_TOKEN/view"
```

Multiple URLs are accepted. Optional flags:

```powershell
python -m scripts.live_validate --base-url http://127.0.0.1:3000 --deadline-seconds 1800 <URL1> <URL2>
```

It verifies:

- capture job creation and terminal success;
- nonempty capture;
- contiguous zero-based indices;
- unique page IDs;
- orientation matches logical dimensions;
- trusted capture-scoped image paths;
- PNG MIME and nonempty ≤25 MB bytes;
- decoded PNG dimensions match up-to-2× logical dimensions;
- per-page SHA-256 and reported unique hash count.

It does not verify:

- the screenshot visually matches the corresponding Canva thumbnail;
- intentionally equal pages versus semantically wrong equal pages;
- editable reconstruction or external AI providers;
- final Figma rendering;
- font fidelity.

The validator uses the same uniform up-to-2× scale as production capture, including extreme pages that hit the 8,192-pixel maximum on one axis.

## Manual exact-image acceptance

For each chosen public design:

1. Record source type and expected page count.
2. Open the Canva public viewer in a separate browser.
3. Load pages in the plugin.
4. Compare every thumbnail in order, not only first/last.
5. Confirm page count, order, dimensions, and orientations.
6. Import every page as exact images.
7. Confirm one frame and one image layer per selected page.
8. Compare representative pages at 100% zoom.
9. Cancel a second large import mid-transfer and confirm no partial frames remain.
10. Save results without checking source tokens into the repository.

Use at least:

- single-page poster;
- portrait social/flyer;
- landscape presentation;
- square social design;
- mixed-orientation set if Canva permits it;
- one-page-at-a-time viewer;
- multi-page rendered document;
- intentionally duplicated pages.

## Manual editable acceptance

For each curated design:

1. Run editable reconstruction with a known OpenAI model/key configuration.
2. Record backend metrics and warnings.
3. Confirm text is editable and exact.
4. Inspect line wrapping, font substitution, alignment, rotation, and spacing.
5. Confirm images are separate fills and basic shapes are native.
6. Reveal the hidden QA reference and compare alignment.
7. Confirm no visible full-page screenshot duplicates editable text.
8. Confirm unsupported regions are present as localized fallbacks with reasons.
9. Confirm the plugin reports final Figma visual similarity and Phase 1 page QA after its automatic 1× frame export.
10. Retrieve `/api/reconstruction-jobs/{jobId}/figma-qa` and collect all five categories with `python -m scripts.phase1_report`.

Initial roadmap targets remain evaluation goals, not current guarantees:

- ≥95% exact OCR text;
- ≥80% ordinary-design visible area represented natively;
- ≥95% visual similarity on supported curated designs;
- zero missing visible regions;
- zero duplicate visible text.

## CI recommendation

Run on every change:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q canva_converter scripts tests run.py
python -m pip check
node --check figma-plugin/code.js
node tests/figma/renderer.test.js
```

Do not put live Canva or paid OpenAI calls in normal CI. Schedule separate controlled compatibility checks using public test designs owned for that purpose; never hardcode user share tokens into runtime code.

## Missing quality infrastructure

- Curated five-category real-design corpus with stable ownership.
- Automated capture thumbnail-to-page visual correspondence.
- Unattended Figma Desktop launch/import orchestration.
- Figma Desktop still must be opened by a person, although export and actual-Figma pixel comparison inside the running plugin are automatic.
- Cross-platform browser matrix.
- Large-deck performance, disk-full, restart, and cancellation soak tests.
- Provider model/version regression dashboard.
