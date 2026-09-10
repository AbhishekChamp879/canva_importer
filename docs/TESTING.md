# Testing

Run deterministic checks from the project directory:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q canva_converter scripts tests run.py
.\.venv\Scripts\python.exe -m pip check
node --check figma-plugin/code.js
node tests/figma/renderer.test.js
node tests/figma/ui.test.js
```

Backend tests cover environment parsing, URL policies, browser selection, page detection, OAuth contracts, capture storage, asynchronous capture/cancellation, errors, and image endpoints. Plugin tests cover page dimensions, grid placement, acknowledgements, cancellation, rollback, and removed-feature boundaries. Deterministic tests mock external acquisition and do not call live Canva.

## Live capture

Start the backend and run:

```powershell
.\.venv\Scripts\python.exe -m scripts.live_validate "https://www.canva.com/design/DESIGN_ID/SHARE_TOKEN/view"
```

The validator checks page counts, contiguous indices, IDs, orientation, image URLs, MIME types, PNG dimensions, byte limits, and hashes. It does not establish visual correspondence between every Canva page and screenshot.

## Figma acceptance

Load an owned public design, compare every preview against Canva, select a subset, and import it. Confirm one image per frame, correct dimensions, page order, and distinct grid positions. Repeat with portrait, landscape, square, mixed-orientation, and intentionally duplicate pages.

Cancel during a multi-page import and confirm all frames created by that session disappear. Test official OAuth exports separately with an owned integration. Real Figma behavior still requires manual acceptance.
