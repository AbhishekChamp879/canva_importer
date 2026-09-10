from __future__ import annotations

import base64
import io
import unittest
from time import time
from unittest.mock import patch

from PIL import Image, ImageDraw

from canva_converter.asset_fetch import DownloadedCanvaAsset
from canva_converter.models import CaptureRecord, CapturedPage, DomImageHint, DomTextHint, OcrBlock, OcrResult, ReconstructedElement, ReconstructedPage, utc_now
from canva_converter.reconstruction import group_ocr_into_lines, normalize_elements, normalize_font_family, reconstruct_document


class FakeOcr:
    def detect(self, _image, _width, _height):
        return OcrResult(blocks=[OcrBlock(id="word", text="Hello", x=10, y=10, width=30, height=10, confidence=0.99)], fullText="Hello")


class FakeLayout:
    def analyze(self, *_args):
        return ReconstructedPage(backgroundColor="#ffffff", elements=[ReconstructedElement(id="shape", type="rectangle", x=5, y=40, width=50, height=30, rotation=0, opacity=1, zIndex=0, confidence=0.9, fillColor="#336699")])


class StaticOcr:
    def __init__(self, blocks=None, full_text=""):
        self.result = OcrResult(blocks=blocks or [], fullText=full_text)

    def detect(self, _image, _width, _height):
        return self.result


class StaticLayout:
    def __init__(self, elements, background="#ffffff"):
        self.result = ReconstructedPage(backgroundColor=background, elements=elements)

    def analyze(self, *_args):
        return self.result


class CapturingLayout(StaticLayout):
    def analyze(self, *_args):
        self.last_args = _args
        return self.result


