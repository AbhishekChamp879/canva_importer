from __future__ import annotations

import logging
import base64
import hashlib
import html
import io

from flask import Blueprint, current_app, jsonify, request, send_file, url_for
from PIL import Image
from pydantic import ValidationError

from .config import Settings
from .errors import ServiceError, public_error_message
from .models import CaptureRequest, FigmaQaSubmission, ReconstructionRequest, utc_now, validate_uuid
from .services import ServiceContainer
from .acquisition.url_policy import is_valid_canva_url
from .acquisition.oauth import oauth_design_url


logger = logging.getLogger(__name__)
api = Blueprint("api", __name__)


def services() -> ServiceContainer:
    return current_app.extensions["canva_services"]


def settings() -> Settings:
    return current_app.extensions["canva_settings"]


def capture_payload(record):
    return {
        "captureId": record.id,
        "title": record.title,
        "pages": [{
            "id": page.id,
            "index": page.index,
            "width": page.width,
            "height": page.height,
            "orientation": page.orientation,
            "thumbnail": page_thumbnail(page.screenshot_base64),
            "imageUrl": f"/api/captures/{record.id}/pages/{page.id}/image",
        } for page in record.pages],
    }


def bounded_json(payload, status: int = 200):
    response = jsonify(payload)
    response.status_code = status
    if len(response.get_data()) > settings().max_api_response_bytes:
        return error_response(
            "API_RESPONSE_LIMIT_EXCEEDED",
            f"The response exceeds the configured {settings().max_api_response_bytes // (1024 * 1024)}MB API limit. Import fewer editable pages at once.",
            413,
        )
    return response


@api.route("/api/health", methods=["GET", "OPTIONS"])
def health():
    if request.method == "OPTIONS":
        return "", 204
    config = settings()
    return jsonify({
        "status": "ok",
        "service": "canva-converter",
        "runtime": "python-flask",
        "schemaVersion": 1,
        "aiConfigured": bool(config.openai_api_key),
        "providers": {
            "ocr": "openai-vision",
            "layout": "openai",
            "ocrConfigured": bool(config.openai_api_key),
            "layoutConfigured": bool(config.openai_api_key),
        },
        "browser": {
            "portable": True,
            "autoInstall": config.browser_auto_install,
            "overrideConfigured": bool(config.browser_executable_path),
        },
        "canvaOAuth": services().canva_oauth.status(),
        "limits": {
            "captureConcurrency": config.capture_concurrency,
            "reconstructionConcurrency": config.job_concurrency,
            "captureTimeoutMs": config.capture_timeout_ms,
            "maxCaptureBytes": config.max_capture_bytes,
            "maxApiResponseBytes": config.max_api_response_bytes,
            "artifactTtlSeconds": config.artifact_ttl_seconds,
        },
    })


@api.route("/api/canva/oauth/status", methods=["GET", "OPTIONS"])
def canva_oauth_status():
    if request.method == "OPTIONS":
        return "", 204
    return jsonify(services().canva_oauth.status())


@api.route("/api/canva/oauth/start", methods=["POST", "OPTIONS"])
def canva_oauth_start():
    if request.method == "OPTIONS":
        return "", 204
    return jsonify({"authorizationUrl": services().canva_oauth.begin()})


@api.route("/api/canva/oauth/callback", methods=["GET"])
def canva_oauth_callback():
    oauth_error = (request.args.get("error") or "").strip()
    if oauth_error:
        description = (request.args.get("error_description") or "Canva authorization was cancelled.").strip()
        message = public_error_message(description)
        return _oauth_result_page("Canva connection failed", message, False), 400
    try:
        services().canva_oauth.complete(
            state=(request.args.get("state") or "").strip(),
            code=(request.args.get("code") or "").strip(),
        )
    except ServiceError as error:
        return _oauth_result_page("Canva connection failed", public_error_message(error.message), False), error.status
    return _oauth_result_page("Canva connected", "Return to Figma and choose a design.", True)


@api.route("/api/canva/oauth/disconnect", methods=["DELETE", "OPTIONS"])
def canva_oauth_disconnect():
    if request.method == "OPTIONS":
        return "", 204
    services().canva_oauth.disconnect()
    return "", 204


