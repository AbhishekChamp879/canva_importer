from __future__ import annotations

import http.client
import io
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from PIL import Image, ImageOps

from .acquisition.url_policy import MAX_SOURCE_URL_LENGTH, is_canva_asset_host


MAX_ORIGINAL_ASSET_BYTES = 25 * 1024 * 1024
MAX_ORIGINAL_ASSET_PIXELS = 100_000_000
MAX_ORIGINAL_ASSET_DIMENSION = 32_768
MAX_ASSET_REDIRECTS = 5
SUPPORTED_CONTENT_TYPES = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/jpg": "JPEG",
    "image/gif": "GIF",
    "image/webp": "WEBP",
    "image/avif": "AVIF",
}
OUTPUT_MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif"}


@dataclass(frozen=True)
class DownloadedCanvaAsset:
    data: bytes
    mime_type: str
    width: int
    height: int


def _validated_asset_url(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_SOURCE_URL_LENGTH:
        raise ValueError("Canva asset URL is missing or too long.")
    parsed = urlparse(value)
    if parsed.fragment or not is_canva_asset_host(parsed):
        raise ValueError("Canva asset URL is not a trusted HTTPS Canva asset URL.")
    return value


def _decode_and_normalize(data: bytes, declared_content_type: str) -> DownloadedCanvaAsset:
    expected_format = SUPPORTED_CONTENT_TYPES.get(declared_content_type)
    if expected_format is None:
        raise ValueError("Canva asset response has an unsupported image content type.")
    try:
        with Image.open(io.BytesIO(data)) as source:
            actual_format = (source.format or "").upper()
            if actual_format != expected_format:
                raise ValueError("Canva asset bytes do not match the declared image content type.")
            width, height = source.size
            if not 0 < width <= MAX_ORIGINAL_ASSET_DIMENSION or not 0 < height <= MAX_ORIGINAL_ASSET_DIMENSION:
                raise ValueError("Canva asset dimensions exceed the supported limit.")
            if width * height > MAX_ORIGINAL_ASSET_PIXELS:
                raise ValueError("Canva asset decoded pixel count exceeds the supported limit.")
            if actual_format in OUTPUT_MIME_TYPES:
                source.verify()
                return DownloadedCanvaAsset(data=data, mime_type=OUTPUT_MIME_TYPES[actual_format], width=width, height=height)

            # Figma's embedded-image path is deliberately limited to PNG/JPEG/GIF.
            # Decode modern Canva formats in the backend and emit a validated PNG.
            normalized = ImageOps.exif_transpose(source).convert("RGBA")
            buffer = io.BytesIO()
            normalized.save(buffer, format="PNG", optimize=True)
            converted = buffer.getvalue()
            if len(converted) > MAX_ORIGINAL_ASSET_BYTES:
                raise ValueError("Normalized Canva asset exceeds the 25MB image limit.")
            return DownloadedCanvaAsset(data=converted, mime_type="image/png", width=normalized.width, height=normalized.height)
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError("Canva asset response is not a safely decodable image.") from error


def download_canva_asset(url: str, *, timeout: float = 20, max_redirects: int = MAX_ASSET_REDIRECTS) -> DownloadedCanvaAsset:
    current = _validated_asset_url(url)
    if not isinstance(max_redirects, int) or not 0 <= max_redirects <= MAX_ASSET_REDIRECTS:
        raise ValueError(f"max_redirects must be between 0 and {MAX_ASSET_REDIRECTS}.")
    visited: set[str] = set()

    for redirect_index in range(max_redirects + 1):
        current = _validated_asset_url(current)
        if current in visited:
            raise ValueError("Canva asset URL entered a redirect loop.")
        visited.add(current)
        parsed = urlparse(current)
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout)
        try:
            connection.request("GET", path, headers={
                "User-Agent": "Canva-Figma-Importer/1.0",
                "Accept": "image/png,image/jpeg,image/gif,image/webp,image/avif",
                "Accept-Encoding": "identity",
            })
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                response.read(1024)
                if not location or redirect_index >= max_redirects:
                    raise ValueError("Canva asset redirect budget was exhausted.")
                current = _validated_asset_url(urljoin(current, location))
                continue
            if response.status != 200:
                response.read(1024)
                raise ValueError(f"Canva asset returned HTTP {response.status}.")
            if (response.getheader("Content-Encoding") or "identity").casefold() not in {"", "identity"}:
                raise ValueError("Canva asset response used an unsupported content encoding.")
            content_type = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().casefold()
            if content_type not in SUPPORTED_CONTENT_TYPES:
                raise ValueError("Canva asset response has an unsupported image content type.")
            raw_length = response.getheader("Content-Length")
            if raw_length:
                try:
                    content_length = int(raw_length)
                except ValueError as error:
                    raise ValueError("Canva asset returned an invalid Content-Length header.") from error
                if content_length < 0:
                    raise ValueError("Canva asset returned an invalid Content-Length header.")
                if content_length > MAX_ORIGINAL_ASSET_BYTES:
                    raise ValueError("Canva asset exceeds the 25MB image limit.")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ORIGINAL_ASSET_BYTES:
                    raise ValueError("Canva asset exceeds the 25MB image limit.")
                chunks.append(chunk)
            if not chunks:
                raise ValueError("Canva asset response was empty.")
            return _decode_and_normalize(b"".join(chunks), content_type)
        finally:
            connection.close()
    raise ValueError("Canva asset redirect budget was exhausted.")
