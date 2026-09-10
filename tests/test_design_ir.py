from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

from pydantic import ValidationError

from canva_converter.models import DesignDocumentV1


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="


def sample_document():
    return {
        "schemaVersion": 1,
        "id": "sample-document",
        "title": "Schema validation sample",
        "sourceUrl": "https://www.canva.com/design/ABC/Token123/view",
        "createdAt": "2026-08-18T00:00:00.000Z",
        "assets": {
            "reference-image": {
                "id": "reference-image",
                "mimeType": "image/png",
                "dataBase64": REFERENCE_PNG,
                "source": "reference",
            }
        },
        "pages": [{
            "id": "sample-page",
            "name": "Sample page",
            "width": 800,
            "height": 600,
            "background": {"type": "solid", "color": {"r": 1, "g": 1, "b": 1, "a": 1}},
            "qaReferenceAssetId": "reference-image",
            "metrics": {
                "nativeCoverage": 1,
                "fallbackCoverage": 0,
                "exactTextRate": 1,
                "visualSimilarity": 1,
                "pixelDifference": 0,
                "missingRegionRate": 0,
                "duplicateTextBlocks": 0,
                "missingFonts": [],
                "warnings": [],
            },
            "children": [{
                "id": "sample-text",
                "type": "text",
                "name": "Sample text",
                "box": {"x": 20, "y": 20, "width": 300, "height": 60},
                "rotation": 0,
                "opacity": 1,
                "visible": True,
                "locked": False,
                "zIndex": 0,
                "confidence": 1,
                "source": {"sourceType": "text", "extraction": "ocr"},
                "text": "Editable text",
                "runs": [{
                    "start": 0,
                    "end": 13,
                    "fontFamily": "Inter",
                    "fontStyle": "Regular",
                    "fontSize": 24,
                    "color": {"r": 0, "g": 0, "b": 0, "a": 1},
                    "textDecoration": "none",
                }],
                "horizontalAlign": "left",
                "verticalAlign": "top",
            }],
        }],
    }


