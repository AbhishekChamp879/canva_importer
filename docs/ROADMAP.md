# Engineering Roadmap

This roadmap starts from the code that exists on 24 August 2026. It protects the working exact-image path while turning editable reconstruction into a measured product. Dates are intentionally omitted; advancement is based on acceptance evidence.

## Phase 0 — Preserve the local exact-image baseline

Status: implemented, continuous compatibility work.

Required invariants:

- no design/link-specific runtime hardcoding;
- no 10-page or 18-page product cap;
- no silent partial multi-page success;
- no repeated page without authoritative page evidence;
- independent portrait/landscape/square dimensions;
- sequential Figma transfer and rollback;
- exact image mode remains independent of OpenAI.

Exit evidence:

- deterministic suites stay green;
- fresh live capture passes;
- every thumbnail is manually matched to its Canva page on representative viewer variants;
- cancelled import leaves no partial frames.

## Phase 1 — Build the editable quality baseline

Status: evaluation implementation, five-source live capture, and AI configuration complete; editable reconstruction/Figma evidence awaits five plugin runs.

Tasks:

1. Create controlled public designs owned for testing: presentation, poster, social post, flyer, and multi-page deck.
2. Include difficult typography, photos, native shapes, rotation, transparent assets, duplicated pages, and unsupported effects.
3. Define a sanitized manifest containing category, expected page count/dimensions, allowed public test URL source, and review date; do not place those URLs in runtime code.
4. Run fresh capture and editable reconstruction for each.
5. Export actual Figma frames and compare them to capture references.
6. Record per-page text, native/fallback, missing-region, duplicate-text, font, and pixel metrics.
7. Classify failures by acquisition, OCR, typography, asset recovery, hierarchy, vector, effect, renderer, or QA.

Implemented infrastructure:

- the Figma renderer automatically exports each final editable frame at logical 1× size;
- the plugin compares real Figma export pixels with the full captured reference;
- the Flask API validates, thresholds, and persists complete page-level final-Figma QA evidence;
- `scripts.phase1_report` validates the five-category owned-corpus manifest, collects five job reports, checks expected dimensions, classifies failures, and writes a URL-free baseline;
- the complete operator protocol and manifest contract are documented under `docs/evaluation/`.

Remaining acceptance action: run editable import in Figma for each of the five captured/validated source categories and collect their QA job IDs into a sanitized `docs/evaluation/phase1-baseline.json` whose top-level `passed` value is true. This repository contains no substitute/mock corpus and therefore does not claim that final evidence yet.

Exit criteria:

- repeatable evaluation protocol;
- baseline report checked into documentation without private tokens;
- no missing visible region and no duplicate visible text on supported cases;
- every quality claim references measured evidence.

## Phase 2 — Improve editable assets and clipping

Status: core implementation complete; live evaluation/Figma acceptance pending.

Priority order:

1. Match safe DOM image hints to layout regions.
2. Download only trusted Canva asset URLs with redirect/content/signature/byte validation.
3. Prefer original-resolution assets over screenshot crops.
4. Add mask/clipping representation to a new backward-compatible IR revision or V2.
5. Render crop transforms and clipping accurately in Figma.
6. Preserve crop fallback whenever original asset recovery is uncertain.

Implemented infrastructure:

- capture records include image natural dimensions, CSS fit/position, and corner-radius evidence;
- page-sized flattened viewer images are excluded from original-layer hints;
- deterministic geometry matching connects image regions to DOM hints;
- a backend-only downloader revalidates every Canva-host redirect, MIME type, byte signature, decoded dimensions, pixel count, and 25 MB limit;
- original Canva images are embedded as `source=canva`; failed or ambiguous recovery remains a labelled screenshot crop;
- signed asset URLs are removed before DOM hints are sent to OpenAI;
- backward-compatible V1 `clipsContent` groups and validated 2×3 image crop transforms render through the Figma Plugin API;
- conversion reports distinguish original images, screenshot crops, clipped groups, raster fallbacks, and failed layers.

Remaining acceptance action: run the owned evaluation designs through editable import and confirm original-asset usage, clipping, and final-Figma QA on real supported cases. Complex/non-rectangular masks intentionally remain regional raster fallbacks.

Exit criteria:

