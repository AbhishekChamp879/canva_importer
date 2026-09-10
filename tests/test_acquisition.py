from __future__ import annotations

import base64
import io
import unittest

from PIL import Image

from canva_converter.acquisition.canva import PublicCanvaAcquisitionProvider
from canva_converter.acquisition.browser_state import classify_browser_state
from canva_converter.acquisition.coordinator import CaptureCoordinator
from canva_converter.acquisition.page_detection import score_page_candidate, screenshot_fingerprint, select_best_page_candidate
from canva_converter.errors import AcquisitionError


class AcquisitionCoreTests(unittest.TestCase):
    def test_page_identity_proof_uses_zero_based_and_one_based_canva_metadata(self):
        expected = 24
        self.assertTrue(PublicCanvaAcquisitionProvider._identity_is_expected("data-page-id:23", expected))
        self.assertTrue(PublicCanvaAcquisitionProvider._identity_is_expected("data-page-index:23", expected))
        self.assertTrue(PublicCanvaAcquisitionProvider._identity_is_expected("data-page-number:24", expected))
        self.assertTrue(PublicCanvaAcquisitionProvider._identity_is_expected("aria-page:24", expected))
        self.assertFalse(PublicCanvaAcquisitionProvider._identity_is_expected("data-page-id:4", expected))

    def test_scores_design_canvas_above_toolbar(self):
        viewport = {"width": 1920, "height": 1080}
        canvas = {"id": "canvas", "x": 400, "y": 120, "width": 1120, "height": 630, "tagName": "CANVAS", "className": "design-canvas"}
        toolbar = {"id": "toolbar", "x": 0, "y": 0, "width": 1800, "height": 250, "tagName": "DIV", "className": "toolbar navigation"}
        self.assertGreater(score_page_candidate(canvas, viewport), score_page_candidate(toolbar, viewport))

    def test_scores_stable_page_identity_above_shared_viewer_shell(self):
        viewport = {"width": 1920, "height": 1080}
        page = {"id": "page", "x": 193, "y": 73, "width": 1534, "height": 863, "tagName": "DIV", "dataPageId": "0"}
        viewer = {"id": "viewer", "x": 183, "y": 50, "width": 1554, "height": 1035, "tagName": "DIV", "className": "presentation viewer shell"}
        self.assertGreater(score_page_candidate(page, viewport), score_page_candidate(viewer, viewport))

    def test_rejects_whole_viewer_shell(self):
        score = score_page_candidate({"id": "shell", "x": 0, "y": 0, "width": 1900, "height": 1060, "tagName": "DIV"}, {"width": 1920, "height": 1080})
        self.assertEqual(score, float("-inf"))

    def test_capture_coordinator_rejects_duplicate(self):
        coordinator = CaptureCoordinator(2)
        with coordinator.acquire("same"):
            with self.assertRaisesRegex(AcquisitionError, "already being captured"):
                with coordinator.acquire("same"):
                    pass

    def test_fingerprint_is_stable(self):
        self.assertEqual(screenshot_fingerprint(b"page"), screenshot_fingerprint(b"page"))
        self.assertNotEqual(screenshot_fingerprint(b"page"), screenshot_fingerprint(b"other"))

    def test_candidate_selection_prefers_fixed_size_page(self):
        viewport = {"width": 1920, "height": 1080}
        candidates = [
            {"id": "navigation", "x": 0, "y": 0, "width": 1920, "height": 80, "tagName": "DIV", "className": "toolbar navigation"},
            {"id": "fixed-page", "x": 580, "y": 100, "width": 760, "height": 760, "tagName": "CANVAS", "dataPageNumber": "1"},
            {"id": "thumbnail", "x": 20, "y": 120, "width": 220, "height": 220, "tagName": "IMG", "className": "page thumbnail"},
        ]

        selected = select_best_page_candidate(candidates, viewport)

        self.assertEqual(selected["id"], "fixed-page")

    def test_candidate_detection_accepts_portrait_landscape_and_square_pages(self):
        viewport = {"width": 1920, "height": 1080}
        candidates = [
            {"id": "landscape", "x": 400, "y": 140, "width": 1120, "height": 630, "tagName": "CANVAS", "ariaLabel": "Page 1"},
            {"id": "portrait", "x": 720, "y": 80, "width": 480, "height": 850, "tagName": "CANVAS", "ariaLabel": "Page 2"},
            {"id": "square", "x": 560, "y": 100, "width": 760, "height": 760, "tagName": "CANVAS", "ariaLabel": "Page 3"},
        ]

        for candidate in candidates:
            with self.subTest(candidate=candidate["id"]):
                self.assertNotEqual(score_page_candidate(candidate, viewport), float("-inf"))

    def test_access_states_are_specific(self):
        common = {"url": "https://www.canva.com/design/ABC/token/view", "readyState": "complete", "responseStatus": 200}
        ready = {**common, "title": "Public design", "bodyText": "Page 1 of 1", "hasPageCandidate": True}
        private = {**common, "title": "Canva", "bodyText": "You don't have access. Request access.", "hasPageCandidate": False, "responseStatus": 403}
        challenge = {**common, "title": "Security check", "bodyText": "Verify you are human.", "hasPageCandidate": False}

        self.assertEqual(classify_browser_state(ready)[0], "ready")
        self.assertEqual(classify_browser_state(private)[0], "private")
        self.assertEqual(classify_browser_state(challenge)[0], "challenge")


