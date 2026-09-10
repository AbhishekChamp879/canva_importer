from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from scripts.live_validate import request_json
from canva_converter.config import SERVICE_ROOT, load_env
from canva_converter.models import CaptureRequest


REQUIRED_CATEGORIES = {"presentation", "poster", "social-post", "flyer", "multi-page"}
REQUIRED_FEATURES = {
    "difficult-typography",
    "photos",
    "native-shapes",
    "rotation",
    "transparent-assets",
    "duplicated-pages",
    "unsupported-effects",
}
SOURCE_ENV_PATTERN = re.compile(r"CANVA_PHASE1_[A-Z0-9_]+_URL")
JOB_ID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", re.IGNORECASE)


def validate_manifest(raw: object, *, require_sources: bool = False) -> list[dict]:
    if not isinstance(raw, dict) or raw.get("schemaVersion") != 1:
        raise ValueError("Phase 1 manifest must be an object with schemaVersion 1.")
    cases = raw.get("cases")
    if not isinstance(cases, list) or len(cases) != len(REQUIRED_CATEGORIES):
        raise ValueError("Phase 1 manifest must contain exactly five evaluation cases.")
    categories: set[str] = set()
    identifiers: set[str] = set()
    source_variables: set[str] = set()
    covered_features: set[str] = set()
    normalized: list[dict] = []
    for position, case in enumerate(cases, 1):
        if not isinstance(case, dict):
            raise ValueError(f"Evaluation case {position} must be an object.")
        identifier = case.get("id")
        category = case.get("category")
        source_env = case.get("sourceEnvironmentVariable")
        review_date = case.get("reviewDate")
        expected_pages = case.get("expectedPages")
        features = case.get("features")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", identifier):
            raise ValueError(f"Evaluation case {position} has an invalid id.")
        if identifier in identifiers:
            raise ValueError(f"Evaluation case id {identifier!r} is duplicated.")
        if category not in REQUIRED_CATEGORIES or category in categories:
            raise ValueError(f"Evaluation case {identifier!r} has an invalid or duplicated category.")
        if case.get("ownershipConfirmed") is not True:
            raise ValueError(f"Evaluation case {identifier!r} must confirm test-design ownership.")
        if not isinstance(source_env, str) or not SOURCE_ENV_PATTERN.fullmatch(source_env):
            raise ValueError(f"Evaluation case {identifier!r} must reference a CANVA_PHASE1_*_URL environment variable.")
        if source_env in source_variables:
            raise ValueError(f"Evaluation source environment variable {source_env!r} is duplicated.")
        expected_source_fingerprint = None
        if require_sources:
            source = os.getenv(source_env, "")
            parsed = urlsplit(source)
            if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in {"canva.com", "www.canva.com", "canva.link"}:
                raise ValueError(f"{source_env} must contain a public Canva HTTPS URL.")
            normalized_source = str(CaptureRequest.model_validate({"url": source}).url)
            expected_source_fingerprint = sha256(normalized_source.encode("utf-8")).hexdigest()
        try:
            datetime.strptime(str(review_date), "%Y-%m-%d")
        except ValueError as error:
            raise ValueError(f"Evaluation case {identifier!r} reviewDate must use YYYY-MM-DD.") from error
        if not isinstance(expected_pages, list) or not expected_pages:
            raise ValueError(f"Evaluation case {identifier!r} must declare every expected page dimension.")
        pages = []
        for page_number, page in enumerate(expected_pages, 1):
            if not isinstance(page, dict) or not isinstance(page.get("width"), int) or not isinstance(page.get("height"), int):
                raise ValueError(f"Evaluation case {identifier!r} page {page_number} dimensions must be integers.")
            if not 1 <= page["width"] <= 8192 or not 1 <= page["height"] <= 8192:
                raise ValueError(f"Evaluation case {identifier!r} page {page_number} dimensions are outside 1..8192.")
            count = page.get("count", 1)
            if not isinstance(count, int) or not 1 <= count <= 10_000 or len(pages) + count > 10_000:
                raise ValueError(f"Evaluation case {identifier!r} page {page_number} count is outside 1..10000.")
            pages.extend({"width": page["width"], "height": page["height"]} for _ in range(count))
        if not isinstance(features, list) or not features or any(feature not in REQUIRED_FEATURES for feature in features):
            raise ValueError(f"Evaluation case {identifier!r} has an invalid feature declaration.")
        identifiers.add(identifier)
        categories.add(category)
        source_variables.add(source_env)
        covered_features.update(features)
        normalized.append({
            "id": identifier,
            "category": category,
            "sourceEnvironmentVariable": source_env,
            "reviewDate": review_date,
            "expectedPages": pages,
            "features": sorted(set(features)),
            **({"expectedSourceFingerprint": expected_source_fingerprint} if expected_source_fingerprint else {}),
        })
    if categories != REQUIRED_CATEGORIES:
        raise ValueError("Phase 1 manifest does not cover every required category exactly once.")
    missing_features = sorted(REQUIRED_FEATURES - covered_features)
    if missing_features:
        raise ValueError(f"Phase 1 corpus is missing required features: {', '.join(missing_features)}.")
    return normalized


