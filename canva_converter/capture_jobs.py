from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import re
from threading import Event, Lock

from .acquisition import CanvaOAuthAcquisitionProvider, PublicCanvaAcquisitionProvider
from .errors import AcquisitionError, ServiceError, public_error_message
from .models import CaptureJob
from .store import ArtifactStore


class CaptureJobRunner:
    def __init__(
        self,
        store: ArtifactStore,
        capture: PublicCanvaAcquisitionProvider,
        max_workers: int = 2,
        oauth_capture: CanvaOAuthAcquisitionProvider | None = None,
    ):
        self.store = store
        self.capture = capture
        self.oauth_capture = oauth_capture
        self.max_workers = max_workers
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="canva-capture")
        self._signals: dict[str, Event] = {}
        self._lock = Lock()

    def submit(self, job: CaptureJob) -> None:
        with self._lock:
            if len(self._signals) >= self.max_workers:
                raise AcquisitionError("CAPTURE_BUSY", f"Capture capacity is busy ({self.max_workers} active). Retry shortly.")
            signal = Event()
            self._signals[job.id] = signal
        try:
            self.executor.submit(self._run, job.id, signal)
        except Exception:
            with self._lock:
                self._signals.pop(job.id, None)
            raise

    def cancel(self, job_id: str) -> CaptureJob | None:
        with self._lock:
            signal = self._signals.get(job_id)
            if signal:
                signal.set()
        return self.store.cancel_capture_job(job_id)

    def shutdown(self) -> None:
        with self._lock:
            for signal in self._signals.values():
                signal.set()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _run(self, job_id: str, signal: Event) -> None:
        try:
            job = self.store.get_capture_job(job_id)
            if not job:
                return
            if signal.is_set() or job.cancelled:
                self.store.cancel_capture_job(job_id)
                return
            self.store.update_capture_job(job_id, status="capturing", progress=1, message="Starting capture")

            def progress(message: str) -> None:
                live = self.store.get_capture_job(job_id)
                if signal.is_set() or not live or live.cancelled:
                    raise AcquisitionError("CAPTURE_CANCELLED", "Capture cancelled.")
                current, total = live.current_page, live.total_pages
                match = re.search(r"Capturing page (\d+) of (\d+)", message, re.I)
                completed = re.search(r"Page (\d+):", message, re.I)
                if match:
                    current, total = int(match.group(1)) - 1, int(match.group(2))
                elif completed:
                    current = int(completed.group(1))
                phase_progress = {
                    "resolving": 2, "launching": 4, "setup": 4,
                    "loading": 6, "detecting": 8, "completed": 96,
                }
                phase = next((name for name in phase_progress if f"[{name}]" in message), None)
                percent = phase_progress.get(phase, live.progress)
                if total and current is not None:
                    percent = max(percent, min(95, 8 + round((current / total) * 87)))
                self.store.update_capture_job(
                    job_id, current_page=current, total_pages=total,
                    progress=percent, message=message.split("]", 1)[-1].strip(),
                )

            provider = self.oauth_capture if job.source == "canva-oauth" else self.capture
            if provider is None:
                raise AcquisitionError("CANVA_OAUTH_NOT_CONFIGURED", "Canva OAuth acquisition is not configured.", 503)
            title, pages = provider.capture(job.url, progress, signal.is_set)
            if signal.is_set():
                raise AcquisitionError("CAPTURE_CANCELLED", "Capture cancelled.")
            record = self.store.put_capture(job.url, title, pages)
            self.store.update_capture_job(
                job_id, status="completed", progress=100, current_page=len(pages),
                total_pages=len(pages), capture_id=record.id,
                message=f"Captured {len(pages)} page(s)",
            )
        except Exception as error:
            live = self.store.get_capture_job(job_id)
            cancelled = signal.is_set() or bool(live and live.cancelled) or isinstance(error, AcquisitionError) and error.code == "CAPTURE_CANCELLED"
            code = "CAPTURE_CANCELLED" if cancelled else error.code if isinstance(error, ServiceError) else "CAPTURE_FAILED"
            message = "Capture cancelled." if cancelled else public_error_message(error, "Unknown capture error")
            self.store.update_capture_job(
                job_id, status="cancelled" if cancelled else "failed", message=message,
                error={"code": code, "message": message},
            )
        finally:
            with self._lock:
                self._signals.pop(job_id, None)
