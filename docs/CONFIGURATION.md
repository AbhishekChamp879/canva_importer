# Configuration and Local Setup

## Requirements

- Python 3.11 or newer.
- Figma Desktop for loading the development plugin.
- Internet access to Canva for public capture.
- Optional Canva Connect integration credentials for authenticated/private-design export.
- Internet access to the OpenAI Responses API only for editable reconstruction.
- Node.js is optional and used only for JavaScript syntax/renderer tests.

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m scripts.install_browser
python run.py
```

`python -m scripts.install_browser` is optional. If skipped, the first capture resolves or installs a browser automatically.

## Environment loading

`Settings.load()` reads `.env` from the repository root. Existing process environment values win because file entries use `os.environ.setdefault`. Quotes around entire values are stripped. This is intentionally a small loader: shell expansion, multiline values, and advanced dotenv syntax are not supported.

Invalid values fail startup with a direct `RuntimeError`; the application does not silently fall back after malformed configuration.

## Environment reference

| Variable | Default | Validation | Purpose |
| --- | --- | --- | --- |
| `HOST` | `127.0.0.1` | `127.0.0.1`, `localhost`, or `::1` | Loopback bind address |
| `PORT` | `3000` | 1–65,535 | Flask listen port |
| `CAPTURE_CONCURRENCY` | `2` | 1–8 | Maximum simultaneous capture jobs |
| `CAPTURE_TIMEOUT_MS` | `1800000` | 10,000–3,600,000 | Whole capture deadline |
| `JOB_CONCURRENCY` | `2` | 1–2 | Maximum simultaneous reconstruction jobs |
| `ARTIFACT_TTL_SECONDS` | `3600` | 60–604,800 | Capture/job retention |
| `MAX_CAPTURE_BYTES` | `536870912` | 25 MB–2 GB | Decoded PNG total per capture |
| `MAX_API_RESPONSE_BYTES` | `67108864` | 1–256 MB | JSON response ceiling |
| `PLAYWRIGHT_AUTO_INSTALL` | `1` | explicit boolean | Install managed Chromium if none works |
| `PLAYWRIGHT_INSTALL_TIMEOUT_SECONDS` | `900` | 60–3,600 | Browser installation deadline |
| `PLAYWRIGHT_EXECUTABLE_PATH` | empty | existing file when set | Emergency browser executable override |
| `OPENAI_API_KEY` | empty | nonempty string | Required for editable vision OCR and layout analysis |
| `OPENAI_MODEL` | `gpt-5.6-luna` | 1–100 safe identifier characters | OpenAI model ID with image input and Structured Outputs support |
| `CANVA_CLIENT_ID` | empty | must be paired with client secret | Canva Connect OAuth client ID |
| `CANVA_CLIENT_SECRET` | empty | must be paired with client ID | Backend-only Canva Connect client secret |
| `CANVA_REDIRECT_URI` | `http://127.0.0.1:<PORT>/api/canva/oauth/callback` | HTTPS or local `127.0.0.1` HTTP URL; no credentials/query/fragment | Exact registered Canva OAuth callback |
| `ARTIFACT_STORE` | `<repo>/.jobs` | resolved filesystem path | Artifact storage root |
| `FLASK_ENV` | `development` | development/production/testing | Parsed application mode flag; currently does not change the Flask debug setting |

Accepted boolean strings are `1/0`, `true/false`, `yes/no`, and `on/off`, case-insensitively.

## Browser resolution

The launch order is:

1. `PLAYWRIGHT_EXECUTABLE_PATH`, only when explicitly set.
2. Playwright-managed Chromium already installed for this Python/Playwright environment.
3. Chrome channel discovered by Playwright.
4. Microsoft Edge channel discovered by Playwright.
5. One-time `python -m playwright install chromium` when auto-install is enabled.
6. Launch the newly installed managed Chromium.

No Windows, macOS, or Linux installation path is hardcoded. The optional executable path should normally remain empty.

On Linux, downloading Chromium may not install required shared libraries. Provision them with:

```bash
python -m playwright install --with-deps chromium
```

## AI provider configuration

Exact image import does not need AI credentials. Editable reconstruction uses two schema-constrained OpenAI Responses calls per page—one for vision OCR and one for layout analysis—so it requires one OpenAI Platform key:

```dotenv
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6-luna
```

The key is read only by the Flask process, sent to OpenAI through an `Authorization: Bearer` header, and never sent to the Figma plugin. Public errors redact OpenAI keys, sensitive query parameters, and common credential fields. Never put a real key in `.env.example`, source files, plugin files, screenshots, or issue reports.

`OPENAI_API_KEY` is an OpenAI Platform API key. A ChatGPT subscription by itself is not an API credential or API billing plan. `OPENAI_MODEL` can override the default, but the selected model must accept image input and support Structured Outputs through the Responses API.

Both OpenAI provider stages use a 90-second timeout per attempt and retry one malformed/invalid structured response. Because OCR and layout are separate calls, an editable page can make up to four Responses requests when both first attempts are invalid.

## Canva OAuth trial configuration

Canva OAuth is optional; public-link import works when it is unconfigured. `CANVA_CLIENT_ID` and `CANVA_CLIENT_SECRET` must be set together. Follow [Canva OAuth + Public Link Trial](CANVA_OAUTH.md) for Developer Portal and callback setup.

The callback default follows `PORT`. Canva requires the literal `127.0.0.1` host for local redirect registration; `localhost` is not accepted as a local Canva redirect. Tokens are held only in Flask memory, so restarting the backend intentionally disconnects Canva.

## Port coupling

Although Flask accepts a configurable `PORT`, the development plugin currently compiles `http://localhost:3000` in two places:

- `figma-plugin/ui.html`: `BACKEND_BASE`.
- `figma-plugin/manifest.json`: `devAllowedDomains`.

If the port changes, update both files and reload/reimport the development plugin. Changing only `.env` will make the plugin report a fetch failure.

## Figma installation

1. Start the backend.
2. In Figma Desktop, open a design file.
3. Open **Plugins → Development → Import plugin from manifest**.
4. Select `figma-plugin/manifest.json`.
5. Run **Canva to Editable Figma** from development plugins.

The manifest allows no production domains and only `http://localhost:3000` during development.

## Startup checks

```powershell
Invoke-RestMethod http://localhost:3000/api/health
python -m pip check
```

The health response should report `status=ok`, `runtime=python-flask`, browser portability, configured limits, `aiConfigured`, per-provider configuration flags, and `canvaOAuth`. It does not make a provider call, so configuration presence does not prove credential validity, permission, quota, or billing.
