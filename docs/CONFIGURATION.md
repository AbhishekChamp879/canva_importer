# Configuration

Use Python 3.11 or newer and install `requirements.txt` in a virtual environment. Run `python -m scripts.install_browser` once, then `python run.py`. See the root README for PowerShell commands.

Settings load from environment variables and then the project `.env`; existing process environment values take precedence. Defaults work for public capture without credentials.

| Variable | Default | Purpose |
| --- | --- | --- |
| HOST | 127.0.0.1 | Must be loopback |
| PORT | 3000 | Backend port |
| CAPTURE_CONCURRENCY | 2 | Simultaneous capture workers; range 1–8 |
| CAPTURE_TIMEOUT_MS | 1800000 | Public browser capture deadline |
| ARTIFACT_TTL_SECONDS | 3600 | Artifact retention while cleanup runs |
| MAX_CAPTURE_BYTES | 536870912 | Maximum image bytes per capture |
| MAX_API_RESPONSE_BYTES | 67108864 | Maximum JSON response size |
| ARTIFACT_STORE | project/.jobs | Local artifact directory |
| PLAYWRIGHT_EXECUTABLE_PATH | empty | Optional browser executable override |
| PLAYWRIGHT_AUTO_INSTALL | 1 | Install managed Chromium if needed |
| PLAYWRIGHT_INSTALL_TIMEOUT_SECONDS | 900 | Browser installation timeout |
| CANVA_CLIENT_ID | empty | Optional Canva integration client ID |
| CANVA_CLIENT_SECRET | empty | Optional Canva integration secret |
| CANVA_REDIRECT_URI | http://127.0.0.1:3000/api/canva/oauth/callback | OAuth callback; default follows backend port |
| OPENAI_API_KEY | empty | Optional cloud font suggestions; backend only |
| FONT_AI_MODEL | gpt-4.1-mini | Image-capable Responses API model for user-triggered font suggestions |

The plugin UI and development manifest permit `http://localhost:3000`. Keep port 3000 unless you update both `figma-plugin/ui.html` and `figma-plugin/manifest.json` consistently.

The service uses a local Flask server with debug and reloader disabled. Credentials stay in the backend. PDF conversion additionally uses pinned pypdfium2 and psutil for extraction and worker resource monitoring. The optional AI client uses Python's standard HTTPS client; no OpenAI key is needed for deterministic conversion.

See EDITABLE_IMPORT.md for fixed conversion limits and font fallback behavior. Never replace an existing `.env` with the sample: preserve the configured Canva credentials.
