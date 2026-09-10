# Canva to Figma Pages

Import selected Canva pages into Figma as full-resolution images, with one correctly sized frame per page.

## Run the backend

Use Python 3.11 or newer. From this project directory in PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m scripts.install_browser
.\.venv\Scripts\python.exe run.py
```

If you already have a virtual environment, start with the last command. The health endpoint is http://127.0.0.1:3000/api/health.

The defaults work without an environment file. Copy `.env.example` to `.env` only if you do not already have one and need custom settings or optional Canva OAuth credentials.

## Import pages

1. In Figma Desktop, choose Plugins → Development → Import plugin from manifest.
2. Select `figma-plugin/manifest.json` and run **Canva to Figma Pages**.
3. Paste an accessible Canva link and choose **Load pages**, or connect Canva and select a design.
4. Select pages and choose **Import page images**.
5. Keep the backend running until import completes. Cancel rolls back frames created by that import.

See the [engineering handbook](docs/README.md) for configuration, supported sources, API contracts, testing, and limitations.
