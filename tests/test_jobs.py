from __future__ import annotations

import base64
import io
from pathlib import Path
import tempfile
import threading
import unittest

from PIL import Image

from canva_converter.errors import ServiceError
from canva_converter.jobs import ReconstructionJobRunner
from canva_converter.models import CapturedPage, OcrResult, ReconstructedPage
from canva_converter.store import ArtifactStore


class BlockingOcr:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def detect(self, _image, _width, _height):
        self.started.set()
        if not self.release.wait(timeout=2):
            raise RuntimeError("Test OCR release timed out")
        return OcrResult(blocks=[], fullText="")


class EmptyLayout:
    def analyze(self, *_args):
        return ReconstructedPage(backgroundColor="#ffffff", elements=[])


class JobCapacityTests(unittest.TestCase):
    def test_reconstruction_queue_is_bounded_by_worker_capacity(self):
        image = Image.new("RGB", (20, 20), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        page = CapturedPage(
            id="dddddddd-dddd-4ddd-8ddd-dddddddddddd", index=0, width=20, height=20,
            screenshotBase64=base64.b64encode(buffer.getvalue()).decode("ascii"),
        )
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(Path(directory))
            capture = store.put_capture("https://www.canva.com/design/ABC/token/view", "Capacity", [page])
            first = store.create_job(capture.id, [page.id])
            second = store.create_job(capture.id, [page.id])
            ocr = BlockingOcr()
            runner = ReconstructionJobRunner(store, ocr, EmptyLayout(), max_workers=1)
            try:
                runner.submit(first)
                self.assertTrue(ocr.started.wait(timeout=1))
                with self.assertRaisesRegex(ServiceError, "capacity is busy") as raised:
                    runner.submit(second)
                self.assertEqual(raised.exception.status, 429)
            finally:
                ocr.release.set()
                runner.executor.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    unittest.main()
