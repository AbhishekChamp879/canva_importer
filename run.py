from __future__ import annotations

import os

os.environ.setdefault("FLASK_SKIP_DOTENV", "1")

from canva_converter import create_app


app = create_app()


if __name__ == "__main__":
    settings = app.extensions["canva_settings"]
    app.run(
        host=settings.host,
        port=settings.port,
        debug=False,
        threaded=True,
        use_reloader=False,
    )
