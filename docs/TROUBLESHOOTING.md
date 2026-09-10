# Troubleshooting

## Converter is offline / failed to fetch

Check:

```powershell
Invoke-RestMethod http://localhost:3000/api/health
Get-NetTCPConnection -LocalPort 3000 -ErrorAction SilentlyContinue
```

Start `python run.py`. The plugin is compiled for `http://localhost:3000`; if `PORT` changed, update both `BACKEND_BASE` in `figma-plugin/ui.html` and `devAllowedDomains` in `figma-plugin/manifest.json`, then reload the plugin.

A 30-second request timeout can also appear as an unavailable converter if the server thread is blocked or restarting.

## Manifest rejects localhost

The manifest should contain `http://localhost:3000` only under `devAllowedDomains`, with `allowedDomains: ["none"]`. Figma manifest validation may reject an IP-form URL even when the service binds to `127.0.0.1). Keep the plugin URL as `localhost`.

## Browser setup error

Run:

```powershell
python -m scripts.install_browser
```

Normally leave `PLAYWRIGHT_EXECUTABLE_PATH` empty. If set, it must point to an existing browser executable on that machine. If automatic installation is disabled, install manually:

```powershell
python -m playwright install chromium
```

On Linux:

```bash
python -m playwright install --with-deps chromium
```

Corporate proxies, antivirus, restricted home directories, or missing Linux libraries can prevent download/launch.

## `INVALID_SOURCE`

Use an HTTPS Canva design/share URL or `canva.link`. General Canva home, template, folder, team, or arbitrary asset URLs are not design sources. Lookalike domains, custom ports, credentials in URLs, and malformed/encoded paths are rejected intentionally.

## `SOURCE_NOT_PUBLIC` or `AUTH_REQUIRED`

The source is not anonymously viewable. In Canva, configure link access for anyone with the link and view permission, then test in a private/incognito browser while signed out. Normalizing `/edit` to `/view` cannot bypass permissions.

## `SOURCE_CHALLENGED` or `SOURCE_RATE_LIMITED`

Canva presented an anti-automation challenge or rate limit. Wait and retry later. Do not add challenge bypasses or link-specific code. A commercial solution should prefer an official authenticated/export integration where available.

## `SOURCE_UNAVAILABLE`

The viewer or navigation did not stabilize before the configured capture deadline. Check internet access, open the public URL manually, increase `CAPTURE_TIMEOUT_MS` within its allowed range for very large designs, and run a fresh live validator.

## Same page repeated / duplicate-page failure

Current capture should reject unproven equal screenshots with `CAPTURE_DUPLICATE_PAGE` instead of importing page 1 repeatedly. If equal pages are intentional, Canva must expose distinct page number or identity for acceptance.

If visually wrong pages still pass:

1. Save no private tokens in a bug report.
2. Record viewer style (scrolling document or slideshow).
3. Compare every plugin thumbnail against Canva.
4. Run `python -m scripts.live_validate` and retain only counts/dimensions/hashes.
5. Add a generic detector/navigation regression test; never hardcode the failing URL.

## `CAPTURE_INCOMPLETE` or `MULTI_PAGE_CAPTURE_UNAVAILABLE`

Canva reported more pages than the public viewer allowed the browser to locate/navigate. The service intentionally returns no partial capture. Confirm anonymous viewer access and whether all pages are visible. Open Graph cover previews cannot supply a full multi-page design.

## `CAPTURE_LIMIT_EXCEEDED`

One of these limits was exceeded:

- logical page above 8,192 pixels;
- page PNG above 25 MB;
- total capture above `MAX_CAPTURE_BYTES`;
- reconstructed asset above 25 MB.

Increase only the configurable total capture limit if the host has enough memory/disk. Per-page bounds are code-level safety limits.

## `CAPTURE_BUSY`, `CAPTURE_DUPLICATE`, or `RECONSTRUCTION_BUSY`

The bounded worker capacity is full or the canonical same design is already being captured. Wait for the active job to finish/cancel. Increasing capture concurrency raises browser memory use. Reconstruction concurrency is capped at two.

## AI providers not configured

Exact images still work. For editable mode set:

```dotenv
OPENAI_API_KEY=...
```

Restart the backend and confirm `aiConfigured: true`, `providers.ocrConfigured: true`, and `providers.layoutConfigured: true` from `/api/health`. Both provider flags reflect the same OpenAI key; presence does not prove that it has correct permissions, quota, or billing.

Settings are loaded once when Flask starts. Editing `.env` does not update an already-running process. If health remains false after a restart, ensure only one process is listening on port 3000; a stale listener can otherwise serve an older configuration.

## Editable import differs from Canva

Expected alpha limitations include font substitution, crop-based images, approximate grouping, and rasterized masks/gradients/effects. Review:

- missing font list;
- fallback reasons;
- native/fallback coverage;
- hidden `QA Reference`;
- text and visual QA warnings.

Use exact page images when visual fidelity matters more than editability.

Image-layer reasons distinguish `Screenshot crop`, `Original Canva asset unavailable`, and `Unsupported mask`. Original recovery deliberately falls back when the URL is not a trusted Canva host, redirects outside Canva, expires, fails image validation, uses unsupported positioning/stretching, or cannot be matched confidently to one layout region.

## Import hangs in Figma

- Exact image pages have 60-second per-page main-thread acknowledgement deadlines.
- Editable rendering has a five-minute UI watchdog.
- Very large images/decks can stress Figma memory.

Cancel and retry fewer selected pages. A cancelled exact-image session should remove partial frames. If it does not, capture the plugin console error and reproduce with the isolated Figma API simulation before modifying transaction code.

## Stale jobs or disk usage

Artifacts live under `.jobs` and default to one-hour TTL. The cleanup worker runs only while the non-test app process is alive; store access also sweeps.

After stopping the service, verify the absolute path and remove only the repository's `.jobs` directory if immediate cleanup is required. Do not delete a broad parent directory.

## Verification commands

```powershell
python -m unittest discover -s tests -v
python -m compileall -q canva_converter scripts tests run.py
python -m pip check
node --check figma-plugin/code.js
node tests/figma/renderer.test.js
python -m scripts.live_validate "<public Canva design URL>"
```

See [Testing](TESTING.md) for what each command proves and does not prove.
