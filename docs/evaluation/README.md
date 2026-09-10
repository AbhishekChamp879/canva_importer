# Editable-Quality Evaluation

This directory owns the repeatable acceptance protocol and sanitized evidence for the editable-quality roadmap. Canva URLs, share tokens, signed asset URLs, capture IDs, and reconstruction job IDs must never be committed.

Evidence files:

- `phase1-capture-report.json` proves structural acquisition for the five-source corpus; it does not prove editable Figma fidelity.
- `phase2-asset-report.json` proves that a live Canva-hosted original asset passed the production security boundary and was selected during real OpenAI reconstruction. It does not close Phase 2 because final Figma clipping and the complete owned corpus still require manual acceptance.
- `phase1-baseline.json` is created only after all five final-Figma QA runs pass. It does not exist yet.

Sanitized evidence is documentation output only. Production capture and reconstruction never read these files, so they are not mocked or recorded runtime inputs.

## What is automatic

For every editable import, the Figma main thread exports each reconstructed page frame as a 1× PNG after all native and fallback nodes are created. The hidden `QA Reference` remains invisible. The plugin UI compares that exported PNG with the full captured Canva reference at a bounded maximum comparison dimension of 2,048 pixels and submits these measurements to the Flask job:

- exported and logical dimensions;
- export byte count and SHA-256;
- mean RGB pixel difference;
- final-Figma visual similarity;
- materially different-pixel rate.

The backend combines those measurements with exact-text, native/fallback coverage, missing-region, duplicate-text, and missing-font metrics. It evaluates every page against the declared Phase 1 thresholds and persists the report until the job TTL expires.

## Required owned corpus

Create exactly five anonymously accessible fixed-size Canva designs that you own and can keep stable:

| Category ID | Required role |
| --- | --- |
| `presentation` | landscape presentation |
| `poster` | poster |
| `social-post` | square or portrait social design |
| `flyer` | portrait flyer |
| `multi-page` | multi-page deck, including intentional duplicate pages |

Across the five cases, cover all feature labels accepted by the collector: `difficult-typography`, `photos`, `native-shapes`, `rotation`, `transparent-assets`, `duplicated-pages`, and `unsupported-effects`.

## Local manifest

Create `docs/evaluation/phase1-manifest.local.json`. This filename is ignored by source control. Its schema is:

- `schemaVersion`: integer `1`;
- `cases`: exactly five objects, one per required category;
- `id`: stable lowercase letters/digits/hyphens identifier;
- `category`: one required category ID;
- `ownershipConfirmed`: must be `true`;
- `sourceEnvironmentVariable`: a unique `CANVA_PHASE1_*_URL` variable name;
- `reviewDate`: `YYYY-MM-DD` date on which the source was visually reviewed;
- `expectedPages`: ordered dimension runs. Each item contains integer `width` and `height`, plus optional integer `count` when consecutive pages share dimensions. Omitting `count` means one page;
- `features`: one or more accepted feature labels; their union must cover every required feature.

Set the five named environment variables only in the local shell. Do not add them to `.env`, screenshots, documentation, or runtime code.

## Run protocol

1. Set `ARTIFACT_TTL_SECONDS=86400` for the evaluation session, restart Flask, and confirm OpenAI Vision OCR and OpenAI layout are configured. This keeps all five job reports available for one day while staying inside the supported retention range.
2. For each manifest case, paste its environment-provided URL into the plugin.
3. Load pages and visually compare every thumbnail with Canva. Stop if page count, order, dimensions, or content differs.
4. Select every page and run **Import editable layers**.
5. Wait for the plugin summary to show `Phase 1 page QA: passed` or `failed`. This step automatically creates `GET /api/reconstruction-jobs/{jobId}/figma-qa` evidence.
6. Record the `QA job ID` displayed in the plugin summary.
7. Run the collector with five `--job category=UUID` arguments:

```powershell
python -m scripts.phase1_report docs/evaluation/phase1-manifest.local.json `
  --job presentation=<UUID> `
  --job poster=<UUID> `
  --job social-post=<UUID> `
  --job flyer=<UUID> `
  --job multi-page=<UUID> `
  --output docs/evaluation/phase1-baseline.json
```

The command exits nonzero unless all five cases pass, verifies that each job's source fingerprint matches its manifest environment variable, validates declared page dimensions, preserves page-level font/text/coverage/export metrics, categorizes failures, and writes no Canva URL. Review the generated file for tokens before committing it.

## Thresholds and release gate

Every page must satisfy all checks:

| Check | Threshold |
| --- | ---: |
| exact OCR text | ≥95% |
| native visible-area coverage | ≥80% |
| final Figma visual similarity | ≥95% |
| missing visible region | 0 |
| duplicate visible text blocks | 0 |
| Figma export dimensions | exact logical dimensions |

The collector classifies failed evidence into acquisition, OCR, typography, asset recovery, hierarchy, vector, effect, renderer, and QA-related work. Acquisition failures happen before a Figma QA job exists and must be recorded manually beside the attempted case; they cannot be represented as passing evidence.

Phase 1 is complete only when `phase1-baseline.json` exists, contains five current real-design cases, and has `"passed": true`. Deterministic unit tests prove the evaluator works; they are not substitutes for this real Figma evidence.

`phase1-capture-report.json` is the sanitized structural baseline for the current five-source corpus. Its `passed` value proves live acquisition only; `finalEditableAcceptance` intentionally remains false until five actual Figma QA reports are collected.
