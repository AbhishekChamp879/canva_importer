from __future__ import annotations

import json
import os
import base64
import hashlib
import shutil
import time
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Any

from .errors import ServiceError
from .models import CaptureJob, CaptureRecord, ReconstructionJob, new_id, utc_now


class ArtifactStore:
    def __init__(self, root: Path, ttl_seconds: int = 3600, max_capture_bytes: int = 512 * 1024 * 1024):
        self.root = root
        self.ttl_seconds = ttl_seconds
        self.max_capture_bytes = max_capture_bytes
        self.capture_root = root / "captures"
        self.capture_job_root = root / "capture-jobs"
        self.job_root = root / "jobs"
        self.job_asset_root = root / "job-assets"
        self.figma_qa_root = root / "figma-qa"
        self.capture_root.mkdir(parents=True, exist_ok=True)
        self.capture_job_root.mkdir(parents=True, exist_ok=True)
        self.job_root.mkdir(parents=True, exist_ok=True)
        self.job_asset_root.mkdir(parents=True, exist_ok=True)
        self.figma_qa_root.mkdir(parents=True, exist_ok=True)
        self._captures: dict[str, CaptureRecord] = {}
        self._capture_jobs: dict[str, CaptureJob] = {}
        self._jobs: dict[str, ReconstructionJob] = {}
        self._lock = RLock()
        self._sweeper_stop = Event()
        self._sweeper_thread: Thread | None = None

    def start_cleanup_worker(self, interval_seconds: int = 60) -> None:
        with self._lock:
            if self._sweeper_thread and self._sweeper_thread.is_alive():
                return
            interval = max(5, min(interval_seconds, max(5, self.ttl_seconds // 2)))
            self._sweeper_stop.clear()

            def run() -> None:
                while not self._sweeper_stop.wait(interval):
                    self.sweep()

            self._sweeper_thread = Thread(target=run, name="canva-artifact-sweeper", daemon=True)
            self._sweeper_thread.start()

    def stop_cleanup_worker(self) -> None:
        self._sweeper_stop.set()
        thread = self._sweeper_thread
        if thread and thread.is_alive():
            thread.join(timeout=2)

    @staticmethod
    def _path(root: Path, record_id: str) -> Path:
        return root / f"{record_id}.json"

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_suffix(f".{os.getpid()}.{new_id()}.tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, path)

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _capture_directory(root: Path, record_id: str) -> Path:
        return root / record_id

    def _read_capture_directory(self, record_id: str) -> CaptureRecord | None:
        directory = self._capture_directory(self.capture_root, record_id)
        raw = self._read(directory / "metadata.json")
        if not raw:
            return None
        pages = []
        try:
            for page in raw.get("pages", []):
                page_payload = dict(page)
                image_file = page_payload.pop("_imageFile")
                image_bytes = (directory / image_file).read_bytes()
                page_payload["screenshotBase64"] = base64.b64encode(image_bytes).decode("ascii")
                pages.append(page_payload)
            raw["pages"] = pages
            return CaptureRecord.model_validate(raw)
        except (OSError, KeyError, ValueError):
            return None

    @staticmethod
    def _asset_filename(asset_id: str, mime_type: str) -> str:
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif"}.get(mime_type, ".bin")
        return hashlib.sha256(asset_id.encode("utf-8")).hexdigest() + extension

    def _persist_job(self, job: ReconstructionJob) -> None:
        payload = job.json_dict()
        result = payload.get("result")
        if result:
            directory = self.job_asset_root / job.id
            directory.mkdir(parents=True, exist_ok=True)
            for asset_id, asset in result.get("assets", {}).items():
                encoded = asset.pop("dataBase64", None)
                if not encoded:
                    continue
                image_bytes = base64.b64decode(encoded, validate=True)
                filename = self._asset_filename(asset_id, asset.get("mimeType", ""))
                (directory / filename).write_bytes(image_bytes)
                asset["_dataFile"] = filename
        self._atomic_write(self._path(self.job_root, job.id), payload)

    def _read_persisted_job(self, job_id: str) -> ReconstructionJob | None:
        raw = self._read(self._path(self.job_root, job_id))
        if not raw:
            return None
        result = raw.get("result")
        try:
            if result:
                for asset in result.get("assets", {}).values():
                    filename = asset.pop("_dataFile", None)
                    if filename:
                        image_bytes = (self.job_asset_root / job_id / filename).read_bytes()
                        asset["dataBase64"] = base64.b64encode(image_bytes).decode("ascii")
            return ReconstructionJob.model_validate(raw)
        except (OSError, ValueError):
            return None

    def sweep(self) -> None:
        now = time.time()
        with self._lock:
            for record_id, capture in list(self._captures.items()):
                if capture.expires_at <= now:
                    self._captures.pop(record_id, None)
            for path in self.capture_root.glob("*.json"):
                raw = self._read(path)
                if not raw or float(raw.get("expiresAt", 0)) <= now:
                    path.unlink(missing_ok=True)
            for directory in self.capture_root.iterdir():
                if not directory.is_dir():
                    continue
                raw = self._read(directory / "metadata.json")
                if not raw or float(raw.get("expiresAt", 0)) <= now:
                    shutil.rmtree(directory, ignore_errors=True)
                    self._captures.pop(directory.name, None)
            for path in self.job_root.glob("*.json"):
                raw = self._read(path)
                updated = raw.get("updatedAt") if raw else None
                try:
                    timestamp = __import__("datetime").datetime.fromisoformat(str(updated).replace("Z", "+00:00")).timestamp()
                except Exception:
                    timestamp = 0
                if timestamp + self.ttl_seconds <= now:
                    path.unlink(missing_ok=True)
                    self._jobs.pop(path.stem, None)
                    shutil.rmtree(self.job_asset_root / path.stem, ignore_errors=True)
                    self._path(self.figma_qa_root, path.stem).unlink(missing_ok=True)
            for path in self.capture_job_root.glob("*.json"):
                raw = self._read(path)
                updated = raw.get("updatedAt") if raw else None
                try:
                    timestamp = __import__("datetime").datetime.fromisoformat(str(updated).replace("Z", "+00:00")).timestamp()
                except Exception:
                    timestamp = 0
                if timestamp + self.ttl_seconds <= now:
                    path.unlink(missing_ok=True)
                    self._capture_jobs.pop(path.stem, None)

    def put_capture(self, source_url: str, title: str, pages) -> CaptureRecord:
        self.sweep()
        record = CaptureRecord(
            id=new_id(),
            sourceUrl=source_url,
            title=title,
            createdAt=utc_now(),
            expiresAt=time.time() + self.ttl_seconds,
            pages=pages,
        )
        with self._lock:
            directory = self._capture_directory(self.capture_root, record.id)
            directory.mkdir(parents=False, exist_ok=False)
            total_bytes = 0
            metadata = record.json_dict()
            stored_pages = []
            try:
                for page in metadata["pages"]:
                    page_payload = dict(page)
                    encoded = page_payload.pop("screenshotBase64")
                    image_bytes = base64.b64decode(encoded, validate=True)
                    total_bytes += len(image_bytes)
                    if total_bytes > self.max_capture_bytes:
                        raise ServiceError(
                            "CAPTURE_LIMIT_EXCEEDED",
                            f"Captured pages exceed the configured {self.max_capture_bytes // (1024 * 1024)}MB design limit.",
                        )
                    filename = f"{page_payload['id']}.png"
                    (directory / filename).write_bytes(image_bytes)
                    page_payload["_imageFile"] = filename
                    stored_pages.append(page_payload)
                metadata["pages"] = stored_pages
                # Metadata is written last, so an interrupted directory is
                # never treated as a complete capture and is removed by sweep.
                self._atomic_write(directory / "metadata.json", metadata)
            except Exception:
                shutil.rmtree(directory, ignore_errors=True)
                raise
            self._captures[record.id] = record
        return record

    def get_capture(self, record_id: str) -> CaptureRecord | None:
        self.sweep()
        with self._lock:
            record = self._captures.get(record_id)
            if not record:
                record = self._read_capture_directory(record_id)
                if not record:
                    raw = self._read(self._path(self.capture_root, record_id))
                    record = CaptureRecord.model_validate(raw) if raw else None
            if not record or record.expires_at <= time.time():
                return None
            self._captures[record_id] = record
            return record

    def create_capture_job(self, url: str, *, source: str = "public-url") -> CaptureJob:
        self.sweep()
        now = utc_now()
        job = CaptureJob(
            id=new_id(), url=url, source=source, status="queued", progress=0, totalPages=0,
            message="Queued", createdAt=now, updatedAt=now, cancelled=False,
        )
        with self._lock:
            self._capture_jobs[job.id] = job
            self._atomic_write(self._path(self.capture_job_root, job.id), job.json_dict())
        return job

    def get_capture_job(self, job_id: str) -> CaptureJob | None:
        self.sweep()
        with self._lock:
            job = self._capture_jobs.get(job_id)
            if not job:
                raw = self._read(self._path(self.capture_job_root, job_id))
                job = CaptureJob.model_validate(raw) if raw else None
                if job:
                    self._capture_jobs[job_id] = job
            return job

    def update_capture_job(self, job_id: str, **patch) -> CaptureJob:
        with self._lock:
            job = self.get_capture_job(job_id)
            if not job:
                raise KeyError("Capture job not found")
            payload = job.json_dict(exclude_none=False)
            aliases = {
                "current_page": "currentPage", "total_pages": "totalPages",
                "capture_id": "captureId", "created_at": "createdAt", "updated_at": "updatedAt",
            }
            for key, value in patch.items():
                payload[aliases.get(key, key)] = value.json_dict() if hasattr(value, "json_dict") else value
            payload["updatedAt"] = utc_now()
            updated = CaptureJob.model_validate(payload)
            self._capture_jobs[job_id] = updated
            self._atomic_write(self._path(self.capture_job_root, job_id), updated.json_dict())
            return updated

    def cancel_capture_job(self, job_id: str) -> CaptureJob | None:
        if not self.get_capture_job(job_id):
            return None
        return self.update_capture_job(job_id, cancelled=True, status="cancelled", message="Cancelled")

    def create_job(self, capture_id: str, page_ids: list[str]) -> ReconstructionJob:
        now = utc_now()
        job = ReconstructionJob(
            id=new_id(),
            captureId=capture_id,
            pageIds=page_ids,
            status="queued",
            progress=0,
            totalPages=len(page_ids),
            message="Queued",
            createdAt=now,
            updatedAt=now,
            cancelled=False,
        )
        with self._lock:
            self._jobs[job.id] = job
            self._persist_job(job)
        return job

    def get_job(self, job_id: str) -> ReconstructionJob | None:
        self.sweep()
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                job = self._read_persisted_job(job_id)
                if job:
                    self._jobs[job_id] = job
            return job

    def update_job(self, job_id: str, **patch) -> ReconstructionJob:
        with self._lock:
            job = self.get_job(job_id)
            if not job:
                raise KeyError("Job not found")
            payload = job.json_dict(exclude_none=False)
            aliases = {
                "capture_id": "captureId", "page_ids": "pageIds", "current_page": "currentPage",
                "total_pages": "totalPages", "created_at": "createdAt", "updated_at": "updatedAt",
            }
            for key, value in patch.items():
                payload[aliases.get(key, key)] = value.json_dict() if hasattr(value, "json_dict") else value
            payload["updatedAt"] = utc_now()
            updated = ReconstructionJob.model_validate(payload)
            self._jobs[job_id] = updated
            self._persist_job(updated)
            return updated

    def cancel_job(self, job_id: str) -> ReconstructionJob | None:
        if not self.get_job(job_id):
            return None
        return self.update_job(job_id, cancelled=True, status="cancelled", message="Cancelled")

    def get_job_asset(self, job_id: str, asset_id: str) -> tuple[bytes, str] | None:
        job = self._jobs.get(job_id)
        if job and job.result and asset_id in job.result.assets:
            asset = job.result.assets[asset_id]
            if asset.data_base64:
                return base64.b64decode(asset.data_base64, validate=True), asset.mime_type
        raw = self._read(self._path(self.job_root, job_id))
        asset = ((raw or {}).get("result") or {}).get("assets", {}).get(asset_id)
        if not asset:
            return None
        filename = asset.get("_dataFile")
        if not filename:
            return None
        try:
            return (self.job_asset_root / job_id / filename).read_bytes(), asset.get("mimeType", "application/octet-stream")
        except OSError:
            return None

    def put_figma_qa_report(self, job_id: str, report: dict[str, Any]) -> dict[str, Any]:
        if not self.get_job(job_id):
            raise KeyError("Reconstruction job not found")
        with self._lock:
            self._atomic_write(self._path(self.figma_qa_root, job_id), report)
        return report

    def get_figma_qa_report(self, job_id: str) -> dict[str, Any] | None:
        self.sweep()
        if not self.get_job(job_id):
            return None
        return self._read(self._path(self.figma_qa_root, job_id))
