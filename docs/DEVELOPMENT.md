# Development

The runtime uses Flask, Pillow, Playwright, and Pydantic. The plugin uses plain JavaScript and HTML and has no bundler. Use Python 3.11+ and Node.js for the JavaScript checks.

Create a virtual environment, install requirements.txt, and run the checks in TESTING.md. Start the backend with run.py and load the development plugin from its manifest.

Keep acquisition, storage, HTTP contracts, and Figma rendering separate. Acquisition returns CapturedPage objects with PNG data and logical dimensions. The API returns previews and full-image URLs. The plugin downloads each selected image and waits for Figma's acknowledgement before continuing.

Do not introduce production dependencies on test fixtures, recorded Canva responses, or particular share links. Preserve URL validation, page identity checks, bounded capture workers, image limits, cancellation, and rollback.

Use temporary storage and mocked external acquisition in deterministic tests. Use owned designs for live acceptance and avoid committing share tokens or credentials.

The artifact store reads older capture metadata by discarding optional per-layer hint fields. Runtime data under .jobs and credentials in .env are ignored by Git.