class FakeNavigatingPage:
    def __init__(self):
        self.calls = 0

    async def evaluate(self, *_args):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("Execution context was destroyed, most likely because of a navigation")
        return {
            "url": "https://www.canva.com/design/ABC/token/view",
            "title": "Public design",
            "bodyText": "Ready",
            "readyState": "complete",
            "hasDocument": True,
            "hasPageCandidate": False,
        }


class FakePreviewResponse:
    def __init__(self, image_bytes):
        self.ok = True
        self.url = "https://static.canva.com/preview.png"
        self.headers = {"content-type": "image/png"}
        self.image_bytes = image_bytes

    async def body(self):
        return self.image_bytes


class FakePreviewRequest:
    def __init__(self, image_bytes):
        self.image_bytes = image_bytes

    async def get(self, *_args, **_kwargs):
        return FakePreviewResponse(self.image_bytes)


class FakePreviewPage:
    def __init__(self, image_bytes):
        self.context = type("Context", (), {"request": FakePreviewRequest(image_bytes)})()

    async def evaluate(self, *_args):
        return {"url": "https://static.canva.com/preview.png", "width": 100, "height": 50}


class FakePageCountPage:
    async def evaluate(self, *_args):
        return 37


class FakeLocator:
    def __init__(self, identity, box=None, parent=None, matches=None):
        self.identity = identity
        self._box = box
        self._parent = parent or self
        self._matches = matches if matches is not None else [self]

    async def count(self):
        return len(self._matches)

    def nth(self, index):
        return self._matches[index]

    async def bounding_box(self):
        return self._box

    def locator(self, selector):
        if selector == "xpath=..":
            return self._parent
        raise AssertionError(f"Unexpected locator selector: {selector}")


class FakeIndexedPage:
    def __init__(self, pages):
        self.pages = pages

    def locator(self, selector):
        if selector.startswith('[data-page-id="'):
            identity = selector.split('"')[1]
            matches = [item for item in self.pages if item.identity == identity]
            return FakeLocator("collection", matches=matches)
        if selector == "[data-page-id]":
            return FakeLocator("collection", matches=self.pages)
        return FakeLocator("collection", matches=[])


class FakeScaleLocator:
    async def evaluate(self, *_args):
        return 0.348


class AcquisitionBrowserRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_count_is_not_capped_at_ten(self):
        provider = object.__new__(PublicCanvaAcquisitionProvider)
        self.assertEqual(await provider._detect_page_count(FakePageCountPage()), 37)

    async def test_stable_snapshot_retries_destroyed_navigation_context(self):
        provider = object.__new__(PublicCanvaAcquisitionProvider)
        snapshot = await provider._stable_page_snapshot(FakeNavigatingPage())
        self.assertEqual(snapshot["readyState"], "complete")

    async def test_open_graph_preview_downloads_outside_page_cors_context(self):
        image = Image.new("RGB", (20, 10), "blue")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        image_bytes = buffer.getvalue()
        provider = object.__new__(PublicCanvaAcquisitionProvider)

        width, height, encoded = await provider._open_graph_preview(FakePreviewPage(image_bytes))

        self.assertEqual((width, height), (100, 50))
        self.assertEqual(base64.b64decode(encoded), image_bytes)

    async def test_finds_an_offscreen_page_by_its_stable_index(self):
        pages = [
            FakeLocator(str(index), {"x": 700, "y": index * 450, "width": 276, "height": 391})
            for index in range(18)
        ]
        provider = object.__new__(PublicCanvaAcquisitionProvider)

        locator = await provider._find_page_locator(FakeIndexedPage(pages), 18)

        self.assertIs(locator, pages[17])

    async def test_slideshow_page_without_index_requires_navigation(self):
        current_page_only = [FakeLocator("0", {"x": 193, "y": 73, "width": 1534, "height": 863})]
        provider = object.__new__(PublicCanvaAcquisitionProvider)

        locator = await provider._find_page_locator(FakeIndexedPage(current_page_only), 2)

        self.assertIsNone(locator)

    async def test_duplicate_page_requires_authoritative_page_evidence(self):
        provider = object.__new__(PublicCanvaAcquisitionProvider)

        self.assertTrue(provider._duplicate_is_authoritative(2, 2, "data-page-id:0", "data-page-id:0"))
        self.assertTrue(provider._duplicate_is_authoritative(2, None, "data-page-id:opaque-b", "data-page-id:opaque-a"))
        self.assertFalse(provider._duplicate_is_authoritative(2, None, "data-page-id:0", "data-page-id:0"))
        self.assertFalse(provider._duplicate_is_authoritative(2, None, None, None))

    async def test_expands_accessibility_marker_to_fixed_page_box(self):
        page_box = FakeLocator("page-box", {"x": 700, "y": 4500, "width": 276, "height": 391})
        marker = FakeLocator("page-marker", {"x": 700, "y": 4500, "width": 0.3, "height": 0.3}, parent=page_box)
        provider = object.__new__(PublicCanvaAcquisitionProvider)

        locator = await provider._nearest_fixed_page_box(marker, object())

        self.assertIs(locator, page_box)

    async def test_recovers_logical_scale_independent_of_viewer_zoom(self):
        provider = object.__new__(PublicCanvaAcquisitionProvider)
        self.assertAlmostEqual(await provider._logical_scale(FakeScaleLocator()), 0.348)

    async def test_normalizes_page_image_to_two_times_logical_size(self):
        source = Image.new("RGB", (80, 40), "green")
        buffer = io.BytesIO()
        source.save(buffer, format="PNG")

        normalized = PublicCanvaAcquisitionProvider._normalize_page_image(buffer.getvalue(), 100, 50)

        with Image.open(io.BytesIO(normalized)) as image:
            self.assertEqual(image.size, (200, 100))

    async def test_normalizes_each_page_without_changing_its_orientation(self):
        source = Image.new("RGB", (300, 300), "green")
        buffer = io.BytesIO()
        source.save(buffer, format="PNG")
        cases = [((1600, 900), (3200, 1800)), ((900, 1600), (1800, 3200)), ((1080, 1080), (2160, 2160))]

        for logical, expected in cases:
            with self.subTest(logical=logical):
                normalized = PublicCanvaAcquisitionProvider._normalize_page_image(buffer.getvalue(), *logical)
                with Image.open(io.BytesIO(normalized)) as image:
                    self.assertEqual(image.size, expected)


if __name__ == "__main__":
    unittest.main()
