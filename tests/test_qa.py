from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from canva_converter.models import (
    Box, Color, ConversionMetrics, DesignNode, DesignPage, SolidFill, SourceMeta,
)
from canva_converter.qa import evaluate_page_quality


def metrics() -> ConversionMetrics:
    return ConversionMetrics(nativeCoverage=1, fallbackCoverage=0, exactTextRate=1, missingFonts=[], warnings=[])


def rectangle(node_id: str, x: float, y: float, width: float, height: float, color: Color) -> DesignNode:
    return DesignNode(
        id=node_id, type="rectangle", box=Box(x=x, y=y, width=width, height=height),
        zIndex=0, confidence=1, source=SourceMeta(sourceType="rectangle", extraction="vision"),
        fills=[SolidFill(color=color)],
    )


class VisualQaTests(unittest.TestCase):
    def page(self, children):
        return DesignPage(
            id="page", name="Page", width=100, height=100,
            background=SolidFill(color=Color(r=1, g=1, b=1)), children=children,
            qaReferenceAssetId="qa", metrics=metrics(),
        )

    def test_matching_native_shape_has_high_similarity_and_no_missing_region(self):
        reference = Image.new("RGB", (100, 100), "white")
        ImageDraw.Draw(reference).rectangle((10, 10, 49, 49), fill=(0, 128, 255))
        page = self.page([rectangle("shape", 10, 10, 40, 40, Color(r=0, g=128 / 255, b=1))])

        result = evaluate_page_quality(page, {}, reference)

        self.assertGreater(result["visualSimilarity"], 0.99)
        self.assertEqual(result["missingRegionRate"], 0)

    def test_unreconstructed_foreground_is_reported_as_missing(self):
        reference = Image.new("RGB", (100, 100), "white")
        ImageDraw.Draw(reference).rectangle((10, 10, 49, 49), fill="black")

        result = evaluate_page_quality(self.page([]), {}, reference)

        self.assertGreater(result["missingRegionRate"], 0.95)
        self.assertLess(result["visualSimilarity"], 0.9)

    def test_duplicate_overlapping_text_is_counted(self):
        source = SourceMeta(sourceType="text", extraction="ocr")
        common = dict(
            type="text", box=Box(x=10, y=10, width=50, height=20), zIndex=1,
            confidence=1, source=source, text="Same",
            runs=[{"start": 0, "end": 4, "fontFamily": "Inter", "fontStyle": "Regular", "fontSize": 12, "color": {"r": 0, "g": 0, "b": 0}}],
        )
        page = self.page([DesignNode(id="a", **common), DesignNode(id="b", **common)])

        result = evaluate_page_quality(page, {}, Image.new("RGB", (100, 100), "white"))

        self.assertEqual(result["duplicateTextBlocks"], 1)

    def test_rectangular_group_clipping_is_respected_by_backend_qa(self):
        blue = Color(r=0, g=128 / 255, b=1)
        child = rectangle("oversized", -20, -20, 100, 100, blue)
        group = DesignNode(
            id="clip", type="group", box=Box(x=20, y=20, width=40, height=40),
            zIndex=0, confidence=1, source=SourceMeta(sourceType="group", extraction="vision"),
            children=[child], clipsContent=True,
        )
        reference = Image.new("RGB", (100, 100), "white")
        ImageDraw.Draw(reference).rectangle((20, 20, 59, 59), fill=(0, 128, 255))

        result = evaluate_page_quality(self.page([group]), {}, reference)

        self.assertGreater(result["visualSimilarity"], 0.98)
        self.assertEqual(result["missingRegionRate"], 0)


if __name__ == "__main__":
    unittest.main()
