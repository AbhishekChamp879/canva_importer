from __future__ import annotations

from contextlib import contextmanager
from threading import Lock

from ..errors import AcquisitionError


class CaptureCoordinator:
    def __init__(self, max_concurrency: int = 2):
        if max_concurrency < 1:
            raise ValueError("Capture concurrency must be positive")
        self.max_concurrency = max_concurrency
        self._active_keys: set[str] = set()
        self._lock = Lock()

    @contextmanager
    def acquire(self, key: str):
        with self._lock:
            if key in self._active_keys:
                raise AcquisitionError("CAPTURE_DUPLICATE", "This Canva URL is already being captured.")
            if len(self._active_keys) >= self.max_concurrency:
                raise AcquisitionError("CAPTURE_BUSY", f"Capture capacity is busy ({self.max_concurrency} active). Retry shortly.")
            self._active_keys.add(key)
        try:
            yield
        finally:
            with self._lock:
                self._active_keys.discard(key)

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active_keys)

