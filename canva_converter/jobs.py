from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

from .errors import ServiceError, public_error_message
from .models import ReconstructionJob
from .providers import LayoutProvider, OcrProvider
from .reconstruction import reconstruct_document
from .store import ArtifactStore


class ReconstructionJobRunner:
    def __init__(self, store: ArtifactStore, ocr: OcrProvider, layout: LayoutProvider, max_workers: int = 2):
        self.store = store
        self.ocr = ocr
        self.layout = layout
        self.max_workers = max_workers
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="canva-reconstruct")
        self._signals: dict[str, Event] = {}
        self._lock = Lock()

    def submit(self, job: ReconstructionJob) -> None:
        with self._lock:
            if len(self._signals) >= self.max_workers:
                raise ServiceError("RECONSTRUCTION_BUSY", f"Reconstruction capacity is busy ({self.max_workers} active). Retry shortly.", 429)
            signal = Event()
            self._signals[job.id] = signal
        self.executor.submit(self._run, job.id, signal)

    def cancel(self, job_id: str) -> ReconstructionJob | None:
        with self._lock:
            signal = self._signals.get(job_id)
            if signal:
                signal.set()
        return self.store.cancel_job(job_id)

    def shutdown(self) -> None:
        with self._lock:
            for signal in self._signals.values():
                signal.set()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _run(self, job_id: str, signal: Event) -> None:
        job = self.store.get_job(job_id)
        if not job:
            return
        capture = self.store.get_capture(job.capture_id)
        if not capture:
            self.store.update_job(job_id, status="failed", message="Capture expired before reconstruction started.", error={"code": "CAPTURE_NOT_FOUND", "message": "Capture expired before reconstruction started."})
            return
        try:
            self.store.update_job(job_id, status="analyzing", message="Starting reconstruction", progress=1)

            def progress(current: int, message: str) -> None:
                live = self.store.get_job(job_id)
                if signal.is_set() or not live or live.cancelled:
                    raise RuntimeError("Job cancelled.")
                self.store.update_job(job_id, current_page=current, progress=round((current / job.total_pages) * 95), message=message)

            result = reconstruct_document(capture, job.page_ids, self.ocr, self.layout, progress, signal.is_set)
            if signal.is_set():
                raise RuntimeError("Job cancelled.")
            fallback_pages = sum(1 for page in result.pages if page.metrics.native_coverage == 0)
            message = "Ready to import"
            if fallback_pages:
                message += f" ({fallback_pages} page(s) preserved as full-page fallback)"
            self.store.update_job(job_id, status="completed", progress=100, current_page=job.total_pages, message=message, result=result)
        except Exception as error:
            live = self.store.get_job(job_id)
            cancelled = signal.is_set() or bool(live and live.cancelled)
            message = public_error_message(error, "Unknown reconstruction error")
            self.store.update_job(job_id, status="cancelled" if cancelled else "failed", message=message, error={"code": "CANCELLED" if cancelled else "RECONSTRUCTION_FAILED", "message": message})
        finally:
            with self._lock:
                self._signals.pop(job_id, None)
