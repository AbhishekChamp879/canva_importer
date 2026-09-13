"""Single-worker PDF jobs with disk artifacts, cancellation and bounded subprocesses."""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, RLock, Thread

import psutil
from PIL import Image

from .acquisition.url_policy import resolve_canva_source_url, _parse_design_path
from urllib.parse import urlparse
from .editable_models import EditableScene
from .errors import ServiceError
from .font_ai import FontAI
from .models import new_id, validate_uuid
from .store import ArtifactStore


TERMINAL = {"completed", "failed", "cancelled"}


class EditableJobs:
    def __init__(self, settings, store, client):
        self.settings, self.store, self.client = settings, store, client
        self.root = settings.store_root / "editable-jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.active = {}
        self.ai_active = set()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="editable-pdf")
        self.ai = FontAI(settings)
        self.stop = Event()
        self.sweeper = None
        for path in self.root.glob("*/job.json"):
            record = ArtifactStore._read(path)
            if record and record.get("status") not in TERMINAL:
                record.update(status="failed", message="Conversion interrupted by backend restart.", error={"code": "EDITABLE_INTERRUPTED", "message": "Reload or retry conversion."})
                ArtifactStore._atomic_write(path, record)
        self.sweep()

    def start(self):
        def run():
            while not self.stop.wait(30):
                self.sweep()
        self.sweeper = Thread(target=run, daemon=True, name="editable-cleanup")
        self.sweeper.start()

    def directory(self, job_id):
        try:
            return self.root / validate_uuid(job_id)
        except (TypeError, ValueError):
            raise ServiceError("EDITABLE_NOT_FOUND", "Conversion expired or does not exist.", 404) from None

    def get(self, job_id):
        with self.lock:
            record = ArtifactStore._read(self.directory(job_id) / "job.json")
            if not record or (record["expiresAt"] <= time.time() and job_id not in self.active and job_id not in self.ai_active):
                raise ServiceError("EDITABLE_NOT_FOUND", "Conversion expired or does not exist. Convert the page again.", 404)
            return record

    def patch(self, job_id, **patch):
        with self.lock:
            record = self.get(job_id)
            if record["status"] == "cancelled" and patch.get("status") != "cancelled":
                return record
            record.update(patch)
            record["updatedAt"] = time.time()
            ArtifactStore._atomic_write(self.directory(job_id) / "job.json", record)
            return record

    def submit(self, capture_id, page_id):
        try:
            capture_id, page_id = validate_uuid(capture_id), validate_uuid(page_id)
        except (TypeError, ValueError):
            raise ServiceError("INVALID_REQUEST", "Capture and page IDs must be valid UUIDs.", 400) from None
        capture = self.store.get_capture(capture_id)
        page = next((p for p in capture.pages if p.id == page_id), None) if capture else None
        if not page:
            raise ServiceError("CAPTURE_NOT_FOUND", "The selected page expired or does not belong to this capture. Reload pages.", 404)
        self.client.auth.access_token()
        with self.lock:
            if self.stop.is_set() or self.active:
                raise ServiceError("EDITABLE_BUSY", "An editable conversion is already running. Retry shortly.", 429)
            job_id, signal = new_id(), Event()
            directory = self.directory(job_id)
            directory.mkdir()
            now = time.time()
            record = {"jobId": job_id, "captureId": capture_id, "pageId": page_id, "pageIndex": page.index,
                      "title": capture.title, "status": "queued", "progress": 0, "message": "Queued",
                      "createdAt": now, "updatedAt": now, "expiresAt": now + self.settings.artifact_ttl_seconds,
                      "fontAIConfigured": self.ai.configured, "aiRequests": 0}
            ArtifactStore._atomic_write(directory / "job.json", record)
            self.active[job_id] = signal
            try:
                self.executor.submit(self._run, job_id, signal, capture.source_url, page.width, page.height)
            except Exception:
                self.active.pop(job_id, None)
                self.patch(job_id, status="failed", message="Worker unavailable")
                raise ServiceError("EDITABLE_BUSY", "Conversion worker unavailable.", 503) from None
            return record

    def _run(self, job_id, signal, source_url, width, height):
        process = None
        directory = self.directory(job_id)
        deadline = time.monotonic() + 300
        def stopped():
            return signal.is_set() or self.stop.is_set() or time.monotonic() >= deadline
        def check():
            if stopped():
                raise ServiceError("EDITABLE_CANCELLED" if signal.is_set() or self.stop.is_set() else "EDITABLE_TIMEOUT", "Conversion cancelled or exceeded its deadline.", 408)
        try:
            check()
            job = self.get(job_id)
            self.patch(job_id, status="exporting", progress=5, message="Checking Canva export access")
            design = _parse_design_path(urlparse(resolve_canva_source_url(source_url)).path)
            if not design:
                raise ServiceError("INVALID_SOURCE", "Invalid Canva design reference.", 400)
            metadata = self.client.get_design(design.design_id)
            if set(metadata.get("design_types") or []).intersection({"doc", "whiteboard", "sheet", "video"}):
                raise ServiceError("UNSUPPORTED_SOURCE", "Editable import supports fixed-size Canva designs only.", 422)
            count = metadata.get("page_count")
            if count is not None and (type(count) is not int or job["pageIndex"] >= count or count > 500):
                raise ServiceError("SOURCE_CHANGED", "The selected page is no longer available. Reload Canva pages.", 409)
            urls = self.client.export_pdf(design.design_id, job["pageIndex"], is_cancelled=stopped,
                progress=lambda _: self.patch(job_id, progress=20, message="Canva is exporting the selected page as PDF"))
            check()
            if len(urls) != 1:
                raise ServiceError("INVALID_PDF", "Canva did not return one selected-page PDF.", 422)
            data = self.client.download_export(urls[0], kind="pdf", is_cancelled=stopped)
            check()
            (directory / "source.pdf").write_bytes(data)
            del data
            self.patch(job_id, status="extracting", progress=45, message="Extracting PDF objects and font metadata")
            process = subprocess.Popen([sys.executable, "-m", "canva_converter.pdf_worker", str(directory / "source.pdf"), str(directory), str(width), str(height)],
                cwd=Path(__file__).resolve().parents[1], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            child, extraction_deadline = psutil.Process(process.pid), time.monotonic() + 60
            while process.poll() is None:
                check()
                if time.monotonic() >= extraction_deadline:
                    raise ServiceError("PDF_TIMEOUT", "PDF is too complex to convert within 60 seconds. Use image import.", 422)
                try:
                    if child.memory_info().rss > 1024 ** 3:
                        raise ServiceError("PDF_MEMORY_LIMIT", "PDF exceeded the 1 GiB worker memory limit. Use image import.", 413)
                except psutil.NoSuchProcess:
                    pass
                signal.wait(0.1)
            check()
            if process.returncode != 0:
                raise ServiceError("PDF_CONVERSION_FAILED", "PDF is encrypted, malformed, changed-size or too complex. Reload pages or use image import.", 422)
            scene_path = directory / "scene.json"
            if scene_path.stat().st_size > self.settings.max_api_response_bytes:
                raise ServiceError("SCENE_TOO_LARGE", "Extracted page exceeds the scene size limit.", 413)
            scene = EditableScene.model_validate_json(scene_path.read_bytes())
            self.patch(job_id, status="completed", progress=100, message="Review the fresh PDF and Figma result before importing",
                       wholePageFallback=scene.wholePageFallback, warnings=scene.warnings,
                       expiresAt=time.time() + self.settings.artifact_ttl_seconds)
        except Exception as error:
            cancelled = signal.is_set() or self.stop.is_set()
            code = "EDITABLE_CANCELLED" if cancelled else error.code if isinstance(error, ServiceError) else "EDITABLE_FAILED"
            message = "Conversion cancelled." if cancelled else error.message if isinstance(error, ServiceError) else "Conversion failed. Retry or use image import."
            self.patch(job_id, status="cancelled" if cancelled else "failed", message=message, error={"code": code, "message": message})
        finally:
            if process and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            (directory / "source.pdf").unlink(missing_ok=True)
            with self.lock:
                record = ArtifactStore._read(directory / "job.json")
                if not record or record["status"] != "completed":
                    for file in directory.iterdir():
                        if file.is_file() and file.name != "job.json":
                            file.unlink(missing_ok=True)
                self.active.pop(job_id, None)

    def cancel(self, job_id):
        with self.lock:
            job = self.get(job_id)
            if job_id in self.active:
                self.active[job_id].set()
            if job["status"] not in TERMINAL:
                return self.patch(job_id, status="cancelled", message="Conversion cancelled")
            return job

    def scene(self, job_id):
        with self.lock:
            if self.get(job_id)["status"] != "completed":
                raise ServiceError("EDITABLE_NOT_READY", "The conversion has not completed.", 409)
            return EditableScene.model_validate_json((self.directory(job_id) / "scene.json").read_bytes())

    def asset(self, job_id, asset_id):
        with self.lock:
            scene = self.scene(job_id)
            if not any(asset.id == asset_id for asset in scene.assets):
                raise ServiceError("ASSET_NOT_FOUND", "The asset does not belong to this conversion.", 404)
            return (self.directory(job_id) / f"{asset_id}.png").read_bytes()

    def suggest(self, job_id, font_id):
        with self.lock:
            job = self.get(job_id)
            scene = self.scene(job_id)
            font = next((font for font in scene.fonts if font.id == font_id), None)
            if not font:
                raise ServiceError("INVALID_FONT", "Font does not belong to this conversion.", 400)
            if not self.ai.configured:
                raise ServiceError("FONT_AI_NOT_CONFIGURED", "Configure OPENAI_API_KEY in the backend to enable suggestions.", 503)
            cache_path = self.directory(job_id) / "font-suggestions.json"
            cache = ArtifactStore._read(cache_path) or {}
            if font_id in cache:
                return cache[font_id]
            if self.ai_active:
                raise ServiceError("FONT_AI_BUSY", "Another suggestion request is active.", 429)
            if job["aiRequests"] >= 10:
                raise ServiceError("FONT_AI_LIMIT", "Ten font suggestion requests have already been used for this conversion.", 429)
            crop = self.asset(job_id, font.cropAsset)
            with Image.open(io.BytesIO(crop)) as image:
                image.thumbnail((1536, 1536))
                output = io.BytesIO()
                image.save(output, format="PNG")
                crop = output.getvalue()
            self.patch(job_id, aiRequests=job["aiRequests"] + 1)
            self.ai_active.add(job_id)
        try:
            result = self.ai.suggest(font.json_dict(), crop)
            with self.lock:
                cache[font_id] = result
                ArtifactStore._atomic_write(cache_path, cache)
            return result
        finally:
            with self.lock:
                self.ai_active.discard(job_id)

    def sweep(self):
        with self.lock:
            for directory in self.root.iterdir():
                if not directory.is_dir() or directory.is_symlink() or directory.name in self.active or directory.name in self.ai_active:
                    continue
                record = ArtifactStore._read(directory / "job.json")
                if not record or record.get("expiresAt", 0) <= time.time():
                    # Only direct children of this dedicated artifact root are removed.
                    if directory.resolve().parent == self.root.resolve():
                        shutil.rmtree(directory)

    def shutdown(self):
        self.stop.set()
        with self.lock:
            for signal in self.active.values():
                signal.set()
        self.executor.shutdown(wait=True, cancel_futures=False)
        if self.sweeper:
            self.sweeper.join(timeout=2)