def parse_job_mappings(values: list[str]) -> dict[str, str]:
    jobs: dict[str, str] = {}
    for value in values:
        category, separator, job_id = value.partition("=")
        if not separator or category not in REQUIRED_CATEGORIES or not JOB_ID_PATTERN.fullmatch(job_id):
            raise ValueError("Each --job must use category=UUID with a required Phase 1 category.")
        if category in jobs:
            raise ValueError(f"Duplicate --job mapping for {category}.")
        jobs[category] = job_id.lower()
    if set(jobs) != REQUIRED_CATEGORIES:
        missing = ", ".join(sorted(REQUIRED_CATEGORIES - set(jobs)))
        raise ValueError(f"A completed Figma QA job is required for every category; missing: {missing}.")
    return jobs


def classify_failures(report: dict) -> list[str]:
    classes: set[str] = set()
    for page in report.get("pages", []):
        checks = page.get("checks", {})
        if not checks.get("exportDimensions", False):
            classes.add("renderer")
        if not checks.get("exactText", False):
            classes.update({"ocr", "typography"})
        if not checks.get("nativeCoverage", False):
            classes.update({"asset-recovery", "hierarchy", "vector", "effect"})
        if not checks.get("figmaVisualSimilarity", False):
            classes.update({"renderer", "qa"})
        if not checks.get("zeroMissingVisibleRegions", False):
            classes.update({"hierarchy", "effect"})
        if not checks.get("zeroDuplicateVisibleText", False):
            classes.update({"ocr", "hierarchy"})
    return sorted(classes)


def build_baseline(cases: list[dict], jobs: dict[str, str], base_url: str) -> dict:
    evidence = []
    for case in cases:
        report = request_json(base_url, f"/api/reconstruction-jobs/{jobs[case['category']]}/figma-qa")
        pages = report.get("pages")
        if not isinstance(pages, list):
            raise RuntimeError(f"Figma QA for {case['category']} returned no page evidence.")
        actual_dimensions = [
            {"width": page.get("logicalWidth"), "height": page.get("logicalHeight")}
            for page in sorted(pages, key=lambda page: page.get("pageIndex", -1))
        ]
        dimensions_match = actual_dimensions == case["expectedPages"]
        source_matches = not case.get("expectedSourceFingerprint") or report.get("sourceFingerprint") == case["expectedSourceFingerprint"]
        page_evidence = [{
            "pageIndex": page.get("pageIndex"),
            "width": page.get("logicalWidth"),
            "height": page.get("logicalHeight"),
            "figmaExport": {
                "width": page.get("exportWidth"),
                "height": page.get("exportHeight"),
                "byteLength": page.get("exportByteLength"),
                "sha256": page.get("exportSha256"),
                "comparedWidth": page.get("comparedWidth"),
                "comparedHeight": page.get("comparedHeight"),
                "pixelDifference": page.get("pixelDifference"),
                "visualSimilarity": page.get("visualSimilarity"),
                "mismatchRate": page.get("mismatchRate"),
            },
            "backendMetrics": page.get("backendMetrics", {}),
            "checks": page.get("checks", {}),
            "passed": page.get("passed") is True,
        } for page in sorted(pages, key=lambda page: page.get("pageIndex", -1))]
        evidence.append({
            "id": case["id"],
            "category": case["category"],
            "reviewDate": case["reviewDate"],
            "features": case["features"],
            "sourceEnvironmentVariable": case["sourceEnvironmentVariable"],
            "sourceFingerprint": report.get("sourceFingerprint"),
            "sourceMatchesManifest": source_matches,
            "jobId": report.get("jobId"),
            "expectedPages": case["expectedPages"],
            "actualPages": actual_dimensions,
            "pages": page_evidence,
            "dimensionsMatch": dimensions_match,
            "metrics": report.get("summary", {}),
            "thresholds": report.get("thresholds", {}),
            "failureClasses": classify_failures(report),
            "passed": source_matches and dimensions_match and report.get("passed") is True,
        })
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "evaluationProtocol": "phase1-editable-quality-v1",
        "caseCount": len(evidence),
        "passedCases": sum(1 for case in evidence if case["passed"]),
        "cases": evidence,
        "passed": all(case["passed"] for case in evidence),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect sanitized Phase 1 evidence from five final-Figma QA jobs.")
    parser.add_argument("manifest", type=Path, help="Local Phase 1 corpus manifest JSON.")
    parser.add_argument("--job", action="append", default=[], help="Required category=UUID Figma QA job mapping; repeat five times.")
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--output", type=Path, required=True, help="Sanitized baseline report path.")
    arguments = parser.parse_args()
    load_env(SERVICE_ROOT / ".env")
    parsed_base = urlsplit(arguments.base_url)
    if parsed_base.scheme != "http" or parsed_base.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed_base.username or parsed_base.password:
        raise SystemExit("--base-url must be an unauthenticated loopback HTTP URL.")
    try:
        manifest = json.loads(arguments.manifest.read_text(encoding="utf-8"))
        cases = validate_manifest(manifest, require_sources=True)
        jobs = parse_job_mappings(arguments.job)
        baseline = build_baseline(cases, jobs, arguments.base_url.rstrip("/") + "/")
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(arguments.output), "passed": baseline["passed"], "passedCases": baseline["passedCases"], "caseCount": baseline["caseCount"]}))
    if not baseline["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
