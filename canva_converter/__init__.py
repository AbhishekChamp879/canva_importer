from __future__ import annotations

import atexit
from urllib.parse import urlparse

from flask import Flask

from .config import Settings
from .routes import api
from .services import ServiceContainer


def create_app(test_config: dict | None = None) -> Flask:
    settings = Settings.load()
    app = Flask(__name__)
    app.config.update(
        JSON_SORT_KEYS=False,
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        TESTING=False,
    )
    if test_config:
        app.config.update(test_config)

    services = ServiceContainer(settings)
    if not app.config["TESTING"]:
        services.start_background_tasks()
        atexit.register(services.shutdown)
    app.extensions["canva_settings"] = settings
    app.extensions["canva_services"] = services
    app.register_blueprint(api)

    @app.after_request
    def apply_cors(response):
        from flask import request

        origin = request.headers.get("Origin")
        if _origin_allowed(origin):
            response.headers["Access-Control-Allow-Origin"] = origin or "null"
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET,POST,DELETE,OPTIONS"
            response.headers["Access-Control-Expose-Headers"] = "ETag,Content-Length,X-Content-SHA256"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    return app


def _origin_allowed(origin: str | None) -> bool:
    if not origin or origin == "null":
        return True
    try:
        parsed = urlparse(origin)
        hostname = (parsed.hostname or "").lower()
        if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return False
        if parsed.scheme == "http" and hostname in {"localhost", "127.0.0.1", "::1"}:
            return True
        return parsed.scheme == "https" and (hostname == "figma.com" or hostname.endswith(".figma.com"))
    except ValueError:
        return False
