# Engineering handbook

This project is a local Flask service and Figma development plugin for importing Canva pages as images. Every selected page becomes a frame containing one full-resolution image. Portrait, landscape, square, and mixed page dimensions are preserved.

Public links use Playwright capture. Optional Canva OAuth uses official PNG exports for fixed-size designs accessible to the connected account. Neither path requires an image-analysis service.

## Documentation

- [Setup and configuration](CONFIGURATION.md)
- [Architecture](ARCHITECTURE.md)
- [Capture pipeline](CAPTURE_PIPELINE.md)
- [Canva OAuth](CANVA_OAUTH.md)
- [Figma plugin](FIGMA_PLUGIN.md)
- [HTTP API](API.md)
- [Testing](TESTING.md)
- [Security](SECURITY.md)
- [Troubleshooting](TROUBLESHOOTING.md)
- [Development](DEVELOPMENT.md)
- [Status](STATUS.md)
- [Roadmap](ROADMAP.md)
- [Saved capture evaluation](evaluation/README.md)

## Repository map

- `canva_converter/`: Flask setup, capture API, configuration, capture jobs, models, and artifact storage.
- `canva_converter/acquisition/`: URL validation, browser selection, Canva capture, OAuth, page detection, and concurrency coordination.
- `figma-plugin/`: UI and Figma page-image renderer.
- `scripts/install_browser.py`: browser installation and launch check.
- `scripts/live_validate.py`: real capture and image-endpoint validation.
- `tests/`: deterministic backend and plugin checks.
- `run.py`: local service entry point.

The service is single-user and loopback-only. Public capture requires anonymous access; private designs require the optional Canva connection.
