# Architecture

## Data flow

1. The plugin submits a public Canva URL or a selected OAuth design.
2. Flask validates the request and creates a capture job.
3. A bounded worker acquires pages through Playwright or official Canva PNG exports.
4. ArtifactStore writes PNGs and capture metadata under `.jobs/captures`.
5. The plugin polls job status, fetches previews, and displays page selection.
6. Selected full-resolution images are downloaded and sent to Figma one at a time.
7. Figma creates a frame and image rectangle for each page and acknowledges each transfer.
8. Completion selects the imported frames. Cancellation or a fatal import error removes the session's frames.

## Components

`create_app` constructs Settings and ServiceContainer. The container owns the artifact store, public capture provider, OAuth manager/client/provider, and capture worker pool. Shutdown stops cleanup and signals captures to cancel.

`models.py` contains capture pages, records, jobs, requests, and structured job errors. The page contract includes ID, index, logical dimensions, orientation, and PNG data. Public responses use thumbnail data and a separate full-image URL.

## Storage and lifecycle

Capture metadata and PNGs are separate files; metadata is committed last with an atomic replace. Capture-job metadata lives in `.jobs/capture-jobs`. In-memory capture caches still retain image data.

Captures expire at an absolute timestamp; capture jobs expire relative to their last update. Cleanup runs in the service and on access, not while the service is stopped. Old capture files containing optional per-layer hints can still be read; those fields are discarded.

Workers are process-local. Restart does not resume unfinished jobs. There is no database, durable queue, or multi-user isolation.