class DesignIrTests(unittest.TestCase):
    def test_validates_design_document(self):
        document = DesignDocumentV1.model_validate(sample_document())
        self.assertEqual(document.schema_version, 1)
        self.assertGreater(len(document.pages[0].children), 0)

    def test_rejects_missing_asset_reference(self):
        payload = sample_document()
        payload["pages"][0]["qaReferenceAssetId"] = "does-not-exist"
        with self.assertRaisesRegex(ValidationError, "Missing QA reference asset"):
            DesignDocumentV1.model_validate(payload)

    def test_rejects_invalid_text_run(self):
        payload = sample_document()
        payload["pages"][0]["children"][0]["runs"][0]["end"] = 100
        with self.assertRaisesRegex(ValidationError, "Text runs"):
            DesignDocumentV1.model_validate(payload)

    def test_rejects_non_contiguous_text_runs(self):
        payload = sample_document()
        payload["pages"][0]["children"][0]["runs"] = [
            {**payload["pages"][0]["children"][0]["runs"][0], "end": 5},
            {**payload["pages"][0]["children"][0]["runs"][0], "start": 6, "end": 13},
        ]
        with self.assertRaisesRegex(ValidationError, "contiguous"):
            DesignDocumentV1.model_validate(payload)

    def test_rejects_non_finite_geometry_and_duplicate_pages(self):
        payload = sample_document()
        payload["pages"][0]["children"][0]["box"]["x"] = float("nan")
        with self.assertRaises(ValidationError):
            DesignDocumentV1.model_validate(payload)

        payload = sample_document()
        payload["pages"].append(deepcopy(payload["pages"][0]))
        with self.assertRaisesRegex(ValidationError, "Duplicate page id"):
            DesignDocumentV1.model_validate(payload)

    def test_rejects_unsafe_svg_and_non_canva_sources(self):
        payload = sample_document()
        text_node = payload["pages"][0]["children"][0]
        payload["pages"][0]["children"] = [{
            **text_node,
            "id": "unsafe-vector",
            "type": "vector",
            "text": None,
            "runs": None,
            "horizontalAlign": None,
            "verticalAlign": None,
            "svg": '<svg><script>alert(1)</script></svg>',
        }]
        with self.assertRaisesRegex(ValidationError, "unsafe"):
            DesignDocumentV1.model_validate(payload)

        payload = sample_document()
        payload["sourceUrl"] = "https://canva.com.attacker.example/design/ABC/view"
        with self.assertRaisesRegex(ValidationError, "sourceUrl"):
            DesignDocumentV1.model_validate(payload)

    def test_rejects_non_svg_vector_content_and_mismatched_image_bytes(self):
        payload = sample_document()
        text_node = payload["pages"][0]["children"][0]
        payload["pages"][0]["children"] = [{
            **text_node,
            "id": "not-svg",
            "type": "vector",
            "text": None,
            "runs": None,
            "horizontalAlign": None,
            "verticalAlign": None,
            "svg": "<html></html>",
        }]
        with self.assertRaisesRegex(ValidationError, "SVG root"):
            DesignDocumentV1.model_validate(payload)

        payload = sample_document()
        payload["assets"]["reference-image"]["mimeType"] = "image/jpeg"
        with self.assertRaisesRegex(ValidationError, "mimeType"):
            DesignDocumentV1.model_validate(payload)

    def test_rejects_ambiguous_assets_and_invalid_timestamps(self):
        payload = sample_document()
        payload["assets"]["reference-image"]["url"] = "https://www.canva.com/image.png"
        with self.assertRaisesRegex(ValidationError, "exactly one"):
            DesignDocumentV1.model_validate(payload)

        payload = sample_document()
        payload["createdAt"] = "2026-08-24T12:00:00"
        with self.assertRaisesRegex(ValidationError, "timezone"):
            DesignDocumentV1.model_validate(payload)

    def test_validates_image_crop_transforms_and_group_clipping(self):
        payload = sample_document()
        common = {
            "name": "Image",
            "box": {"x": 0, "y": 0, "width": 100, "height": 100},
            "rotation": 0,
            "opacity": 1,
            "visible": True,
            "locked": False,
            "zIndex": 0,
            "confidence": 1,
            "source": {"sourceType": "image", "extraction": "asset"},
        }
        image = {
            **common,
            "id": "cropped-image",
            "type": "image",
            "fill": {
                "type": "image",
                "assetId": "reference-image",
                "scaleMode": "crop",
                "transform": [[1, 0, 0], [0, 1, 0]],
            },
        }
        payload["pages"][0]["children"] = [{
            **common,
            "id": "clip-group",
            "type": "group",
            "source": {"sourceType": "group", "extraction": "vision"},
            "clipsContent": True,
            "children": [image],
        }]

        document = DesignDocumentV1.model_validate(payload)

        self.assertTrue(document.pages[0].children[0].clips_content)
        self.assertEqual(document.pages[0].children[0].children[0].fill.transform, [[1, 0, 0], [0, 1, 0]])

        payload["pages"][0]["children"][0]["children"][0]["fill"]["transform"] = [[1, 0], [0, 1]]
        with self.assertRaisesRegex(ValidationError, "2x3"):
            DesignDocumentV1.model_validate(payload)

        payload["pages"][0]["children"][0]["children"][0]["fill"]["transform"] = [[1, 0, 0], [0, 1, 0]]
        payload["pages"][0]["children"][0]["children"][0]["fill"]["scaleMode"] = "fill"
        with self.assertRaisesRegex(ValidationError, "scaleMode=crop"):
            DesignDocumentV1.model_validate(payload)

        payload = sample_document()
        payload["pages"][0]["children"][0]["clipsContent"] = True
        with self.assertRaisesRegex(ValidationError, "Only group"):
            DesignDocumentV1.model_validate(payload)

    def test_validates_native_gradients_effects_and_safe_compound_vectors(self):
        payload = sample_document()
        node = payload["pages"][0]["children"][0]
        payload["pages"][0]["children"] = [{
            **node,
            "id": "gradient-shape",
            "type": "rectangle",
            "text": None,
            "runs": None,
            "horizontalAlign": None,
            "verticalAlign": None,
            "fills": [{
                "type": "linear-gradient",
                "stops": [
                    {"position": 0, "color": {"r": 1, "g": 0, "b": 0, "a": 1}},
                    {"position": 1, "color": {"r": 0, "g": 0, "b": 1, "a": 1}},
                ],
                "transform": [[1, 0, 0], [0, 1, 0]],
            }],
            "effects": [
                {"type": "drop-shadow", "color": {"r": 0, "g": 0, "b": 0, "a": 0.4}, "offsetX": 2, "offsetY": 3, "radius": 8},
                {"type": "layer-blur", "radius": 2},
            ],
        }, {
            **node,
            "id": "compound-vector",
            "type": "vector",
            "text": None,
            "runs": None,
            "horizontalAlign": None,
            "verticalAlign": None,
            "svg": "<svg><g><rect width='10' height='10'/><circle cx='5' cy='5' r='3'/><path d='M0 0L10 10'/></g></svg>",
        }]

        document = DesignDocumentV1.model_validate(payload)

        self.assertEqual(document.pages[0].children[0].fills[0].type, "linear-gradient")
        self.assertEqual(len(document.pages[0].children[0].effects), 2)

        payload["pages"][0]["children"][1]["svg"] = "<svg><filter id='x'/><rect width='10' height='10'/></svg>"
        with self.assertRaisesRegex(ValidationError, "unsupported: filter"):
            DesignDocumentV1.model_validate(payload)

    def test_accepts_documents_with_more_than_ten_pages(self):
        payload = sample_document()
        template = payload["pages"][0]
        payload["pages"] = [
            {
                **deepcopy(template),
                "id": f"page-{index}",
                "name": f"Page {index + 1}",
                "children": [],
            }
            for index in range(25)
        ]

        document = DesignDocumentV1.model_validate(payload)

        self.assertEqual(len(document.pages), 25)

    def test_committed_json_schema_matches_the_pydantic_contract(self):
        committed = json.loads((ROOT / "schemas" / "design-document-v1.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(committed, DesignDocumentV1.model_json_schema(by_alias=True))

    def test_rejects_more_than_five_thousand_nested_nodes(self):
        payload = sample_document()
        leaf = payload["pages"][0]["children"][0]
        payload["pages"][0]["children"] = [{
            "id": "large-group",
            "type": "group",
            "name": "Large group",
            "box": {"x": 0, "y": 0, "width": 800, "height": 600},
            "rotation": 0,
            "opacity": 1,
            "visible": True,
            "locked": False,
            "zIndex": 0,
            "confidence": 1,
            "source": {"sourceType": "group", "extraction": "vision"},
            "children": [{**leaf, "id": f"node-{index}"} for index in range(5000)],
        }]
        with self.assertRaisesRegex(Exception, "5000-node limit"):
            DesignDocumentV1.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