@api.route("/api/canva/designs", methods=["GET", "OPTIONS"])
def canva_designs():
    if request.method == "OPTIONS":
        return "", 204
    query = (request.args.get("query") or "").strip()
    continuation = (request.args.get("continuation") or "").strip()
    if len(query) > 100 or len(continuation) > 4096:
        return error_response("INVALID_REQUEST", "Canva design search parameters are too long.", 400)
    return bounded_json(services().canva_api.list_designs(query=query, continuation=continuation))


@api.route("/api/canva/oauth/capture-jobs", methods=["POST", "OPTIONS"])
def create_canva_oauth_capture_job():
    if request.method == "OPTIONS":
        return "", 204
    payload = request.get_json(silent=False) or {}
    design_id = payload.get("designId")
    if not isinstance(design_id, str):
        return error_response("INVALID_CANVA_DESIGN", "Select a Canva design first.", 400)
    source_url = oauth_design_url(design_id)
    # Resolve metadata before queueing so expired OAuth and missing permissions
    # fail immediately instead of becoming an opaque background error.
    services().canva_api.get_design(design_id)
    job = services().store.create_capture_job(source_url, source="canva-oauth")
    try:
        services().capture_jobs.submit(job)
    except ServiceError as error:
        services().store.update_capture_job(
            job.id, status="failed", message=error.message,
            error={"code": error.code, "message": error.message},
        )
        raise
    return jsonify({"jobId": job.id}), 202


def _oauth_result_page(title: str, message: str, success: bool) -> str:
    color = "#12a66a" if success else "#d92d20"
    return f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\"><title>{html.escape(title)}</title></head><body style=\"font:16px system-ui,sans-serif;margin:48px;max-width:640px\"><h1 style=\"color:{color}\">{html.escape(title)}</h1><p>{html.escape(message)}</p><p>You can close this tab.</p></body></html>"""


@api.route("/api/captures", methods=["POST", "OPTIONS"])
def create_capture():
    if request.method == "OPTIONS":
        return "", 204
    payload = CaptureRequest.model_validate(request.get_json(silent=False) or {})
    if not is_valid_canva_url(str(payload.url)):
        return error_response("INVALID_SOURCE", "Use a Canva design/share URL or canva.link URL.", 400)
    title, pages = services().capture.capture(str(payload.url), lambda message: logger.info("[capture] %s", message))
    record = services().store.put_capture(str(payload.url), title, pages)
    return bounded_json(capture_payload(record), 201)


@api.route("/api/capture-jobs", methods=["POST", "OPTIONS"])
def create_capture_job():
    if request.method == "OPTIONS":
        return "", 204
    payload = CaptureRequest.model_validate(request.get_json(silent=False) or {})
    if not is_valid_canva_url(str(payload.url)):
        return error_response("INVALID_SOURCE", "Use a Canva design/share URL or canva.link URL.", 400)
    job = services().store.create_capture_job(str(payload.url))
    try:
        services().capture_jobs.submit(job)
    except ServiceError as error:
        services().store.update_capture_job(
            job.id, status="failed", message=error.message,
            error={"code": error.code, "message": error.message},
        )
        raise
    return jsonify({"jobId": job.id}), 202


@api.route("/api/capture-jobs/<job_id>", methods=["GET", "DELETE", "OPTIONS"])
def capture_job(job_id: str):
    if request.method == "OPTIONS":
        return "", 204
    try:
        normalized_id = validate_uuid(job_id)
    except (ValueError, TypeError):
        return error_response("CAPTURE_JOB_NOT_FOUND", "Capture job expired or does not exist.", 404)
    if request.method == "DELETE":
        job = services().capture_jobs.cancel(normalized_id)
        return (jsonify({"status": job.status}), 202) if job else error_response("CAPTURE_JOB_NOT_FOUND", "Capture job not found.", 404)
    job = services().store.get_capture_job(normalized_id)
    if not job:
        return error_response("CAPTURE_JOB_NOT_FOUND", "Capture job expired or does not exist.", 404)
    payload = job.json_dict()
    payload.pop("cancelled", None)
    return jsonify(payload)


