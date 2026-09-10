from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import secrets
import time
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image, UnidentifiedImageError

from ..config import Settings
from ..errors import AcquisitionError, ServiceError, public_error_message
from ..models import CapturedPage, new_id


CANVA_AUTHORIZE_URL = "https://www.canva.com/api/oauth/authorize"
CANVA_API_ORIGIN = "https://api.canva.com"
CANVA_TOKEN_PATH = "/rest/v1/oauth/token"
CANVA_REVOKE_PATH = "/rest/v1/oauth/revoke"
CANVA_SCOPES = ("design:meta:read", "design:content:read")
DESIGN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MAX_PAGE_IMAGE_BYTES = 25 * 1024 * 1024
MAX_PAGES = 500


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


@dataclass
class _PendingAuthorization:
    verifier: str
    expires_at: float


@dataclass
class _Tokens:
    access_token: str
    refresh_token: str
    expires_at: float
    scopes: tuple[str, ...]


class CanvaOAuthManager:
    """Single-user local OAuth state; credentials and tokens never leave Flask."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._pending: dict[str, _PendingAuthorization] = {}
        self._tokens: _Tokens | None = None
        self._lock = RLock()

    @property
    def configured(self) -> bool:
        return bool(self.settings.canva_client_id and self.settings.canva_client_secret and self.settings.canva_redirect_uri)

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._prune_pending()
            return {
                "configured": self.configured,
                "connected": self._tokens is not None,
                "scopes": list(self._tokens.scopes) if self._tokens else list(CANVA_SCOPES),
            }

    def begin(self) -> str:
        self._require_configured()
        verifier = secrets.token_urlsafe(72)
        state = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
        with self._lock:
            self._prune_pending()
            self._pending[state] = _PendingAuthorization(verifier=verifier, expires_at=time.time() + 600)
        query = urlencode({
            "code_challenge": challenge,
            "code_challenge_method": "s256",
            "scope": " ".join(CANVA_SCOPES),
            "response_type": "code",
            "client_id": self.settings.canva_client_id,
            "state": state,
            "redirect_uri": self.settings.canva_redirect_uri,
        })
        return f"{CANVA_AUTHORIZE_URL}?{query}"

    def complete(self, *, state: str, code: str) -> None:
        self._require_configured()
        if not state or not code or len(state) > 256 or len(code) > 8192:
            raise ServiceError("CANVA_OAUTH_INVALID_CALLBACK", "Canva returned an invalid authorization response.", 400)
        with self._lock:
            self._prune_pending()
            pending = self._pending.pop(state, None)
        if not pending or pending.expires_at <= time.time():
            raise ServiceError("CANVA_OAUTH_STATE_INVALID", "The Canva authorization request expired or its state did not match.", 400)
        payload = self._token_request({
            "grant_type": "authorization_code",
            "code_verifier": pending.verifier,
            "code": code,
            "redirect_uri": self.settings.canva_redirect_uri,
        })
        self._store_tokens(payload)

    def access_token(self) -> str:
        self._require_configured()
        with self._lock:
            tokens = self._tokens
            if not tokens:
                raise ServiceError("CANVA_NOT_CONNECTED", "Connect your Canva account first.", 401)
            # Canva rotates refresh tokens. Keep the refresh exchange inside the
            # same re-entrant lock so two concurrent page jobs cannot reuse an
            # already-rotated refresh token.
            if tokens.expires_at - time.time() <= 60:
                payload = self._token_request({"grant_type": "refresh_token", "refresh_token": tokens.refresh_token})
                self._store_tokens(payload)
                tokens = self._tokens
        if not tokens:
            raise ServiceError("CANVA_NOT_CONNECTED", "Reconnect your Canva account.", 401)
        return tokens.access_token

    def disconnect(self) -> None:
        with self._lock:
            tokens = self._tokens
            self._tokens = None
            self._pending.clear()
        if not tokens or not self.configured:
            return
        try:
            self._authenticated_form_request(CANVA_REVOKE_PATH, {"token": tokens.refresh_token})
        except Exception:
            # Local credentials are already removed. Remote revocation failure is
            # intentionally non-fatal and the rotating refresh token is not kept.
            pass

    def _store_tokens(self, payload: dict[str, Any]) -> None:
        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        expires_in = payload.get("expires_in")
        if not isinstance(access_token, str) or not isinstance(refresh_token, str) or not access_token or not refresh_token:
            raise ServiceError("CANVA_OAUTH_TOKEN_INVALID", "Canva returned an incomplete token response.", 502)
        try:
            lifetime = max(60, min(int(expires_in), 86_400))
        except (TypeError, ValueError) as error:
            raise ServiceError("CANVA_OAUTH_TOKEN_INVALID", "Canva returned an invalid token lifetime.", 502) from error
        scopes = tuple(str(payload.get("scope") or " ".join(CANVA_SCOPES)).split())
        if not set(CANVA_SCOPES).issubset(scopes):
            raise ServiceError("CANVA_OAUTH_SCOPE_MISSING", "Canva did not grant the required design read permissions.", 403)
        with self._lock:
            self._tokens = _Tokens(access_token, refresh_token, time.time() + lifetime, scopes)

    def _token_request(self, form: dict[str, str]) -> dict[str, Any]:
        try:
            return self._authenticated_form_request(CANVA_TOKEN_PATH, form)
        except ServiceError:
            raise
        except Exception as error:
            raise ServiceError("CANVA_OAUTH_FAILED", public_error_message(error, "Canva authorization failed."), 502) from error

    def _authenticated_form_request(self, path: str, form: dict[str, str]) -> dict[str, Any]:
        credentials = base64.b64encode(f"{self.settings.canva_client_id}:{self.settings.canva_client_secret}".encode("utf-8")).decode("ascii")
        request = Request(
            f"{CANVA_API_ORIGIN}{path}",
            data=urlencode(form).encode("ascii"),
            headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            method="POST",
        )
        return _read_json_response(request, timeout=30)

    def _require_configured(self) -> None:
        if not self.configured:
            raise ServiceError(
                "CANVA_OAUTH_NOT_CONFIGURED",
                "Set CANVA_CLIENT_ID and CANVA_CLIENT_SECRET, register the configured 127.0.0.1 callback in Canva, then restart the converter.",
                503,
            )

    def _prune_pending(self) -> None:
        now = time.time()
        self._pending = {key: value for key, value in self._pending.items() if value.expires_at > now}


class CanvaConnectClient:
    def __init__(self, auth: CanvaOAuthManager):
        self.auth = auth

    def list_designs(self, *, query: str = "", continuation: str = "") -> dict[str, Any]:
        params: dict[str, str | int] = {"limit": 50}
        if query:
            params["query"] = query
        if continuation:
            params["continuation"] = continuation
        payload = self._json("GET", f"/rest/v1/designs?{urlencode(params)}")
        designs = payload.get("items")
        if not isinstance(designs, list):
            raise ServiceError("CANVA_API_INVALID_RESPONSE", "Canva returned an invalid design list.", 502)
        return {"items": [self._public_design(item) for item in designs[:50]], "continuation": payload.get("continuation")}

    def get_design(self, design_id: str) -> dict[str, Any]:
        _validate_design_id(design_id)
        payload = self._json("GET", f"/rest/v1/designs/{quote(design_id, safe='')}")
        design = payload.get("design")
        if not isinstance(design, dict):
            raise ServiceError("CANVA_API_INVALID_RESPONSE", "Canva returned invalid design metadata.", 502)
        return design

    def get_pages(self, design_id: str) -> list[dict[str, Any]]:
        _validate_design_id(design_id)
        pages: list[dict[str, Any]] = []
        for offset in range(1, MAX_PAGES + 1, 200):
            payload = self._json("GET", f"/rest/v1/designs/{quote(design_id, safe='')}/pages?offset={offset}&limit=200")
            items = payload.get("items")
            if not isinstance(items, list):
                raise ServiceError("CANVA_API_INVALID_RESPONSE", "Canva returned invalid page metadata.", 502)
            pages.extend(item for item in items if isinstance(item, dict))
            if len(items) < 200:
                break
        return pages

    def export_pngs(self, design_id: str, *, is_cancelled: Callable[[], bool], progress: Callable[[str], None]) -> list[str]:
        _validate_design_id(design_id)
        created = self._json("POST", "/rest/v1/exports", {
            "design_id": design_id,
            "format": {"type": "png", "lossless": True, "as_single_image": False},
        })
        job = created.get("job")
        if not isinstance(job, dict) or not isinstance(job.get("id"), str):
            raise ServiceError("CANVA_EXPORT_INVALID_RESPONSE", "Canva did not return an export job.", 502)
        job_id = job["id"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if is_cancelled():
                raise AcquisitionError("CAPTURE_CANCELLED", "Capture cancelled.")
            status_payload = self._json("GET", f"/rest/v1/exports/{quote(job_id, safe='')}")
            current = status_payload.get("job")
            if not isinstance(current, dict):
                raise ServiceError("CANVA_EXPORT_INVALID_RESPONSE", "Canva returned invalid export status.", 502)
            status = current.get("status")
            if status == "success":
                urls = current.get("urls")
                if not isinstance(urls, list) or not urls or not all(isinstance(url, str) for url in urls):
                    raise ServiceError("CANVA_EXPORT_INVALID_RESPONSE", "Canva completed the export without page files.", 502)
                return urls
            if status == "failed":
                detail = current.get("error") if isinstance(current.get("error"), dict) else {}
                message = str(detail.get("message") or "Canva could not export this design.")
                raise ServiceError("CANVA_EXPORT_FAILED", public_error_message(message), 422)
            if status != "in_progress":
                raise ServiceError("CANVA_EXPORT_INVALID_RESPONSE", "Canva returned an unknown export status.", 502)
            progress("[exporting] Canva is preparing the official page export.")
            time.sleep(1)
        raise ServiceError("CANVA_EXPORT_TIMEOUT", "Canva did not finish the export within three minutes.", 504)

    def download_export(self, url: str) -> bytes:
        current = _validated_export_url(url)
        opener = build_opener(_NoRedirect())
        for _ in range(6):
            request = Request(current, headers={"Accept": "image/png", "User-Agent": "Canva-Figma-Importer/1.0"})
            try:
                response = opener.open(request, timeout=45)
            except HTTPError as error:
                if error.code in {301, 302, 303, 307, 308}:
                    location = error.headers.get("Location")
                    if not location:
                        raise ServiceError("CANVA_EXPORT_DOWNLOAD_FAILED", "Canva export redirect had no destination.", 502)
                    current = _validated_export_url(urljoin(current, location))
                    continue
                raise ServiceError("CANVA_EXPORT_DOWNLOAD_FAILED", f"Canva export download returned HTTP {error.code}.", 502) from error
            except URLError as error:
                raise ServiceError("CANVA_EXPORT_DOWNLOAD_FAILED", "The Canva export could not be downloaded.", 502) from error
            with response:
                content_type = response.headers.get_content_type()
                if content_type not in {"image/png", "application/octet-stream"}:
                    raise ServiceError("CANVA_EXPORT_DOWNLOAD_FAILED", "Canva export did not return a PNG image.", 502)
                if response.headers.get("Content-Encoding", "identity").casefold() not in {"", "identity"}:
                    raise ServiceError("CANVA_EXPORT_DOWNLOAD_FAILED", "Canva export used an unsupported content encoding.", 502)
                length = response.headers.get("Content-Length")
                if length:
                    try:
                        declared_length = int(length)
                    except ValueError as error:
                        raise ServiceError("CANVA_EXPORT_DOWNLOAD_FAILED", "Canva export returned an invalid Content-Length.", 502) from error
                    if declared_length < 0 or declared_length > MAX_PAGE_IMAGE_BYTES:
                        raise ServiceError("CAPTURE_LIMIT_EXCEEDED", "A Canva export page exceeds the 25MB image limit.", 413)
                data = response.read(MAX_PAGE_IMAGE_BYTES + 1)
                if not data or len(data) > MAX_PAGE_IMAGE_BYTES:
                    raise ServiceError("CAPTURE_LIMIT_EXCEEDED", "A Canva export page is empty or exceeds the 25MB image limit.", 413)
                return data
        raise ServiceError("CANVA_EXPORT_DOWNLOAD_FAILED", "Canva export exceeded the redirect limit.", 502)

    def _json(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if not path.startswith("/rest/v1/"):
            raise ValueError("Canva API path is outside the allowed API prefix.")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(
            f"{CANVA_API_ORIGIN}{path}", data=data, method=method,
            headers={
                "Authorization": f"Bearer {self.auth.access_token()}",
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        return _read_json_response(request, timeout=45)

    @staticmethod
    def _public_design(item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            return {}
        return {
            "id": item.get("id"),
            "title": item.get("title") or "Untitled Canva design",
            "pageCount": item.get("page_count"),
            "designTypes": item.get("design_types") if isinstance(item.get("design_types"), list) else [],
        }


class CanvaOAuthAcquisitionProvider:
    id = "canva-connect-oauth-png-v1"

    def __init__(self, settings: Settings, client: CanvaConnectClient):
        self.settings = settings
        self.client = client

    def capture(self, url: str, progress=None, is_cancelled=None) -> tuple[str, list[CapturedPage]]:
        progress = progress or (lambda _message: None)
        is_cancelled = is_cancelled or (lambda: False)
        design_id = _design_id_from_internal_url(url)
        progress("[resolving] Reading Canva design metadata through OAuth.")
        design = self.client.get_design(design_id)
        design_types = set(design.get("design_types") or [])
        if design_types.intersection({"doc", "whiteboard", "sheet", "video"}):
            raise AcquisitionError("UNSUPPORTED_SOURCE", "OAuth import currently supports fixed-size Canva designs only.")
        page_count = design.get("page_count")
        if isinstance(page_count, int) and page_count > MAX_PAGES:
            raise AcquisitionError("CAPTURE_LIMIT_EXCEEDED", f"Canva designs above {MAX_PAGES} pages are outside the current safety limit.")
        try:
            page_metadata = self.client.get_pages(design_id)
        except ServiceError:
            # The page endpoint is currently a Canva preview API. Official PNG
            # export remains sufficient when preview page metadata is absent.
            page_metadata = []
        urls = self.client.export_pngs(design_id, is_cancelled=is_cancelled, progress=progress)
        if page_count and len(urls) != page_count:
            raise AcquisitionError("CAPTURE_INCOMPLETE", f"Canva reported {page_count} pages but exported {len(urls)} page files.")
        if len(urls) > MAX_PAGES:
            raise AcquisitionError("CAPTURE_LIMIT_EXCEEDED", f"Canva designs above {MAX_PAGES} pages are outside the current safety limit.")
        pages: list[CapturedPage] = []
        captured_bytes = 0
        metadata_by_number = {item.get("page_number"): item for item in page_metadata if isinstance(item.get("page_number"), int)}
        for index, export_url in enumerate(urls):
            if is_cancelled():
                raise AcquisitionError("CAPTURE_CANCELLED", "Capture cancelled.")
            progress(f"[capturing] Capturing page {index + 1} of {len(urls)}.")
            exported = self.client.download_export(export_url)
            normalized, image_width, image_height = _normalize_png(exported)
            metadata = metadata_by_number.get(index + 1, {})
            dimensions = metadata.get("dimensions") if isinstance(metadata.get("dimensions"), dict) else {}
            width = _bounded_dimension(dimensions.get("width"), image_width)
            height = _bounded_dimension(dimensions.get("height"), image_height)
            captured_bytes += len(normalized)
            if captured_bytes > self.settings.max_capture_bytes:
                raise AcquisitionError("CAPTURE_LIMIT_EXCEEDED", "Official Canva page exports exceed the configured capture byte limit.")
            pages.append(CapturedPage(
                id=new_id(), index=index, width=width, height=height,
                screenshotBase64=base64.b64encode(normalized).decode("ascii"), textHints=[], imageHints=[],
            ))
            progress(f"[completed] Page {index + 1}: official Canva PNG ready.")
        if not pages:
            raise AcquisitionError("CAPTURE_FAILED", "Canva returned no fixed-size page exports.")
        return str(design.get("title") or "Canva design"), pages


def oauth_design_url(design_id: str) -> str:
    _validate_design_id(design_id)
    return f"https://www.canva.com/design/{design_id}/view"


def _design_id_from_internal_url(url: str) -> str:
    parts = [part for part in urlparse(url).path.split("/") if part]
    if len(parts) < 2 or parts[0] != "design":
        raise AcquisitionError("INVALID_SOURCE", "OAuth capture received an invalid Canva design reference.")
    return _validate_design_id(parts[1])


def _validate_design_id(value: str) -> str:
    if not isinstance(value, str) or not DESIGN_ID_PATTERN.fullmatch(value):
        raise ServiceError("INVALID_CANVA_DESIGN", "Canva design ID is invalid.", 400)
    return value


def _validated_export_url(value: str) -> str:
    try:
        parsed = urlparse(value)
    except ValueError as error:
        raise ServiceError("CANVA_EXPORT_DOWNLOAD_FAILED", "Canva returned an invalid export URL.", 502) from error
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port is not None or not (host == "canva.com" or host.endswith(".canva.com")):
        raise ServiceError("CANVA_EXPORT_DOWNLOAD_FAILED", "Canva returned an untrusted export URL.", 502)
    return value


def _normalize_png(data: bytes) -> tuple[bytes, int, int]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            width, height = image.size
            if width <= 0 or height <= 0 or width > 8192 or height > 8192 or width * height > 67_108_864:
                raise AcquisitionError("CAPTURE_LIMIT_EXCEEDED", "A Canva export page exceeds the supported image dimensions.")
            normalized = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            output = io.BytesIO()
            normalized.save(output, format="PNG", optimize=True)
            result = output.getvalue()
            if len(result) > MAX_PAGE_IMAGE_BYTES:
                raise AcquisitionError("CAPTURE_LIMIT_EXCEEDED", "A normalized Canva export page exceeds the 25MB image limit.")
            return result, width, height
    except AcquisitionError:
        raise
    except (UnidentifiedImageError, OSError) as error:
        raise ServiceError("CANVA_EXPORT_DOWNLOAD_FAILED", "Canva export bytes were not a valid image.", 502) from error


def _bounded_dimension(value: Any, fallback: int) -> int:
    try:
        dimension = round(float(value))
    except (TypeError, ValueError):
        dimension = fallback
    if dimension <= 0 or dimension > 8192:
        raise AcquisitionError("CAPTURE_LIMIT_EXCEEDED", "Canva page dimensions are outside the supported fixed-size range.")
    return dimension


def _read_json_response(request: Request, *, timeout: int) -> dict[str, Any]:
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ServiceError("CANVA_API_INVALID_RESPONSE", "Canva API response exceeded the 2MB limit.", 502)
    except HTTPError as error:
        try:
            detail = json.loads(error.read(64 * 1024).decode("utf-8"))
            message = detail.get("message") if isinstance(detail, dict) else None
        except Exception:
            message = None
        status = 401 if error.code == 401 else 403 if error.code == 403 else 429 if error.code == 429 else 502
        code = "CANVA_NOT_CONNECTED" if error.code == 401 else "CANVA_PERMISSION_DENIED" if error.code == 403 else "CANVA_RATE_LIMITED" if error.code == 429 else "CANVA_API_FAILED"
        raise ServiceError(code, public_error_message(message or f"Canva API returned HTTP {error.code}."), status) from error
    except URLError as error:
        raise ServiceError("CANVA_API_UNAVAILABLE", "The Canva API could not be reached.", 502) from error
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ServiceError("CANVA_API_INVALID_RESPONSE", "Canva returned invalid JSON.", 502) from error
    if not isinstance(payload, dict):
        raise ServiceError("CANVA_API_INVALID_RESPONSE", "Canva returned an invalid response object.", 502)
    return payload
