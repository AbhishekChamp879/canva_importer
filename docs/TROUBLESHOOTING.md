# Troubleshooting

- **Retry Canva setup / OAuth not configured:** the running backend did not load Canva credentials at startup. Keep the real values in the project's `.env`, restart `run.py`, and click **Retry Canva setup**. The button remains usable even if the server was previously unavailable. `.env.example` is documentation, not a loaded configuration file.
- **Configuration fixed but plugin still looks disconnected:** click the connection control to refresh its status, or reopen the plugin. Retrying a stale disconnected state will not disconnect an account that has since connected.

- **Backend command not found:** install Python 3.11+ or use an available Python executable to create .venv. Then run .venv/Scripts/python.exe directly; activation is optional.
- **Missing Python module:** install requirements.txt using the same virtual environment used to start run.py.
- **Converter unavailable:** keep the backend running and open http://127.0.0.1:3000/api/health. The plugin expects localhost:3000.
- **Browser unavailable:** run python -m scripts.install_browser. Check PLAYWRIGHT_EXECUTABLE_PATH if using an override.
- **Old plugin button/name still visible:** close the plugin, restart the backend, and import the current figma-plugin/manifest.json again.
- **Private or login-only design:** make the link anonymously viewable, or configure the optional Canva connection.
- **Challenge or rate limit:** retry later and inspect the link in a normal browser.
- **Incomplete capture:** the viewer did not prove every page. Check the source and retry; a repeated cover image is not a valid replacement.
- **Capture busy:** wait for an active capture or cancel it.
- **Capture expired:** load the pages again.
- **Image or response too large:** use a smaller source design; each image is limited to 25 MiB.
- **OAuth setup error:** configure both client ID and secret, register the exact redirect URI, and restart.
- **OAuth disconnected after restart:** reconnect; user tokens are intentionally memory-only.
- **Import cancelled or failed:** frames created by the session are rolled back. Reopen the plugin if it was interrupted before receiving a response.

Images imported into Figma remain there after local captures expire.