- image layers use original assets on supported evaluation designs;
- masks/clips survive Figma import;
- no regression in exact-image mode or security boundary;
- fallback reasons distinguish crop, unavailable asset, and unsupported mask.

## Phase 3 — Typography, hierarchy, and vectors

Status: core implementation complete; owned-corpus calibration and final-Figma acceptance pending.

Tasks:

- improve OCR line/paragraph reconstruction without changing exact text;
- add deterministic font-family/style mapping and font metric compensation;
- infer group hierarchy using geometry/evidence before model-only relationships;
- introduce gradient/effect vocabulary with explicit fallback behavior;
- support more SVG primitives and compound vectors safely;
- calibrate confidence thresholds by element category.

Implemented infrastructure:

- deterministic CSS font-stack normalization, numeric/named weight mapping, OCR-authoritative boxes, and bounded line-height/letter-spacing compensation;
- Figma font loading tries compatible style aliases and same-family fallbacks before Inter, while reporting the exact substitution;
- conservative geometry hierarchy inference assigns unparented elements only to confident, non-page-sized groups containing multiple elements;
- backward-compatible two-stop gradient, drop-shadow, and layer-blur IR vocabulary with strict numeric limits;
- Figma rendering and conversion-report counts for native gradients and effects;
- safe compound SVG primitives are accepted while filters, masks, embedded images, text, animation, and unsupported SVG elements become labelled regional fallbacks;
- transformed gradients are excluded from backend pixel claims and remain measured by final-Figma export QA.

Remaining acceptance action: run the owned corpus through editable Figma import, measure font substitutions and hierarchy usefulness, and calibrate element-category confidence thresholds from those real results.

Exit criteria on the curated set:

- ≥95% exact OCR text;
- ≥80% native visible area for ordinary supported designs;
- zero visible screenshot/text duplication;
- documented font substitutions;
- hierarchy useful for common editing operations.

## Phase 4 — Figma-native visual QA

Status: core export/compare path implemented early during Phase 1; calibration and regression masking remain.

Tasks:

1. Automate or standardize Figma frame export at source dimensions.
2. Compare exported pixels with captured reference.
3. Separate comparison masks for font/vector anti-aliasing.
4. Store page-level source, backend approximation, and final-Figma metrics.
5. Add regression thresholds by evaluation category.
6. Fail releases on missing regions or duplicate text, not only average similarity.

Exit criteria:

- ≥95% final visual similarity on declared supported evaluation designs;
- metrics reproduce within a defined tolerance;
- regression report identifies affected page and layer class.

## Phase 5 — Local beta reliability

Status: partially implemented.

Tasks:

- extend large-page and extreme-aspect live-validator regression coverage;
- add large-deck performance and memory benchmarks;
- add disk-full, corrupt artifact, provider timeout, browser crash, restart, and cancellation soak tests;
- define process-restart behavior for persisted nonterminal jobs;
- add structured logging with request/job correlation and secret redaction;
- expose internal health/readiness for browser/provider dependencies;
- add configurable plugin/backend base URL through a build or settings flow.

Exit criteria:

- documented resource envelope;
- no orphan frames or unbounded queues;
- predictable restart and cleanup behavior;
- actionable logs without source tokens or keys.

## Phase 6 — Hosted product architecture

Status: local OAuth acquisition trial implemented early; hosted product remains future.

Required work:

- authenticated Figma users and tenant authorization;
- promote the implemented local Canva OAuth/export trial to reviewed, tenant-scoped production acquisition;
- durable queue and idempotent workers;
- encrypted object storage and database;
- quotas, rate limits, abuse controls, billing, and cost budgets;
- deployment automation, monitoring, tracing, alerting, backups, and disaster recovery;
- production Figma manifest/domain policy;
- privacy, retention, consent, copyright, Canva terms, and Figma policy review.

Exit criteria:

- security and privacy review completed;
- tenant isolation verified;
- failures retry idempotently;
- operational SLOs and rollback plan defined;
- production claims limited to measured supported sources.

## Decision rule

Do not add more model complexity until a measured evaluation failure demonstrates the need. Prefer deterministic geometry, pixel sampling, validation, and asset evidence. When uncertain, preserve pixels locally and report the fallback instead of emitting a confidently wrong editable layer.
