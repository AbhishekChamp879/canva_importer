# Figma page importer

Import `figma-plugin/manifest.json` through Figma Desktop's development plugin menu. The plugin is named **Canva to Figma Pages**.

## User flow

Load a public Canva link, or connect Canva and load an accessible fixed-size design. Inspect thumbnails, choose pages, and click **Import page images**. Each page becomes a full-resolution image inside a correctly sized Figma frame. Mixed page dimensions and orientation are retained.

The UI provides select-all/clear-all, progress, cancellation, and an import summary. Binary images are transferred sequentially, with an acknowledgement after each page.

## UI and sandbox protocol

- begin-page-image-import: session ID, capture title/ID, selected page metadata.
- append-page-image: session ID, page ID, image bytes.
- finish-page-image-import: finish and select frames.
- cancel-page-image-import: roll back frames created by the session.
- open-external: open a validated Canva authorization URL.
- close-plugin: close the plugin.

Replies include page-image-import-ready, page-image-import-page, import-completed, and import-failed. Invalid metadata, duplicate pages, wrong sessions, empty images, and images over 25 MiB are rejected.

The renderer creates a frame and a single image rectangle per page, arranges frames on a grid, and stores capture/page/orientation metadata as plugin data.

## Network

The development manifest permits only `http://localhost:3000`. The UI talks to Flask; OAuth credentials stay in Flask. After updating the project, restart the backend and reopen the plugin. If Figma retains the old development entry, import the current manifest again.
