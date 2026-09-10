from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _env_int(name: str, default: int, *, minimum: int, maximum: int | None = None) -> int:
    raw = os.environ.get(name)
    try:
        value = default if raw is None else int(raw.strip())
    except (AttributeError, ValueError) as error:
        raise RuntimeError(f"{name} must be an integer.") from error
    if value < minimum or maximum is not None and value > maximum:
        range_text = f"between {minimum} and {maximum}" if maximum is not None else f"at least {minimum}"
        raise RuntimeError(f"{name} must be {range_text}.")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be one of: 1, 0, true, false, yes, no, on, off.")


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    capture_concurrency: int
    capture_timeout_ms: int
    artifact_ttl_seconds: int
    max_capture_bytes: int
    max_api_response_bytes: int
    browser_executable_path: str | None
    browser_auto_install: bool
    browser_install_timeout_seconds: int
    store_root: Path
    canva_client_id: str | None = None
    canva_client_secret: str | None = None
    canva_redirect_uri: str | None = None

    @classmethod
    def load(cls) -> "Settings":
        load_env(SERVICE_ROOT / ".env")
        browser_path = os.environ.get("PLAYWRIGHT_EXECUTABLE_PATH") or None
        host = os.environ.get("HOST", "127.0.0.1").strip().casefold()
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError("HOST must be a loopback address for the local converter: 127.0.0.1, localhost, or ::1.")
        store_root = Path(os.environ.get("ARTIFACT_STORE", SERVICE_ROOT / ".jobs")).expanduser().resolve()
        port = _env_int("PORT", 3000, minimum=1, maximum=65535)
        canva_client_id = (os.environ.get("CANVA_CLIENT_ID") or "").strip() or None
        canva_client_secret = (os.environ.get("CANVA_CLIENT_SECRET") or "").strip() or None
        if bool(canva_client_id) != bool(canva_client_secret):
            raise RuntimeError("CANVA_CLIENT_ID and CANVA_CLIENT_SECRET must either both be set or both be empty.")
        if canva_client_id and len(canva_client_id) > 256:
            raise RuntimeError("CANVA_CLIENT_ID must not exceed 256 characters.")
        if canva_client_secret and len(canva_client_secret) > 1024:
            raise RuntimeError("CANVA_CLIENT_SECRET must not exceed 1024 characters.")
        canva_redirect_uri = (os.environ.get("CANVA_REDIRECT_URI") or f"http://127.0.0.1:{port}/api/canva/oauth/callback").strip()
        redirect = urlparse(canva_redirect_uri)
        valid_local = redirect.scheme == "http" and redirect.hostname == "127.0.0.1"
        valid_hosted = redirect.scheme == "https" and bool(redirect.hostname)
        if redirect.username or redirect.password or redirect.fragment or redirect.query or not (valid_local or valid_hosted):
            raise RuntimeError("CANVA_REDIRECT_URI must be HTTPS, or use http://127.0.0.1:<port> for local development.")
        if len(canva_redirect_uri) > 2048:
            raise RuntimeError("CANVA_REDIRECT_URI must not exceed 2048 characters.")
        return cls(
            host=host,
            port=port,
            capture_concurrency=_env_int("CAPTURE_CONCURRENCY", 2, minimum=1, maximum=8),
            capture_timeout_ms=_env_int("CAPTURE_TIMEOUT_MS", 1_800_000, minimum=10_000, maximum=3_600_000),
            artifact_ttl_seconds=_env_int("ARTIFACT_TTL_SECONDS", 3600, minimum=60, maximum=604_800),
            max_capture_bytes=_env_int("MAX_CAPTURE_BYTES", 512 * 1024 * 1024, minimum=25 * 1024 * 1024, maximum=2 * 1024 * 1024 * 1024),
            max_api_response_bytes=_env_int("MAX_API_RESPONSE_BYTES", 64 * 1024 * 1024, minimum=1024 * 1024, maximum=256 * 1024 * 1024),
            browser_executable_path=browser_path,
            browser_auto_install=_env_bool("PLAYWRIGHT_AUTO_INSTALL", True),
            browser_install_timeout_seconds=_env_int("PLAYWRIGHT_INSTALL_TIMEOUT_SECONDS", 900, minimum=60, maximum=3600),
            store_root=store_root,
            canva_client_id=canva_client_id,
            canva_client_secret=canva_client_secret,
            canva_redirect_uri=canva_redirect_uri,
        )