@api.route("/api/captures/<capture_id>", methods=["GET", "OPTIONS"])
def captured_design(capture_id: str):
    if request.method == "OPTIONS":
        return "", 204
    try:
        normalized_id = validate_uuid(capture_id)
    except (ValueError, TypeError):
        return error_response("CAPTURE_NOT_FOUND", "Capture expired or does not exist.", 404)
    record = services().store.get_capture(normalized_id)
    if not record:
        return error_response("CAPTURE_NOT_FOUND", "Capture expired or does not exist.", 404)
    return bounded_json(capture_payload(record))


@api.route("/api/captures/<capture_id>/pages/<page_id>/image", methods=["GET", "OPTIONS"])
def captured_page_image(capture_id: str, page_id: str):
    if request.method == "OPTIONS":
        return "", 204
    try:
        normalized_capture_id = validate_uuid(capture_id)
        normalized_page_id = validate_uuid(page_id)
    except (ValueError, TypeError):
        return error_response("CAPTURE_NOT_FOUND", "Capture page expired or does not exist.", 404)
    capture = services().store.get_capture(normalized_capture_id)
    page = next((item for item in capture.pages if item.id == normalized_page_id), None) if capture else None
    if page is None:
        return error_response("CAPTURE_NOT_FOUND", "Capture page expired or does not exist.", 404)
    image_bytes = base64.b64decode(page.screenshot_base64, validate=True)
    response = send_file(io.BytesIO(image_bytes), mimetype="image/png", download_name=f"canva-page-{page.index + 1}.png")
    response.set_etag(hashlib.sha256(image_bytes).hexdigest())
    response.cache_control.private = True
    response.cache_control.max_age = settings().artifact_ttl_seconds
    return response.make_conditional(request)


