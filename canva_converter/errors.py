from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


STATUS_BY_CODE = {
    "INVALID_SOURCE": 400,
    "UNSUPPORTED_SOURCE": 422,
    "AUTH_REQUIRED": 401,
    "SOURCE_NOT_PUBLIC": 403,
    "SOURCE_CHALLENGED": 503,
    "SOURCE_RATE_LIMITED": 429,
    "SOURCE_UNAVAILABLE": 504,
    "CAPTURE_LIMIT_EXCEEDED": 413,
    "CAPTURE_DUPLICATE": 409,
    "CAPTURE_BUSY": 429,
    "CAPTURE_CANCELLED": 499,
    "CAPTURE_SETUP": 503,
    "CAPTURE_INCOMPLETE": 502,
    "CAPTURE_DUPLICATE_PAGE": 502,
    "MULTI_PAGE_CAPTURE_UNAVAILABLE": 502,
    "CAPTURE_FAILED": 502,
}


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status or STATUS_BY_CODE.get(code, 500)


class AcquisitionError(ServiceError):
    pass


_URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+", re.I)
_SECRET_PATTERN = re.compile(
    r"(?i)(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password)"
    r"(\s*[:=]\s*)([^\s,;&]+)"
)
_OPENAI_KEY_PATTERN = re.compile(r"\bsk-(?:proj-)?[0-9A-Za-z_-]{20,}\b")
_SENSITIVE_QUERY_KEYS = {"key", "api_key", "apikey", "access_token", "token", "auth", "signature", "sig"}
_CANVA_ROUTE_NAMES = {"view", "edit", "watch", "present", "play", "preview", "share"}


def _redact_url(match: re.Match[str]) -> str:
    raw = match.group(0)
    trailing = ""
    while raw and raw[-1] in ").,;]}":
        trailing = raw[-1] + trailing
        raw = raw[:-1]
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").casefold()
        path = parsed.path
        if host == "canva.link":
            path = "/[redacted-share-link]"
        elif host in {"canva.com", "www.canva.com"}:
            parts = path.split("/")
            try:
                design_position = next(index for index, part in enumerate(parts) if part.casefold() == "design")
            except StopIteration:
                design_position = -1
            token_position = design_position + 2
            if 0 <= token_position < len(parts) and parts[token_position].casefold() not in _CANVA_ROUTE_NAMES:
                parts[token_position] = "[redacted-share-token]"
                path = "/".join(parts)
        if not parsed.query:
            return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")) + trailing
        query = urlencode([
            (key, "[redacted]" if key.casefold() in _SENSITIVE_QUERY_KEYS else value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ])
        return urlunsplit((parsed.scheme, parsed.netloc, path, query, "")) + trailing
    except ValueError:
        return "[invalid-url]" + trailing


def public_error_message(error: Exception | str, fallback: str = "The operation failed.", limit: int = 1200) -> str:
    """Return a bounded, single-line message safe to persist and send to the plugin."""
    message = re.sub(r"\s+", " ", str(error)).strip() or fallback
    message = _URL_PATTERN.sub(_redact_url, message)
    message = _SECRET_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}[redacted]", message)
    message = _OPENAI_KEY_PATTERN.sub("[redacted-openai-key]", message)
    return message[:limit]
