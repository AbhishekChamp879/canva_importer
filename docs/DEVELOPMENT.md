# Development and Extension Guide

## Engineering principles

- Treat production code, models, and tests as the source of truth; update documentation with behavioral changes.
- Do not hardcode Canva links, design IDs, tokens, page counts, or page-specific selectors.
- Do not use fixtures or recorded results in runtime acquisition.
- Fail closed on unproven multi-page navigation rather than returning repeated pages.
- Keep capture, reconstruction, IR, and Figma rendering independent.
- Validate data at every trust boundary.
- Preserve visible content through raster fallback when native conversion is uncertain.

## Local development loop

```powershell
.\.venv\Scripts\Activate.ps1
python -m unittest discover -s tests -v
python run.py
```

After changing plugin files, close/reopen or reload the development plugin in Figma. The project has no bundler step.

## Adding an API endpoint

1. Add/extend request and response models in `models.py`.
2. Add the route in `routes.py`.
3. Use `bounded_json` for potentially large JSON.
4. Return `ServiceError` or `error_response` with a stable code.
5. Validate UUID path IDs before store access.
6. Add OPTIONS behavior if called by the plugin.
7. Add tests for success, validation, expiry/not-found, wrong method, and CORS as applicable.
8. Document the contract in `API.md`.

## Adding a capture source

Do not broaden `url_policy.py` to unrelated hosts. A new authenticated/export source should implement its own acquisition provider and produce `CapturedPage` records with the same invariants.

Steps:

1. Define source-specific URL/auth validation.
2. Implement acquisition without importing Flask.
3. Preserve logical dimensions and page order.
4. Enforce byte/dimension/time limits.
5. Emit explicit access/support errors.
6. Register provider selection in `ServiceContainer` or a source router.
7. Add deterministic and live compatibility tests.

## Adding an OCR or layout provider

Implement:

```python
class OcrProvider(Protocol):
    def detect(self, image_base64: str, image_width: int, image_height: int) -> OcrResult: ...

class LayoutProvider(Protocol):
    def analyze(
        self,
        image_base64: str,
        width: int,
        height: int,
        ocr: OcrResult,
        text_hints: list[dict],
        image_hints: list[dict],
    ) -> ReconstructedPage: ...
```

Provider output must use the existing models. Keep credentials in backend settings. Add explicit deadlines, sanitize failures, and isolate HTTP in deterministic tests.

## Adding an IR node or property

1. Decide whether it is backward-compatible V1 or requires V2.
2. Change the Pydantic source model.
3. Add strict invariants and size/safety limits.
4. Update reconstruction conversion and fallback behavior.
5. Update `code.js` rendering and node-level error behavior.
6. Update the isolated Figma API simulation and its test documents.
7. Run `python -m scripts.export_schema`.
8. Update `DESIGN_IR.md`, `ARCHITECTURE.md`, and status.

Never make the renderer consume provider-specific fields.

## Changing Canva detection/navigation

Required properties:

- selectors describe generic platform structure, not one design;
- thumbnails/navigation chrome cannot be selected as full pages;
- slideshow mode advances before generic lookup;
- advancement must be proven;
- identical bytes require distinct authoritative identity;
- page count remains derived from the viewer;
- incomplete multi-page capture fails as a whole.

Add targeted fake-locator tests plus at least one fresh live validation. A successful count alone is insufficient; visually inspect page correspondence.

## Changing storage

`ArtifactStore` owns all artifact path construction and persistence. Preserve:

- server-generated IDs and UUID validation at routes;
- atomic JSON writes;
- metadata-last capture commit;
- binary data outside persisted JSON;
- TTL semantics;
- cleanup of incomplete/expired directories;
- per-capture and per-asset limits.

If moving to object storage, keep API page/asset URLs scoped and expiring.

## Changing plugin messages

Update both `ui.html` and `code.js` in the same change. Preserve:

- session ID correlation;
- one-page-at-a-time transfer;
- acknowledgements before the next page;
- cancellation and rollback;
- timeout recovery;
- stale response protection.

Add renderer tests for success and partial failure/cancellation.

## Schema regeneration

```powershell
python -m scripts.export_schema
python -m unittest tests.test_design_ir -v
```

The generated file should change only because the source model changed.

## Dependency policy

Runtime dependencies are intentionally small:

- Flask;
- Pillow;
- Playwright;
- Pydantic.

Before adding one, explain why the standard library/current dependency cannot safely do the job, pin a compatible range, and add verification. The plugin remains dependency-free unless a build process is deliberately introduced.

## Review checklist

- Does runtime behavior remain generic across links/designs?
- Are untrusted inputs and model output validated?
- Can failure return duplicate/missing visible content?
- Are credentials/tokens absent from logs and errors?
- Are dimensions, bytes, nodes, concurrency, and time bounded?
- Does cancellation leave partial artifacts or Figma frames?
- Are exact image and editable modes independently usable?
- Are tests deterministic and live validation kept separate?
- Did schema, API, status, troubleshooting, and architecture docs remain accurate?
