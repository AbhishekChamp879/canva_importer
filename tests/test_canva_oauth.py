from __future__ import annotations

import base64
import io
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from PIL import Image

from canva_converter.acquisition.oauth import CanvaOAuthAcquisitionProvider, CanvaOAuthManager
from canva_converter.config import Settings
from canva_converter.errors import ServiceError


def settings_for_test(root: str) -> Settings:
    return Settings(
        host="127.0.0.1", port=3000, capture_concurrency=2, capture_timeout_ms=60_000,
        job_concurrency=1, artifact_ttl_seconds=3600, max_capture_bytes=64 * 1024 * 1024,
        max_api_response_bytes=8 * 1024 * 1024, browser_executable_path=None,
        browser_auto_install=False, browser_install_timeout_seconds=60, openai_api_key=None,
        openai_model="gpt-test", store_root=Path(root), development=True,
        canva_client_id="client-test", canva_client_secret="secret-test",
        canva_redirect_uri="http://127.0.0.1:3000/api/canva/oauth/callback",
    )


class FakeConnectClient:
    def __init__(self):
        self.images = [self._png("red", (160, 90)), self._png("blue", (90, 160))]

    @staticmethod
    def _png(color: str, size: tuple[int, int]) -> bytes:
        output = io.BytesIO()
        Image.new("RGB", size, color).save(output, format="PNG")
        return output.getvalue()

    def get_design(self, design_id):
        return {"id": design_id, "title": "Owned presentation", "page_count": 2, "design_types": ["presentation"]}

    def get_pages(self, _design_id):
        return [
            {"page_number": 1, "dimensions": {"width": 160, "height": 90}},
            {"page_number": 2, "dimensions": {"width": 90, "height": 160}},
        ]

    def export_pngs(self, _design_id, **_kwargs):
        return ["https://export-download.canva.com/page-1", "https://export-download.canva.com/page-2"]

    def download_export(self, url):
        return self.images[0 if url.endswith("page-1") else 1]


class CanvaOAuthTests(unittest.TestCase):
    def test_authorization_uses_pkce_state_and_never_exposes_secret_or_verifier(self):
        with tempfile.TemporaryDirectory() as root:
            manager = CanvaOAuthManager(settings_for_test(root))
            authorization_url = manager.begin()
            parsed = urlparse(authorization_url)
            query = parse_qs(parsed.query)
            self.assertEqual(parsed.scheme, "https")
            self.assertEqual(parsed.netloc, "www.canva.com")
            self.assertEqual(query["code_challenge_method"], ["s256"])
            self.assertEqual(query["scope"], ["design:meta:read design:content:read"])
            self.assertIn("state", query)
            self.assertNotIn("secret-test", authorization_url)
            self.assertNotIn("code_verifier", query)

            with patch.object(manager, "_token_request", return_value={
                "access_token": "access", "refresh_token": "refresh", "expires_in": 3600,
                "scope": "design:meta:read design:content:read",
            }) as exchange:
                manager.complete(state=query["state"][0], code="authorization-code")
            self.assertTrue(manager.status()["connected"])
            self.assertEqual(manager.access_token(), "access")
            self.assertIn("code_verifier", exchange.call_args.args[0])

    def test_callback_state_is_one_time_and_required(self):
        with tempfile.TemporaryDirectory() as root:
            manager = CanvaOAuthManager(settings_for_test(root))
            state = parse_qs(urlparse(manager.begin()).query)["state"][0]
            with self.assertRaises(ServiceError) as mismatch:
                manager.complete(state="wrong-state", code="code")
            self.assertEqual(mismatch.exception.code, "CANVA_OAUTH_STATE_INVALID")
            with patch.object(manager, "_token_request", return_value={
                "access_token": "access", "refresh_token": "refresh", "expires_in": 3600,
                "scope": "design:meta:read design:content:read",
            }):
                manager.complete(state=state, code="code")
                with self.assertRaises(ServiceError):
                    manager.complete(state=state, code="code")

    def test_official_export_becomes_ordered_captured_pages_with_mixed_orientation(self):
        with tempfile.TemporaryDirectory() as root:
            provider = CanvaOAuthAcquisitionProvider(settings_for_test(root), FakeConnectClient())
            title, pages = provider.capture("https://www.canva.com/design/DTest123/view")
            self.assertEqual(title, "Owned presentation")
            self.assertEqual([(page.index, page.width, page.height, page.orientation) for page in pages], [
                (0, 160, 90, "landscape"), (1, 90, 160, "portrait"),
            ])
            self.assertNotEqual(pages[0].screenshot_base64, pages[1].screenshot_base64)
            for page, expected in zip(pages, [(160, 90), (90, 160)], strict=True):
                with Image.open(io.BytesIO(base64.b64decode(page.screenshot_base64))) as image:
                    self.assertEqual(image.size, expected)


if __name__ == "__main__":
    unittest.main()
