from __future__ import annotations

import re
import http.client
from dataclasses import dataclass
from urllib.parse import ParseResult, urlparse, urlunparse

from ..errors import AcquisitionError


MAX_SOURCE_URL_LENGTH = 4096
MAX_PATH_LENGTH = 2048
MAX_DESIGN_SEGMENT_LENGTH = 256
MAX_REDIRECTS = 10
SHORT_PATH = re.compile(r"^/[A-Za-z0-9_-]{1,256}/?$")
DESIGN_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
LOCALE_SEGMENT = re.compile(r"^[A-Za-z]{2}(?:[_-][A-Za-z]{2})?$")
ENCODED_PATH_SEPARATOR = re.compile(r"%(?:00|0a|0d|2f|5c)", re.I)
# Three-segment paths are ambiguous: the last segment can be either a route
# mode or a share token. Known route words distinguish the no-token form. A
# four-segment path can safely accept future Canva route names because the
# design ID and share token positions are already unambiguous and the route is
# discarded when the canonical /view URL is built.
NO_TOKEN_ROUTE_NAMES = {"view", "edit", "watch", "present", "play", "preview", "share"}


@dataclass(frozen=True)
class CanvaDesignPath:
    design_id: str
    share_token: str | None
    mode: str | None


def _clean_source(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > MAX_SOURCE_URL_LENGTH:
        return None
    if any(ord(character) <= 32 or ord(character) == 127 for character in cleaned):
        return None
    if "\\" in cleaned:
        return None
    return cleaned


def _parse_design_path(path: str) -> CanvaDesignPath | None:
    if len(path) > MAX_PATH_LENGTH or ENCODED_PATH_SEPARATOR.search(path):
        return None
    stripped = path.strip("/")
    if not stripped or "//" in stripped:
        return None
    parts = stripped.split("/")
    if len(parts) >= 2 and LOCALE_SEGMENT.fullmatch(parts[0]) and parts[1].lower() == "design":
        parts = parts[1:]
    if len(parts) < 2 or len(parts) > 4 or parts[0].lower() != "design":
        return None
    design_id = parts[1]
    if not DESIGN_SEGMENT.fullmatch(design_id):
        return None
    token: str | None = None
    mode: str | None = None
    if len(parts) == 3:
        third = parts[2].lower()
        if third in NO_TOKEN_ROUTE_NAMES:
            mode = third
        elif DESIGN_SEGMENT.fullmatch(parts[2]):
            token = parts[2]
        else:
            return None
    elif len(parts) == 4:
        token = parts[2]
        mode = parts[3].lower()
        if not DESIGN_SEGMENT.fullmatch(token) or not DESIGN_SEGMENT.fullmatch(parts[3]):
            return None
    return CanvaDesignPath(design_id=design_id, share_token=token, mode=mode)


def is_canva_host(parsed: ParseResult) -> bool:
    return _safe_https_authority(parsed) and (parsed.hostname or "").lower() in {"canva.com", "www.canva.com"}


def is_canva_asset_host(parsed: ParseResult) -> bool:
    host = (parsed.hostname or "").lower()
    return _safe_https_authority(parsed) and (host == "canva.com" or host.endswith(".canva.com"))


def _safe_https_authority(parsed: ParseResult) -> bool:
    try:
        return parsed.scheme == "https" and not parsed.username and not parsed.password and parsed.port in {None, 443}
    except ValueError:
        return False


def is_valid_canva_url(value: str) -> bool:
    try:
        cleaned = _clean_source(value)
        if cleaned is None:
            return False
        parsed = urlparse(cleaned)
        if not _safe_https_authority(parsed):
            return False
        host = (parsed.hostname or "").lower()
        return bool(
            (host == "canva.link" and SHORT_PATH.fullmatch(parsed.path))
            or (is_canva_host(parsed) and _parse_design_path(parsed.path))
        )
    except ValueError:
        return False


def is_valid_resolved_canva_url(value: str) -> bool:
    try:
        cleaned = _clean_source(value)
        if cleaned is None:
            return False
        parsed = urlparse(cleaned)
        return is_canva_host(parsed) and bool(_parse_design_path(parsed.path))
    except ValueError:
        return False


def is_allowed_redirect(value: str) -> bool:
    try:
        cleaned = _clean_source(value)
        if cleaned is None:
            return False
        parsed = urlparse(cleaned)
        return _safe_https_authority(parsed) and ((parsed.hostname or "").lower() == "canva.link" or is_canva_host(parsed))
    except ValueError:
        return False


def normalize_canva_view_url(value: str) -> str:
    cleaned = _clean_source(value)
    if cleaned is None or not is_valid_resolved_canva_url(cleaned):
        raise AcquisitionError("INVALID_SOURCE", "Cannot normalize an unsupported Canva URL.")
    parsed = urlparse(cleaned)
    design = _parse_design_path(parsed.path)
    if design is None:
        raise AcquisitionError("INVALID_SOURCE", "Cannot normalize an unsupported Canva URL.")
    path = f"/design/{design.design_id}{f'/{design.share_token}' if design.share_token else ''}/view"
    return urlunparse(("https", "www.canva.com", path, "", "", ""))


def resolve_canva_source_url(value: str, max_redirects: int = 5) -> str:
    cleaned = _clean_source(value)
    if cleaned is None or not is_valid_canva_url(cleaned):
        raise AcquisitionError("INVALID_SOURCE", "Use a Canva design/share URL or canva.link URL.")
    if not isinstance(max_redirects, int) or not 0 <= max_redirects <= MAX_REDIRECTS:
        raise ValueError(f"max_redirects must be between 0 and {MAX_REDIRECTS}.")
    parsed = urlparse(cleaned)
    if (parsed.hostname or "").lower() != "canva.link":
        return normalize_canva_view_url(cleaned)

    current = cleaned
    visited: set[str] = set()
    for _ in range(max_redirects + 1):
        if not is_allowed_redirect(current):
            raise AcquisitionError("INVALID_SOURCE", "Canva short link redirected to an untrusted host.")
        parsed_current = urlparse(current)
        loop_key = urlunparse((
            parsed_current.scheme.casefold(),
            (parsed_current.hostname or "").casefold(),
            parsed_current.path or "/",
            "",
            parsed_current.query,
            "",
        ))
        if loop_key in visited:
            raise AcquisitionError("SOURCE_UNAVAILABLE", "Canva short link entered a redirect loop.")
        visited.add(loop_key)
        if is_valid_resolved_canva_url(current):
            return normalize_canva_view_url(current)
        connection = http.client.HTTPSConnection(parsed_current.hostname, parsed_current.port or 443, timeout=15)
        request_path = parsed_current.path or "/"
        if parsed_current.query:
            request_path += f"?{parsed_current.query}"
        try:
            connection.request("GET", request_path, headers={
                "User-Agent": "Canva-Figma-Importer/1.0",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            })
            response = connection.getresponse()
            status = response.status
            location = response.getheader("Location")
            response.read(1024)
        except Exception as error:
            raise AcquisitionError("SOURCE_UNAVAILABLE", f"Could not resolve Canva short link: {error}") from error
        finally:
            connection.close()
        if status in {301, 302, 303, 307, 308} and location:
            from urllib.parse import urljoin

            candidate = urljoin(current, location)
            if not is_allowed_redirect(candidate):
                raise AcquisitionError("INVALID_SOURCE", "Canva short link redirected to an untrusted host.")
            current = candidate
            continue
        raise AcquisitionError("SOURCE_UNAVAILABLE", f"Canva short link returned HTTP {status} without a supported redirect.")
    if not is_valid_resolved_canva_url(current):
        raise AcquisitionError("INVALID_SOURCE", "Short URL redirected outside a supported Canva public design URL.")
    return normalize_canva_view_url(current)
