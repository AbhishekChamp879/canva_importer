from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from scripts.phase1_report import REQUIRED_CATEGORIES, REQUIRED_FEATURES, build_baseline, parse_job_mappings, validate_manifest


def manifest() -> dict:
    cases = []
    features = sorted(REQUIRED_FEATURES)
    for index, category in enumerate(sorted(REQUIRED_CATEGORIES)):
        cases.append({
            "id": f"owned-{category}",
            "category": category,
            "ownershipConfirmed": True,
            "sourceEnvironmentVariable": f"CANVA_PHASE1_CASE_{index}_URL",
            "reviewDate": "2026-08-27",
            "expectedPages": [{"width": 800 + index, "height": 600 + index}],
            "features": features if index == 0 else ["photos"],
        })
    return {"schemaVersion": 1, "cases": cases}


class Phase1ReportTests(unittest.TestCase):
    def test_manifest_requires_owned_five_category_feature_complete_corpus(self):
        environment = {f"CANVA_PHASE1_CASE_{index}_URL": "https://www.canva.com/design/ABC123/view" for index in range(5)}
        with patch.dict(os.environ, environment, clear=False):
            cases = validate_manifest(manifest(), require_sources=True)
        self.assertEqual({case["category"] for case in cases}, REQUIRED_CATEGORIES)
        invalid = manifest()
        invalid["cases"][0]["ownershipConfirmed"] = False
        with self.assertRaisesRegex(ValueError, "ownership"):
            validate_manifest(invalid)

    def test_manifest_expands_repeated_page_dimensions(self):
        value = manifest()
        value["cases"][0]["expectedPages"] = [{"width": 1920, "height": 1080, "count": 40}]
        cases = validate_manifest(value)
        self.assertEqual(len(cases[0]["expectedPages"]), 40)
        self.assertTrue(all(page == {"width": 1920, "height": 1080} for page in cases[0]["expectedPages"]))

    def test_job_mapping_requires_every_category_and_uuid(self):
        values = [f"{category}=123e4567-e89b-42d3-a456-42661417400{index}" for index, category in enumerate(sorted(REQUIRED_CATEGORIES))]
        self.assertEqual(set(parse_job_mappings(values)), REQUIRED_CATEGORIES)
        with self.assertRaisesRegex(ValueError, "missing"):
            parse_job_mappings(values[:-1])

    @patch("scripts.phase1_report.request_json")
    def test_baseline_is_sanitized_and_fails_dimension_mismatch(self, request):
        cases = validate_manifest(manifest())
        jobs = {category: f"123e4567-e89b-42d3-a456-42661417400{index}" for index, category in enumerate(sorted(REQUIRED_CATEGORIES))}
        reports = []
        for case in cases:
            expected = case["expectedPages"][0]
            reports.append({
                "jobId": jobs[case["category"]],
                "sourceFingerprint": "a" * 64,
                "pages": [{
                    "pageIndex": 0,
                    "logicalWidth": expected["width"],
                    "logicalHeight": expected["height"],
                    "checks": {
                        "exportDimensions": True,
                        "exactText": True,
                        "nativeCoverage": True,
                        "figmaVisualSimilarity": True,
                        "zeroMissingVisibleRegions": True,
                        "zeroDuplicateVisibleText": True,
                    },
                }],
                "summary": {"pageCount": 1},
                "thresholds": {},
                "passed": True,
            })
        reports[-1]["pages"][0]["logicalWidth"] += 1
        request.side_effect = reports
        baseline = build_baseline(cases, jobs, "http://127.0.0.1:3000/")
        self.assertFalse(baseline["passed"])
        self.assertEqual(baseline["passedCases"], 4)
        self.assertNotIn("https://", str(baseline).casefold())


if __name__ == "__main__":
    unittest.main()
