# Technical Documentation

This directory is the engineering handbook for the Canva to Figma Importer. It documents the system that exists in this repository, not a hypothetical hosted product.

The application is a local Python/Flask service plus a Figma development plugin. It has two output modes:

| Mode | Output | Maturity |
| --- | --- | --- |
| Exact page images | One captured image inside one correctly sized Figma frame per selected Canva page | Local MVP |
| Canva OAuth acquisition | Authenticated design picker and official per-page PNG export | Local trial |
| Editable AI | Native text, images, groups, shapes, vectors, and raster fallback regions | Functional alpha |

Supported inputs are anonymously accessible fixed-size Canva links, plus fixed-size designs available to an optionally connected Canva account. Private designs require OAuth. Unbounded Canva document types remain outside both providers.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m scripts.install_browser
python run.py
```

Then import `figma-plugin/manifest.json` through Figma Desktop → Plugins → Development → Import plugin from manifest. An OpenAI Platform API key is required only when editable reconstruction is selected.

## Suggested reading order

For a new engineer:

1. [Project status](STATUS.md) — what works, what has been verified, and what remains incomplete.
2. [Roadmap](ROADMAP.md) — evidence-gated next phases and acceptance criteria.
3. [Architecture](ARCHITECTURE.md) — components, boundaries, data flow, state machines, and storage lifecycle.
4. [Configuration](CONFIGURATION.md) — local setup, environment variables, browser resolution, and AI provider credentials.
5. [Capture pipeline](CAPTURE_PIPELINE.md) — URL handling, Canva browser acquisition, multi-page proof, limits, and failure modes.
6. [Design IR](DESIGN_IR.md) — the versioned contract between reconstruction and Figma.
7. [Figma plugin](FIGMA_PLUGIN.md) — UI/main-thread split, message protocol, rendering, rollback, and metadata.
8. [HTTP API](API.md) — endpoint contracts and status/error payloads.
9. [Testing](TESTING.md), [security](SECURITY.md), and [development](DEVELOPMENT.md).
10. [Phase 1 evaluation](evaluation/README.md) — owned corpus, real Figma export metrics, thresholds, and sanitized baseline collection.
11. [Canva OAuth trial](CANVA_OAUTH.md) — Developer Portal setup, security model, endpoints, and limitations.

Use [Troubleshooting](TROUBLESHOOTING.md) while running the service or plugin.

## Repository map

```text
canva_importer/
├── canva_converter/                 Python application package
│   ├── __init__.py                  Flask application factory, CORS, headers
│   ├── config.py                    Validated environment configuration
│   ├── routes.py                    HTTP API and structured error handlers
│   ├── services.py                  Dependency container and lifecycle
│   ├── models.py                    Pydantic API, capture, job, and IR models
│   ├── store.py                     Expiring disk-backed artifact store
│   ├── capture_jobs.py              Bounded asynchronous capture runner
│   ├── jobs.py                      Bounded reconstruction runner
│   ├── providers.py                 OpenAI Vision OCR/layout provider adapters
│   ├── asset_fetch.py               Trusted original Canva image recovery
│   ├── reconstruction.py            Deterministic reconstruction and fallbacks
│   ├── qa.py                        Backend visual-quality approximation
│   └── acquisition/
│       ├── url_policy.py            Canva URL trust and canonicalization
│       ├── browser_runtime.py       Portable Playwright browser selection
│       ├── browser_state.py         Public/private/challenge classification
│       ├── page_detection.py        Fixed-page candidate scoring
│       ├── coordinator.py           Duplicate/concurrent capture guard
│       ├── canva.py                 End-to-end public browser acquisition
│       └── oauth.py                 Canva Connect auth/API/export acquisition
├── figma-plugin/
│   ├── manifest.json                Development manifest and network policy
│   ├── ui.html                      Plugin UI, API polling, page streaming
│   └── code.js                      Figma sandbox renderer and transactions
├── schemas/
│   └── design-document-v1.schema.json  Generated public IR schema
├── scripts/
│   ├── export_schema.py             Regenerates the IR JSON Schema
│   ├── install_browser.py           Managed Chromium install/launch check
│   ├── live_validate.py             Fresh live capture validator
│   └── phase1_report.py             Sanitized five-case Figma QA collector
├── tests/                           Python/JavaScript contract tests
│   └── figma/renderer.test.js       Figma API simulation test
├── run.py                           Local service entry point
├── .env.example                     Supported local configuration
└── requirements.txt                 Runtime/test Python dependencies
```

## Architectural rules

- The public browser capture path must never depend on test data, recorded responses, or link-specific hardcoding.
- Model output never goes directly to Figma. It must validate as `ReconstructedPage`, pass deterministic normalization, and then validate as `DesignDocumentV1`.
- Exact image import and editable reconstruction remain separate user choices. AI failure must not break the exact image workflow.
- A multi-page capture fails closed when page advancement cannot be proven; it must not silently return repeated page 1 images.
- The complete page screenshot is visible only in exact image mode. Editable mode keeps it hidden as a QA reference and uses regional fallback pixels when needed.
- Credentials stay in the Flask process. The plugin never receives the OpenAI key, Canva client secret, or Canva user tokens.
- Large binary artifacts live in expiring files, not persisted JSON responses.

## Terminology

- **Capture:** acquisition of page pixels through the public viewer or official OAuth export, with DOM hints when the browser path supplies them.
- **Exact page image:** the normalized page screenshot imported as a single Figma image layer.
- **Reconstruction:** OCR/layout analysis and deterministic conversion into editable/fallback IR nodes.
- **Design IR:** `DesignDocumentV1`, the versioned neutral document contract.
- **Native coverage:** estimated visible page area represented by non-fallback nodes.
- **Raster fallback:** an image crop preserving a region that cannot be represented confidently as native nodes.
- **QA reference:** a hidden complete screenshot retained on an editable page for manual comparison.

## Scope statement

The current codebase is a local development system, not a hosted multi-tenant service and not a universal Canva parser. Its strongest end-to-end capability is anonymous fixed-size Canva capture followed by exact image import. Editable reconstruction is implemented but requires broader live quality validation before production claims are justified.
