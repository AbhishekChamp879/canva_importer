from __future__ import annotations

import argparse
from hashlib import sha256
import io
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from PIL import Image

from canva_converter.config import Settings


TERMINAL_STATES = {"completed", "failed", "cancelled"}


def expected_page_image_size(logical_width: int, logical_height: int) -> tuple[int, int]:
    if logical_width <= 0 or logical_height <= 0:
        raise ValueError("Logical page dimensions must be positive.")
    scale = min(2.0, 8192 / max(logical_width, logical_height))
    return max(1, round(logical_width * scale)), max(1, round(logical_height * scale))


def request_json(base_url: str, path: str, *, method: str = "GET", payload: dict | None = None, timeout: int = 60) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        urljoin(base_url, path),
        data=body,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
            if not isinstance(value, dict):
                raise RuntimeError("Converter returned a non-object JSON response.")
            return value
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"Converter returned HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"Converter connection failed: {error.reason}") from error


def cancel_job(base_url: str, job_id: str) -> None:
    try:
        request_json(base_url, f"/api/capture-jobs/{job_id}", method="DELETE", timeout=10)
    except Exception:
        pass


def validate_page_image(base_url: str, page: dict) -> str:
    image_path = page.get("imageUrl")
    if not isinstance(image_path, str) or not image_path.startswith("/api/captures/"):
        raise RuntimeError(f"Page {page.get('index')} returned an untrusted image URL.")
    request = Request(urljoin(base_url, image_path), headers={"Accept": "image/png"})
    with urlopen(request, timeout=60) as response:
        content_type = response.headers.get_content_type()
        image_bytes = response.read(25 * 1024 * 1024 + 1)
    if content_type != "image/png":
        raise RuntimeError(f"Page {page.get('index')} returned {content_type}, not image/png.")
    if not image_bytes or len(image_bytes) > 25 * 1024 * 1024:
        raise RuntimeError(f"Page {page.get('index')} returned an empty or oversized image.")
    with Image.open(io.BytesIO(image_bytes)) as image:
        image.load()
        expected = expected_page_image_size(int(page["width"]), int(page["height"]))
        if image.format != "PNG" or image.size != expected:
            raise RuntimeError(f"Page {page.get('index')} PNG dimensions {image.size} do not match expected {expected}.")
    return sha256(image_bytes).hexdigest()


def validate_capture(base_url: str, source_url: str, deadline_seconds: int) -> dict:
    created = request_json(base_url, "/api/capture-jobs", method="POST", payload={"url": source_url})
    job_id = created.get("jobId")
    if not isinstance(job_id, str):
        raise RuntimeError("Converter did not return a capture job ID.")
    deadline = time.monotonic() + deadline_seconds
    last_progress = None
    try:
        while time.monotonic() < deadline:
            job = request_json(base_url, f"/api/capture-jobs/{job_id}")
            progress = (job.get("status"), job.get("progress"), job.get("currentPage"), job.get("totalPages"))
            if progress != last_progress:
                print(json.dumps({"event": "progress", "status": progress[0], "progress": progress[1], "currentPage": progress[2], "totalPages": progress[3]}), flush=True)
                last_progress = progress
            if job.get("status") in TERMINAL_STATES:
                break
            time.sleep(2)
        else:
            raise TimeoutError(f"Live capture exceeded {deadline_seconds} seconds.")
        if job.get("status") != "completed":
            error = job.get("error") or {}
            raise RuntimeError(f"Live capture failed with {error.get('code', job.get('status'))}: {error.get('message', job.get('message'))}")
        capture_id = job.get("captureId")
        capture = request_json(base_url, f"/api/captures/{capture_id}")
        pages = capture.get("pages")
        if not isinstance(pages, list) or not pages:
            raise RuntimeError("Completed capture returned no pages.")
        expected_indices = list(range(len(pages)))
        indices = [page.get("index") for page in pages]
        if indices != expected_indices:
            raise RuntimeError(f"Captured page indices are not ordered and contiguous: {indices}")
        page_ids = [page.get("id") for page in pages]
        if any(not isinstance(page_id, str) for page_id in page_ids) or len(set(page_ids)) != len(page_ids):
            raise RuntimeError("Captured page IDs are missing or duplicated.")
        orientations = set()
        hashes = []
        for position, page in enumerate(pages, 1):
            width, height = int(page["width"]), int(page["height"])
            expected_orientation = "square" if abs(width - height) / max(width, height) <= 0.01 else "landscape" if width > height else "portrait"
            if page.get("orientation") != expected_orientation:
                raise RuntimeError(f"Page {position} orientation metadata is inconsistent.")
            orientations.add(expected_orientation)
            hashes.append(validate_page_image(base_url, page))
            print(json.dumps({"event": "verified-page", "page": position, "totalPages": len(pages)}), flush=True)
        return {
            "status": "passed",
            "pageCount": len(pages),
            "uniqueImageCount": len(set(hashes)),
            "orientations": sorted(orientations),
        }
    except BaseException:
        cancel_job(base_url, job_id)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a fresh Canva capture through the local Flask API.")
    parser.add_argument("urls", nargs="+", help="Public Canva design/share URLs to validate.")
    parser.add_argument("--base-url", help="Loopback converter base URL. Defaults to the configured HOST and PORT.")
    parser.add_argument("--deadline-seconds", type=int, default=1800)
    arguments = parser.parse_args()
    configured = Settings.load()
    configured_host = f"[{configured.host}]" if configured.host == "::1" else configured.host
    base_url = arguments.base_url or f"http://{configured_host}:{configured.port}"
    parsed_base = urlsplit(base_url)
    if parsed_base.scheme != "http" or parsed_base.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed_base.username or parsed_base.password:
        raise SystemExit("--base-url must be an unauthenticated loopback HTTP URL.")
    if arguments.deadline_seconds < 30 or arguments.deadline_seconds > 3600:
        raise SystemExit("--deadline-seconds must be between 30 and 3600.")
    summaries = []
    for position, source_url in enumerate(arguments.urls, 1):
        result = validate_capture(base_url.rstrip("/") + "/", source_url, arguments.deadline_seconds)
        summaries.append({"input": position, **result})
    print(json.dumps({"liveValidation": "passed", "captures": summaries}, indent=2))


if __name__ == "__main__":
    main()
