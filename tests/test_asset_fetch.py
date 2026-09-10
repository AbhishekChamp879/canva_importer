from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from PIL import Image

from canva_converter.asset_fetch import MAX_ORIGINAL_ASSET_BYTES, download_canva_asset


def png_bytes(width: int = 20, height: int = 10) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "blue").save(buffer, format="PNG")
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None):
        self.status = status
        self._body = io.BytesIO(body)
        self._headers = {key.casefold(): value for key, value in (headers or {}).items()}

    def getheader(self, name: str):
        return self._headers.get(name.casefold())

    def read(self, amount: int = -1):
        return self._body.read(amount)


class FakeConnection:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.requests = []

    def request(self, method, path, headers=None):
        self.requests.append((method, path, headers))

    def getresponse(self):
        return self.response

    def close(self):
        pass


class CanvaAssetFetchTests(unittest.TestCase):
    @patch("canva_converter.asset_fetch.http.client.HTTPSConnection")
    def test_downloads_and_validates_a_trusted_canva_png(self, connection_type):
        data = png_bytes()
        connection = FakeConnection(FakeResponse(200, data, {
            "Content-Type": "image/png",
            "Content-Length": str(len(data)),
        }))
        connection_type.return_value = connection

        result = download_canva_asset("https://media-public.canva.com/example.png?token=private")

        self.assertEqual(result.data, data)
        self.assertEqual(result.mime_type, "image/png")
        self.assertEqual((result.width, result.height), (20, 10))
        self.assertEqual(connection.requests[0][0], "GET")
        self.assertEqual(connection.requests[0][1], "/example.png?token=private")

    @patch("canva_converter.asset_fetch.http.client.HTTPSConnection")
    def test_revalidates_redirects_and_rejects_an_untrusted_target(self, connection_type):
        connection_type.return_value = FakeConnection(FakeResponse(302, headers={
            "Location": "https://attacker.example/image.png",
        }))

        with self.assertRaisesRegex(ValueError, "trusted HTTPS Canva"):
            download_canva_asset("https://media-public.canva.com/redirect")

        self.assertEqual(connection_type.call_count, 1)

    @patch("canva_converter.asset_fetch.http.client.HTTPSConnection")
    def test_rejects_mime_signature_mismatch_and_oversized_content(self, connection_type):
        connection_type.return_value = FakeConnection(FakeResponse(200, png_bytes(), {
            "Content-Type": "image/jpeg",
        }))
        with self.assertRaisesRegex(ValueError, "do not match"):
            download_canva_asset("https://static.canva.com/not-really-jpeg")

        connection_type.return_value = FakeConnection(FakeResponse(200, b"", {
            "Content-Type": "image/png",
            "Content-Length": str(MAX_ORIGINAL_ASSET_BYTES + 1),
        }))
        with self.assertRaisesRegex(ValueError, "25MB"):
            download_canva_asset("https://static.canva.com/too-large.png")

    def test_rejects_non_canva_and_credentialed_asset_urls_before_network(self):
        for url in [
            "https://attacker.example/image.png",
            "https://canva.com.attacker.example/image.png",
            "https://user:password@static.canva.com/image.png",
            "http://static.canva.com/image.png",
        ]:
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "trusted HTTPS Canva"):
                download_canva_asset(url)


if __name__ == "__main__":
    unittest.main()
