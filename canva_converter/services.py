from __future__ import annotations

from threading import Lock

from .acquisition import (
    CanvaConnectClient,
    CanvaOAuthAcquisitionProvider,
    CanvaOAuthManager,
    CaptureCoordinator,
    PublicCanvaAcquisitionProvider,
)
from .config import Settings
from .capture_jobs import CaptureJobRunner
from .store import ArtifactStore
from .editable_jobs import EditableJobs


class ServiceContainer:
    def __init__(self, settings: Settings):
        self.store = ArtifactStore(settings.store_root, settings.artifact_ttl_seconds, settings.max_capture_bytes)
        self.capture = PublicCanvaAcquisitionProvider(settings, CaptureCoordinator(settings.capture_concurrency))
        self.canva_oauth = CanvaOAuthManager(settings)
        self.canva_api = CanvaConnectClient(self.canva_oauth)
        self.oauth_capture = CanvaOAuthAcquisitionProvider(settings, self.canva_api)
        self.capture_jobs = CaptureJobRunner(
            self.store, self.capture, settings.capture_concurrency, oauth_capture=self.oauth_capture,
        )
        self.editable_jobs = EditableJobs(settings, self.store, self.canva_api)
        self._shutdown_lock = Lock()
        self._shutdown_complete = False

    def start_background_tasks(self) -> None:
        self.store.start_cleanup_worker()
        self.editable_jobs.start()

    def shutdown(self) -> None:
        with self._shutdown_lock:
            if self._shutdown_complete:
                return
            self._shutdown_complete = True
            self.store.stop_cleanup_worker()
            self.capture_jobs.shutdown()
            self.editable_jobs.shutdown()
