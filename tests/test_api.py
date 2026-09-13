from __future__ import annotations

import base64
import io
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

from canva_converter import create_app
from canva_converter.capture_jobs import CaptureJobRunner
from canva_converter.errors import AcquisitionError
from canva_converter.models import CapturedPage


class ApiFakeCapture:
    def __init__(self, pages):
        self.pages = pages

    def capture(self, *_args):
        return "Captured fixture", self.pages


class ApiBlockingCapture:
    def __init__(self):
        self.started = threading.Event()

    def capture(self, _url, progress, is_cancelled):
        self.started.set()
        progress("[loading] Waiting in cancellable capture.")
        for _ in range(200):
            if is_cancelled():
                raise AcquisitionError("CAPTURE_CANCELLED", "Capture cancelled.")
            time.sleep(0.005)
        raise AssertionError("Capture cancellation was not observed")


class ApiCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict("os.environ", {
            "ARTIFACT_STORE": self.temp.name,
            "CANVA_CLIENT_ID": "",
            "CANVA_CLIENT_SECRET": "",
        })
        self.env.start()
        self.app = create_app({"TESTING": True})
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions["canva_services"].shutdown()
        self.env.stop()
        self.temp.cleanup()

    def test_health_contract(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["service"], "canva-converter")
        self.assertEqual(payload["runtime"], "python-flask")
        self.assertTrue(payload["browser"]["portable"])
        self.assertTrue(payload["browser"]["autoInstall"])
        self.assertFalse(payload["canvaOAuth"]["configured"])
        self.assertFalse(payload["canvaOAuth"]["connected"])
        self.assertEqual(payload["limits"]["captureConcurrency"], 2)

    def test_oauth_start_returns_specific_setup_error_when_unconfigured(self):
        response = self.client.post("/api/canva/oauth/start", json={})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["error"]["code"], "CANVA_OAUTH_NOT_CONFIGURED")

    def test_backend_exposes_only_capture_capabilities(self):
        payload = self.client.get("/api/health").get_json()
        self.assertNotIn("aiConfigured", payload)
        self.assertNotIn("providers", payload)
        self.assertNotIn("reconstructionConcurrency", payload["limits"])
        services = self.app.extensions["canva_services"]
        self.assertFalse(hasattr(services, "jobs"))
        self.assertFalse(hasattr(services, "ocr"))
        self.assertFalse(hasattr(services, "layout"))
        self.assertEqual({path.name for path in Path(self.temp.name).iterdir()}, {"captures", "capture-jobs", "editable-jobs"})

    def test_removed_conversion_endpoints_are_not_available(self):
        job_id = "11111111-1111-4111-8111-111111111111"
        requests = [
            ("POST", "/api/reconstruction-jobs"),
            ("GET", f"/api/reconstruction-jobs/{job_id}"),
            ("DELETE", f"/api/reconstruction-jobs/{job_id}"),
            ("GET", f"/api/reconstruction-jobs/{job_id}/assets/asset"),
            ("GET", f"/api/reconstruction-jobs/{job_id}/figma-qa"),
            ("POST", f"/api/reconstruction-jobs/{job_id}/figma-qa"),
        ]
        for method, path in requests:
            with self.subTest(method=method, path=path):
                response = self.client.open(path, method=method, json={})
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.get_json()["error"]["code"], "NOT_FOUND")

    def test_invalid_capture_request_is_structured(self):
        response = self.client.post("/api/captures", json={"url": "not-a-url"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "INVALID_REQUEST")

    def test_non_canva_capture_url_is_rejected_before_job_creation(self):
        response = self.client.post("/api/capture-jobs", json={"url": "https://example.com/design/ABC/view"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "INVALID_SOURCE")

    def test_non_json_request_is_structured(self):
        response = self.client.post("/api/captures", data="url=nope", content_type="text/plain")
        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.get_json()["error"]["code"], "INVALID_REQUEST")

    def test_pdf_route_is_not_exposed(self):
        response = self.client.post("/api/pdf", data=b"pdf")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"]["code"], "NOT_FOUND")

    def test_cors_rejects_lookalike_localhost_and_accepts_figma(self):
        rejected = self.client.get("/api/health", headers={"Origin": "http://localhost.attacker.test"})
        accepted = self.client.get("/api/health", headers={"Origin": "https://www.figma.com"})
        self.assertNotIn("Access-Control-Allow-Origin", rejected.headers)
        self.assertEqual(accepted.headers["Access-Control-Allow-Origin"], "https://www.figma.com")

    def test_capture_returns_small_preview_and_separate_full_image(self):
        image = Image.new("RGB", (640, 480), "purple")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        image_bytes = buffer.getvalue()
        page = CapturedPage(
            id="11111111-1111-4111-8111-111111111111",
            index=0,
            width=320,
            height=240,
            screenshotBase64=base64.b64encode(image_bytes).decode("ascii"),
        )
        self.app.extensions["canva_services"].capture = ApiFakeCapture([page])

        response = self.client.post("/api/captures", json={"url": "https://www.canva.com/design/ABC/token/view"})

        self.assertEqual(response.status_code, 201)
        captured = response.get_json()
        preview = captured["pages"][0]
        self.assertEqual(preview["orientation"], "landscape")
        self.assertTrue(preview["thumbnail"].startswith("data:image/jpeg;base64,"))
        self.assertNotIn(page.screenshot_base64, response.get_data(as_text=True))
        with Image.open(io.BytesIO(base64.b64decode(preview["thumbnail"].split(",", 1)[1]))) as thumbnail:
            self.assertLessEqual(max(thumbnail.size), 320)

        full_image = self.client.get(preview["imageUrl"])
        self.assertEqual(full_image.status_code, 200)
        self.assertEqual(full_image.mimetype, "image/png")
        self.assertEqual(full_image.data, image_bytes)

    def test_capture_preserves_mixed_page_orientations(self):
        cases = [
            ("11111111-1111-4111-8111-111111111111", 1600, 900, "landscape"),
            ("22222222-2222-4222-8222-222222222222", 900, 1600, "portrait"),
            ("33333333-3333-4333-8333-333333333333", 1080, 1080, "square"),
        ]
        pages = []
        for index, (page_id, width, height, _orientation) in enumerate(cases):
            image = Image.new("RGB", (max(1, width // 4), max(1, height // 4)), (index * 60, 20, 100))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            pages.append(CapturedPage(
                id=page_id, index=index, width=width, height=height,
                screenshotBase64=base64.b64encode(buffer.getvalue()).decode("ascii"),
            ))
        self.app.extensions["canva_services"].capture = ApiFakeCapture(pages)

        response = self.client.post("/api/captures", json={"url": "https://www.canva.com/design/ABC/token/view"})

        self.assertEqual(response.status_code, 201)
        result = response.get_json()["pages"]
        self.assertEqual([(page["width"], page["height"], page["orientation"]) for page in result], [
            (width, height, orientation) for _page_id, width, height, orientation in cases
        ])

    def test_async_capture_job_completes_and_exposes_capture(self):
        image = Image.new("RGB", (80, 40), "blue")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        page = CapturedPage(
            id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", index=0, width=80, height=40,
            screenshotBase64=base64.b64encode(buffer.getvalue()).decode("ascii"),
        )
        services = self.app.extensions["canva_services"]
        runner = CaptureJobRunner(services.store, ApiFakeCapture([page]), max_workers=1)
        services.capture_jobs = runner
        try:
            created = self.client.post("/api/capture-jobs", json={"url": "https://www.canva.com/design/ABC/token/view"})
            self.assertEqual(created.status_code, 202)
            job_id = created.get_json()["jobId"]
            payload = None
            for _ in range(100):
                payload = self.client.get(f"/api/capture-jobs/{job_id}").get_json()
                if payload["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.01)
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["progress"], 100)
            capture_response = self.client.get(f"/api/captures/{payload['captureId']}")
            self.assertEqual(capture_response.status_code, 200)
            self.assertEqual(len(capture_response.get_json()["pages"]), 1)
        finally:
            runner.executor.shutdown(wait=True, cancel_futures=True)

    def test_async_capture_job_can_be_cancelled(self):
        services = self.app.extensions["canva_services"]
        capture = ApiBlockingCapture()
        runner = CaptureJobRunner(services.store, capture, max_workers=1)
        services.capture_jobs = runner
        try:
            created = self.client.post("/api/capture-jobs", json={"url": "https://www.canva.com/design/ABC/token/view"})
            job_id = created.get_json()["jobId"]
            self.assertTrue(capture.started.wait(timeout=1))
            busy = self.client.post("/api/capture-jobs", json={"url": "https://www.canva.com/design/OTHER/token/view"})
            self.assertEqual(busy.status_code, 429)
            self.assertEqual(busy.get_json()["error"]["code"], "CAPTURE_BUSY")
            cancelled = self.client.delete(f"/api/capture-jobs/{job_id}")
            self.assertEqual(cancelled.status_code, 202)
            for _ in range(100):
                payload = self.client.get(f"/api/capture-jobs/{job_id}").get_json()
                if payload["status"] == "cancelled":
                    break
                time.sleep(0.01)
            self.assertEqual(payload["status"], "cancelled")
        finally:
            runner.executor.shutdown(wait=True, cancel_futures=True)

if __name__ == "__main__":
    unittest.main()