class ReconstructionTests(unittest.TestCase):
    def capture(self, image=None):
        image = image or Image.new("RGB", (200, 200), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        screenshot = base64.b64encode(buffer.getvalue()).decode("ascii")
        page = CapturedPage(id="11111111-1111-4111-8111-111111111111", index=0, width=100, height=100, screenshotBase64=screenshot, textHints=[], imageHints=[])
        capture = CaptureRecord(id="22222222-2222-4222-8222-222222222222", sourceUrl="https://www.canva.com/design/ABC/token/view", title="Fixture", createdAt=utc_now(), expiresAt=time()+3600, pages=[page])
        return page, capture

    def test_produces_valid_native_ir_and_hidden_reference(self):
        page, capture = self.capture()

        document = reconstruct_document(capture, [page.id], FakeOcr(), FakeLayout(), lambda *_: None, lambda: False)

        self.assertEqual(document.schema_version, 1)
        self.assertEqual(document.pages[0].children[0].type, "rectangle")
        self.assertTrue(any(node.type == "text" for node in document.pages[0].children))
        self.assertIn(document.pages[0].qa_reference_asset_id, document.assets)

    def test_normalization_clips_bounds_uniquifies_ids_and_breaks_cycles(self):
        page, _capture = self.capture()
        layout = ReconstructedPage(backgroundColor="#ffffff", elements=[
            ReconstructedElement(id="same id", type="group", parentId="other", x=-10, y=-5, width=200, height=200, zIndex=0, confidence=1),
            ReconstructedElement(id="other", type="group", parentId="same id", x=0, y=0, width=80, height=80, zIndex=1, confidence=1),
            ReconstructedElement(id="same id", type="rectangle", parentId="other", x=95, y=95, width=50, height=50, zIndex=2, confidence=1),
        ])

        normalized = normalize_elements(layout, page)

        self.assertEqual(len({element.id for element in normalized}), 3)
        self.assertTrue(all(element.id.startswith(f"{page.id}-") for element in normalized))
        self.assertTrue(all(0 <= element.x < page.width and 0 <= element.y < page.height for element in normalized))
        self.assertTrue(all(element.x + element.width <= page.width and element.y + element.height <= page.height for element in normalized))
        self.assertIsNone(normalized[0].parent_id)
        self.assertIsNone(normalized[1].parent_id)

    def test_ocr_deduplication_and_model_z_order_are_deterministic(self):
        page, capture = self.capture()
        blocks = [
            OcrBlock(id="strong", text="Hello", x=10, y=10, width=30, height=10, confidence=0.99),
            OcrBlock(id="duplicate", text="Hello", x=10, y=10, width=30, height=10, confidence=0.7),
        ]
        layout = StaticLayout([
            ReconstructedElement(id="model-text", type="text", text="Hello", x=8, y=8, width=40, height=18, zIndex=1, confidence=0.95),
            ReconstructedElement(id="shape", type="rectangle", x=5, y=5, width=50, height=30, zIndex=2, confidence=0.95, fillColor="#ffffff"),
        ])

        document = reconstruct_document(capture, [page.id], StaticOcr(blocks, "Hello world"), layout, lambda *_: None, lambda: False)
        children = document.pages[0].children
        text_nodes = [node for node in children if node.type == "text"]

        self.assertEqual(len(text_nodes), 1)
        self.assertEqual(text_nodes[0].z_index, 1)
        self.assertEqual(children[0].type, "text")
        self.assertLess(document.pages[0].metrics.exact_text_rate, 1)

    def test_shape_fill_is_sampled_from_source_pixels(self):
        image = Image.new("RGB", (200, 200), "white")
        ImageDraw.Draw(image).rectangle((20, 20, 99, 99), fill=(0, 128, 255))
        page, capture = self.capture(image)
        layout = StaticLayout([
            ReconstructedElement(id="shape", type="rectangle", x=10, y=10, width=40, height=40, zIndex=0, confidence=0.99, fillColor="#ff0000"),
        ])

        document = reconstruct_document(capture, [page.id], StaticOcr(), layout, lambda *_: None, lambda: False)
        sampled = document.pages[0].children[0].fills[0].color

        self.assertLess(sampled.r, 0.1)
        self.assertGreater(sampled.g, 0.45)
        self.assertGreater(sampled.b, 0.9)

    def test_fallback_coverage_uses_union_area_and_invalid_vector_falls_back(self):
        page, capture = self.capture()
        layout = StaticLayout([
            ReconstructedElement(id="unsupported-a", type="unsupported", x=0, y=0, width=60, height=100, zIndex=0, confidence=0.2),
            ReconstructedElement(id="unsupported-b", type="unsupported", x=20, y=0, width=60, height=100, zIndex=1, confidence=0.2),
            ReconstructedElement(id="bad-vector", type="vector", x=80, y=0, width=20, height=20, zIndex=2, confidence=1),
        ])

        document = reconstruct_document(capture, [page.id], StaticOcr(), layout, lambda *_: None, lambda: False)
        result_page = document.pages[0]

        self.assertAlmostEqual(result_page.metrics.fallback_coverage, 0.84, places=2)
        vector_fallback = next(node for node in result_page.children if node.id == f"{page.id}-bad-vector")
        self.assertEqual(vector_fallback.type, "raster-fallback")
        self.assertIn("SVG", vector_fallback.reason)

    def test_multi_page_model_ids_are_namespaced_across_the_document(self):
        first_page, capture = self.capture()
        second_page = first_page.model_copy(update={
            "id": "33333333-3333-4333-8333-333333333333",
            "index": 1,
        })
        capture.pages.append(second_page)
        layout = StaticLayout([
            ReconstructedElement(id="group", type="group", x=0, y=0, width=100, height=100, zIndex=0, confidence=1),
            ReconstructedElement(id="bg", type="rectangle", parentId="group", x=0, y=0, width=100, height=100, zIndex=1, confidence=1, fillColor="#ffffff"),
        ])

        document = reconstruct_document(
            capture,
            [first_page.id, second_page.id],
            StaticOcr(),
            layout,
            lambda *_: None,
            lambda: False,
        )

        ids = [node.id for page in document.pages for node in [page.children[0], *page.children[0].children]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(document.pages[0].children[0].id, f"{first_page.id}-group")
        self.assertEqual(document.pages[0].children[0].children[0].id, f"{first_page.id}-bg")
        self.assertEqual(document.pages[1].children[0].id, f"{second_page.id}-group")
        self.assertEqual(document.pages[1].children[0].children[0].id, f"{second_page.id}-bg")

    def test_ocr_line_joining_preserves_punctuation(self):
        blocks = [
            OcrBlock(id="hello", text="Hello", x=0, y=0, width=30, height=10, confidence=1),
            OcrBlock(id="comma", text=",", x=31, y=0, width=2, height=10, confidence=1),
            OcrBlock(id="world", text="world", x=35, y=0, width=30, height=10, confidence=1),
            OcrBlock(id="bang", text="!", x=66, y=0, width=2, height=10, confidence=1),
        ]

        self.assertEqual(group_ocr_into_lines(blocks)[0].text, "Hello, world!")

    def test_pixel_coordinate_layout_is_scaled_to_logical_canvas(self):
        page, capture = self.capture()
        layout = StaticLayout([
            ReconstructedElement(id="a", type="rectangle", x=20, y=20, width=80, height=80, zIndex=0, confidence=1, fillColor="#ffffff"),
            ReconstructedElement(id="b", type="ellipse", x=120, y=120, width=60, height=60, zIndex=1, confidence=1, fillColor="#ffffff"),
        ])

        document = reconstruct_document(capture, [page.id], StaticOcr(), layout, lambda *_: None, lambda: False)

        self.assertAlmostEqual(document.pages[0].children[0].box.x, 10)
        self.assertAlmostEqual(document.pages[0].children[1].box.x, 60)

    def test_text_hints_map_weight_italic_spacing_and_decoration(self):
        page, capture = self.capture()
        page.text_hints = [DomTextHint(
            text="Hello", x=10, y=10, width=30, height=10, fontSize=12,
            fontFamily="Example", fontWeight="600", fontStyle="italic",
            letterSpacing=1.5, textDecoration="underline", color="#112233",
        )]

        document = reconstruct_document(capture, [page.id], FakeOcr(), StaticLayout([]), lambda *_: None, lambda: False)
        run = next(node for node in document.pages[0].children if node.type == "text").runs[0]

        self.assertEqual(run.font_style, "Semi Bold Italic")
        self.assertEqual(run.letter_spacing, 1.5)
        self.assertEqual(run.text_decoration, "underline")

    def test_typography_normalizes_css_stack_and_compensates_line_metrics(self):
        page, capture = self.capture()
        page.text_hints = [DomTextHint(
            text="Hello", x=10, y=10, width=30, height=10, fontSize=14,
            fontFamily='"Montserrat", Arial, sans-serif', fontWeight="700",
            lineHeight=20, letterSpacing=0.5, textAlign="justify",
        )]

        document = reconstruct_document(capture, [page.id], FakeOcr(), StaticLayout([]), lambda *_: None, lambda: False)
        node = next(node for node in document.pages[0].children if node.type == "text")
        run = node.runs[0]

        self.assertEqual(normalize_font_family("system-ui, sans-serif"), "Inter")
        self.assertEqual(run.font_family, "Montserrat")
        self.assertEqual(run.font_style, "Bold")
        self.assertEqual(run.line_height, 20)
        self.assertEqual(node.box.height, 20)
        self.assertEqual(node.horizontal_align, "justify")

    def test_geometry_infers_only_confident_multi_element_groups(self):
        page, capture = self.capture()
        layout = StaticLayout([
            ReconstructedElement(id="card", type="group", x=5, y=5, width=90, height=80, zIndex=0, confidence=0.98),
            ReconstructedElement(id="shape-a", type="rectangle", x=10, y=10, width=30, height=20, zIndex=1, confidence=0.99, fillColor="#ffffff"),
            ReconstructedElement(id="shape-b", type="ellipse", x=55, y=15, width=20, height=20, zIndex=2, confidence=0.99, fillColor="#ffffff"),
            ReconstructedElement(id="outside", type="rectangle", x=0, y=90, width=10, height=10, zIndex=3, confidence=0.99, fillColor="#ffffff"),
        ])

        document = reconstruct_document(capture, [page.id], StaticOcr(), layout, lambda *_: None, lambda: False)
        card = next(node for node in document.pages[0].children if node.id.endswith("-card"))

        self.assertEqual({node.id.rsplit("-", 1)[-1] for node in card.children}, {"a", "b"})
        self.assertTrue(any(node.id.endswith("-outside") for node in document.pages[0].children))

    def test_simple_gradient_shadow_and_blur_become_native(self):
        page, capture = self.capture()
        layout = StaticLayout([ReconstructedElement(
            id="gradient-card", type="rectangle", x=10, y=10, width=80, height=50,
            zIndex=0, confidence=0.99, gradientStartColor="#ff0000",
            gradientEndColor="#0000ff", gradientAngle=45, shadowColor="rgba(0, 0, 0, 0.4)",
            shadowOffsetX=2, shadowOffsetY=4, shadowBlur=8, shadowSpread=1, blurRadius=2,
        )])

        document = reconstruct_document(capture, [page.id], StaticOcr(), layout, lambda *_: None, lambda: False)
        node = document.pages[0].children[0]

        self.assertEqual(node.fills[0].type, "linear-gradient")
        self.assertEqual(len(node.fills[0].stops), 2)
        self.assertEqual([effect.type for effect in node.effects], ["drop-shadow", "layer-blur"])

    def test_incomplete_effect_and_unsupported_svg_fall_back_regionally(self):
        page, capture = self.capture()
        layout = StaticLayout([
            ReconstructedElement(
                id="bad-gradient", type="rectangle", x=0, y=0, width=40, height=40,
                zIndex=0, confidence=1, gradientStartColor="#ffffff",
            ),
            ReconstructedElement(
                id="filtered-vector", type="vector", x=50, y=0, width=40, height=40,
                zIndex=1, confidence=1, svg="<svg><filter id='blur'/><rect width='40' height='40'/></svg>",
            ),
        ])

        document = reconstruct_document(capture, [page.id], StaticOcr(), layout, lambda *_: None, lambda: False)

        self.assertTrue(all(node.type == "raster-fallback" for node in document.pages[0].children))
        self.assertTrue(any(node.reason.startswith("Unsupported effect:") for node in document.pages[0].children))
        self.assertTrue(any(node.reason.startswith("Unsupported vector:") for node in document.pages[0].children))

    def test_ocr_inside_unmatched_raster_region_is_not_duplicated(self):
        page, capture = self.capture()
        layout = StaticLayout([
            ReconstructedElement(id="photo", type="image", x=0, y=0, width=100, height=100, zIndex=0, confidence=1),
        ])

        document = reconstruct_document(capture, [page.id], FakeOcr(), layout, lambda *_: None, lambda: False)

        self.assertFalse(any(node.type == "text" for node in document.pages[0].children))
        self.assertTrue(any("Suppressed 1 OCR" in warning for warning in document.pages[0].metrics.warnings))

    @patch("canva_converter.reconstruction.download_canva_asset")
    def test_verified_matching_canva_image_hint_uses_original_asset(self, download):
        page, capture = self.capture()
        page.image_hints = [DomImageHint(
            src="https://media-public.canva.com/original.png?token=private",
            x=10, y=15, width=60, height=40,
            naturalWidth=1200, naturalHeight=800,
            objectFit="cover", objectPositionX=0.5, objectPositionY=0.5,
            cornerRadius=8,
        )]
        original = Image.new("RGB", (120, 80), "purple")
        buffer = io.BytesIO()
        original.save(buffer, format="PNG")
        download.return_value = DownloadedCanvaAsset(buffer.getvalue(), "image/png", 120, 80)
        layout = StaticLayout([
            ReconstructedElement(id="photo", type="image", x=10, y=15, width=60, height=40, zIndex=0, confidence=1),
        ])

        document = reconstruct_document(capture, [page.id], StaticOcr(), layout, lambda *_: None, lambda: False)
        node = document.pages[0].children[0]
        asset = document.assets[node.fill.asset_id]

        self.assertEqual(node.source.extraction, "asset")
        self.assertEqual(node.fill.scale_mode, "fill")
        self.assertEqual(node.corner_radius, 8)
        self.assertEqual(asset.source, "canva")
        self.assertEqual((asset.width, asset.height), (120, 80))
        self.assertIsNone(node.reason)
        download.assert_called_once_with(page.image_hints[0].src)

    @patch("canva_converter.reconstruction.download_canva_asset", side_effect=ValueError("untrusted or expired asset"))
    def test_unavailable_or_uncertain_original_asset_preserves_crop_with_reason(self, download):
        page, capture = self.capture()
        page.image_hints = [DomImageHint(
            src="https://static.canva.com/expired.png",
            x=0, y=0, width=100, height=100,
            naturalWidth=1000, naturalHeight=1000,
            objectFit="cover", objectPositionX=0.5, objectPositionY=0.5,
        )]
        layout = StaticLayout([
            ReconstructedElement(id="photo", type="image", x=0, y=0, width=100, height=100, zIndex=0, confidence=1),
        ])

        document = reconstruct_document(capture, [page.id], StaticOcr(), layout, lambda *_: None, lambda: False)
        node = document.pages[0].children[0]
        asset = document.assets[node.fill.asset_id]

        self.assertEqual(node.source.extraction, "fallback")
        self.assertEqual(asset.source, "crop")
        self.assertIn("Original Canva asset unavailable", node.reason)
        self.assertTrue(any("untrusted or expired asset" in warning for warning in document.pages[0].metrics.warnings))

    @patch("canva_converter.reconstruction.download_canva_asset")
    def test_non_centered_cover_uses_a_clipped_original_image_group(self, download):
        page, capture = self.capture()
        page.image_hints = [DomImageHint(
            src="https://media-public.canva.com/wide.png",
            x=10, y=10, width=50, height=50,
            naturalWidth=200, naturalHeight=100,
            objectFit="cover", objectPositionX=1, objectPositionY=0.5,
            cornerRadius=5,
        )]
        original = Image.new("RGB", (200, 100), "orange")
        buffer = io.BytesIO()
        original.save(buffer, format="PNG")
        download.return_value = DownloadedCanvaAsset(buffer.getvalue(), "image/png", 200, 100)
        layout = StaticLayout([
            ReconstructedElement(id="photo", type="image", x=10, y=10, width=50, height=50, zIndex=0, confidence=1),
        ])

        document = reconstruct_document(capture, [page.id], StaticOcr(), layout, lambda *_: None, lambda: False)
        clip = document.pages[0].children[0]
        content = clip.children[0]

        self.assertEqual(clip.type, "group")
        self.assertTrue(clip.clips_content)
        self.assertEqual(clip.corner_radius, 5)
        self.assertEqual(content.type, "image")
        self.assertEqual(content.source.extraction, "asset")
        self.assertAlmostEqual(content.box.width, 100)
        self.assertAlmostEqual(content.box.x, -50)

    def test_rectangular_clip_groups_survive_reconstruction(self):
        page, capture = self.capture()
        layout = StaticLayout([
            ReconstructedElement(id="clip", type="group", x=10, y=10, width=50, height=50, zIndex=0, confidence=1, clipsContent=True),
            ReconstructedElement(id="inside", type="rectangle", parentId="clip", x=0, y=0, width=100, height=100, zIndex=1, confidence=1, fillColor="#ffffff"),
        ])

        document = reconstruct_document(capture, [page.id], StaticOcr(), layout, lambda *_: None, lambda: False)
        group = document.pages[0].children[0]

        self.assertTrue(group.clips_content)
        self.assertEqual(len(group.children), 1)

    def test_unsupported_mask_fallback_has_a_distinct_reason(self):
        page, capture = self.capture()
        layout = StaticLayout([
            ReconstructedElement(
                id="masked-photo", type="unsupported", x=0, y=0, width=50, height=50,
                zIndex=0, confidence=0.4, reason="Complex clipping path could not be represented",
            ),
        ])

        document = reconstruct_document(capture, [page.id], StaticOcr(), layout, lambda *_: None, lambda: False)
        fallback = document.pages[0].children[0]

        self.assertEqual(fallback.type, "raster-fallback")
        self.assertTrue(fallback.reason.startswith("Unsupported mask:"))

    def test_signed_asset_urls_are_not_sent_to_the_layout_model(self):
        page, capture = self.capture()
        page.image_hints = [DomImageHint(
            src="https://media-public.canva.com/image.png?signed=secret",
            x=10, y=10, width=30, height=30,
        )]
        layout = CapturingLayout([])

        reconstruct_document(capture, [page.id], StaticOcr(), layout, lambda *_: None, lambda: False)

        model_image_hints = layout.last_args[-1]
        self.assertEqual(len(model_image_hints), 1)
        self.assertNotIn("src", model_image_hints[0])
        self.assertEqual(model_image_hints[0]["x"], 10)


if __name__ == "__main__":
    unittest.main()