def page_thumbnail(image_base64: str, maximum_size: int = 320) -> str:
    image_bytes = base64.b64decode(image_base64, validate=True)
    with Image.open(io.BytesIO(image_bytes)) as source:
        source.load()
        thumbnail = source.convert("RGB")
        thumbnail.thumbnail((maximum_size, maximum_size), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        thumbnail.save(buffer, format="JPEG", quality=82, optimize=True)
    return f"data:image/jpeg;base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"


@api.route("/api/reconstruction-jobs", methods=["POST", "OPTIONS"])
def create_reconstruction_job():
    if request.method == "OPTIONS":
        return "", 204
    payload = ReconstructionRequest.model_validate(request.get_json(silent=False) or {})
    capture_id = validate_uuid(payload.capture_id)
    page_ids = [validate_uuid(page_id) for page_id in payload.page_ids]
    capture = services().store.get_capture(capture_id)
    if not capture:
        return error_response("CAPTURE_NOT_FOUND", "Capture expired or does not exist.", 404)
    valid_ids = {page.id for page in capture.pages}
    if any(page_id not in valid_ids for page_id in page_ids):
        return error_response("INVALID_PAGE_SELECTION", "One or more selected pages do not belong to this capture.", 400)
    if len(set(page_ids)) != len(page_ids):
        return error_response("INVALID_PAGE_SELECTION", "Selected page IDs must be unique.", 400)
    config = settings()
    if not config.openai_api_key:
        return error_response(
            "AI_NOT_CONFIGURED",
            "Set OPENAI_API_KEY in the converter environment, then restart the converter.",
            503,
        )
    job = services().store.create_job(capture_id, page_ids)
    try:
        services().jobs.submit(job)
    except ServiceError as error:
        services().store.update_job(
            job.id, status="failed", message=error.message,
            error={"code": error.code, "message": error.message},
        )
        raise
    return jsonify({"jobId": job.id}), 202


@api.route("/api/reconstruction-jobs/<job_id>", methods=["GET", "DELETE", "OPTIONS"])
def reconstruction_job(job_id: str):
    if request.method == "OPTIONS":
        return "", 204
    try:
        normalized_id = validate_uuid(job_id)
    except (ValueError, TypeError):
        return error_response("JOB_NOT_FOUND", "Job expired or does not exist.", 404)
    if request.method == "DELETE":
        job = services().jobs.cancel(normalized_id)
        return (jsonify({"status": job.status}), 202) if job else error_response("JOB_NOT_FOUND", "Job not found.", 404)
    job = services().store.get_job(normalized_id)
    if not job:
        return error_response("JOB_NOT_FOUND", "Job expired or does not exist.", 404)
    payload = job.json_dict()
    payload.pop("cancelled", None)
    if job.status != "completed":
        payload.pop("result", None)
    elif payload.get("result"):
        for asset_id, asset in payload["result"].get("assets", {}).items():
            if asset.pop("dataBase64", None) is not None:
                path = url_for("api.reconstruction_job_asset", job_id=job.id, asset_id=asset_id, _external=False)
                # The Figma development manifest permits this canonical loopback
                # origin. Keeping a full URL also preserves the Design IR HttpUrl
                # contract returned by the completed job.
                asset["url"] = f"http://localhost:{settings().port}{path}"
    return bounded_json(payload)


@api.route("/api/reconstruction-jobs/<job_id>/assets/<asset_id>", methods=["GET", "OPTIONS"])
def reconstruction_job_asset(job_id: str, asset_id: str):
    if request.method == "OPTIONS":
        return "", 204
    try:
        normalized_id = validate_uuid(job_id)
    except (ValueError, TypeError):
        return error_response("JOB_NOT_FOUND", "Job expired or does not exist.", 404)
    if not services().store.get_job(normalized_id):
        return error_response("JOB_NOT_FOUND", "Job expired or does not exist.", 404)
    item = services().store.get_job_asset(normalized_id, asset_id)
    if not item:
        return error_response("ASSET_NOT_FOUND", "Reconstruction asset expired or does not exist.", 404)
    image_bytes, mime_type = item
    if len(image_bytes) > 25 * 1024 * 1024:
        return error_response("CAPTURE_LIMIT_EXCEEDED", "Reconstruction asset exceeds the 25MB image limit.", 413)
    response = send_file(io.BytesIO(image_bytes), mimetype=mime_type, download_name=f"{asset_id}.bin")
    response.set_etag(hashlib.sha256(image_bytes).hexdigest())
    response.cache_control.private = True
    response.cache_control.max_age = settings().artifact_ttl_seconds
    return response.make_conditional(request)


@api.route("/api/reconstruction-jobs/<job_id>/figma-qa", methods=["GET", "POST", "OPTIONS"])
def reconstruction_job_figma_qa(job_id: str):
    if request.method == "OPTIONS":
        return "", 204
    try:
        normalized_id = validate_uuid(job_id)
    except (ValueError, TypeError):
        return error_response("JOB_NOT_FOUND", "Job expired or does not exist.", 404)
    job = services().store.get_job(normalized_id)
    if not job:
        return error_response("JOB_NOT_FOUND", "Job expired or does not exist.", 404)
    if request.method == "GET":
        report = services().store.get_figma_qa_report(normalized_id)
        return jsonify(report) if report else error_response("FIGMA_QA_NOT_FOUND", "No final Figma QA report exists for this job.", 404)
    if job.status != "completed" or not job.result:
        return error_response("FIGMA_QA_NOT_READY", "The reconstruction job must complete before final Figma QA is submitted.", 409)
    capture = services().store.get_capture(job.capture_id)
    if not capture:
        return error_response("CAPTURE_NOT_FOUND", "Capture expired before final Figma QA was submitted.", 404)

    submission = FigmaQaSubmission.model_validate(request.get_json(silent=False) or {})
    submitted_ids = [page.page_id for page in submission.pages]
    if len(set(submitted_ids)) != len(submitted_ids) or set(submitted_ids) != set(job.page_ids):
        return error_response("INVALID_FIGMA_QA", "Figma QA must contain each reconstructed page exactly once.", 400)
    capture_pages = {page.id: page for page in capture.pages}
    result_pages = {page.id: page for page in job.result.pages}
    if any(page_id not in capture_pages or page_id not in result_pages for page_id in submitted_ids):
        return error_response("INVALID_FIGMA_QA", "Figma QA contains a page that is not present in the capture and reconstruction result.", 400)

    thresholds = {
        "exactTextRate": 0.95,
        "nativeCoverage": 0.80,
        "figmaVisualSimilarity": 0.95,
        "missingRegionRate": 0,
        "duplicateTextBlocks": 0,
    }
    page_reports = []
    for submitted in submission.pages:
        captured = capture_pages[submitted.page_id]
        reconstructed = result_pages[submitted.page_id]
        metrics = reconstructed.metrics
        checks = {
            "exportDimensions": submitted.export_width == captured.width and submitted.export_height == captured.height,
            "exactText": metrics.exact_text_rate >= thresholds["exactTextRate"],
            "nativeCoverage": metrics.native_coverage >= thresholds["nativeCoverage"],
            "figmaVisualSimilarity": submitted.visual_similarity >= thresholds["figmaVisualSimilarity"],
            "zeroMissingVisibleRegions": metrics.missing_region_rate <= thresholds["missingRegionRate"],
            "zeroDuplicateVisibleText": metrics.duplicate_text_blocks <= thresholds["duplicateTextBlocks"],
        }
        page_reports.append({
            **submitted.json_dict(),
            "pageIndex": captured.index,
            "logicalWidth": captured.width,
            "logicalHeight": captured.height,
            "backendMetrics": metrics.json_dict(),
            "checks": checks,
            "passed": all(checks.values()),
        })

    total_area = sum(page["logicalWidth"] * page["logicalHeight"] for page in page_reports) or 1
    weighted = lambda key, source=None: sum(
        (page["logicalWidth"] * page["logicalHeight"]) * ((page.get(source) or {}).get(key, 0) if source else page.get(key, 0))
        for page in page_reports
    ) / total_area
    report = {
        "schemaVersion": 1,
        "jobId": job.id,
        "captureId": job.capture_id,
        # Prove which captured source produced the evidence without persisting
        # a public Canva share token in a checked-in baseline report.
        "sourceFingerprint": hashlib.sha256(capture.source_url.encode("utf-8")).hexdigest(),
        "createdAt": utc_now(),
        "thresholds": thresholds,
        "pages": sorted(page_reports, key=lambda page: page["pageIndex"]),
        "summary": {
            "pageCount": len(page_reports),
            "passedPages": sum(1 for page in page_reports if page["passed"]),
            "figmaVisualSimilarity": weighted("visualSimilarity"),
            "figmaPixelDifference": weighted("pixelDifference"),
            "mismatchRate": weighted("mismatchRate"),
            "exactTextRate": weighted("exactTextRate", "backendMetrics"),
            "nativeCoverage": weighted("nativeCoverage", "backendMetrics"),
            "fallbackCoverage": weighted("fallbackCoverage", "backendMetrics"),
            "missingRegionRate": weighted("missingRegionRate", "backendMetrics"),
            "duplicateTextBlocks": sum(page["backendMetrics"]["duplicateTextBlocks"] for page in page_reports),
            "missingFonts": sorted({
                font
                for page in page_reports
                for font in page["backendMetrics"]["missingFonts"]
            }),
        },
        "passed": all(page["passed"] for page in page_reports),
    }
    services().store.put_figma_qa_report(normalized_id, report)
    return bounded_json(report, 201)


@api.app_errorhandler(ServiceError)
def handle_service_error(error: ServiceError):
    message = public_error_message(error.message)
    logger.warning("%s: %s", error.code, message)
    return error_response(error.code, message, error.status)


@api.app_errorhandler(ValidationError)
def handle_validation_error(error: ValidationError):
    messages = "; ".join(item.get("msg", "Invalid value") for item in error.errors())
    return error_response("INVALID_REQUEST", messages, 400)


@api.app_errorhandler(400)
def handle_bad_request(_error):
    return error_response("INVALID_REQUEST", "Request body must contain valid JSON.", 400)


@api.app_errorhandler(415)
def handle_unsupported_media(_error):
    return error_response("INVALID_REQUEST", "Request body must use application/json.", 415)


@api.app_errorhandler(404)
def handle_not_found(_error):
    return error_response("NOT_FOUND", "The requested converter endpoint does not exist.", 404)


@api.app_errorhandler(405)
def handle_method_not_allowed(_error):
    return error_response("METHOD_NOT_ALLOWED", "The converter endpoint does not support this HTTP method.", 405)


@api.app_errorhandler(413)
def handle_request_too_large(_error):
    return error_response("REQUEST_TOO_LARGE", "The request exceeds the 2MB request-body limit.", 413)


@api.app_errorhandler(Exception)
def handle_unexpected_error(error: Exception):
    logger.exception("Unhandled converter error")
    return error_response("INTERNAL_ERROR", "The converter encountered an unexpected internal error.", 500)


def error_response(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status
