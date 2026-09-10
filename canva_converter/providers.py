from __future__ import annotations

import json
import re
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import Settings
from .models import OcrBlock, OcrResult, ReconstructedPage


class OcrProvider(Protocol):
    def detect(self, image_base64: str, image_width: int, image_height: int) -> OcrResult: ...


class LayoutProvider(Protocol):
    def analyze(
        self,
        image_base64: str,
        width: int,
        height: int,
        ocr: OcrResult,
        text_hints: list[dict],
        image_hints: list[dict],
    ) -> ReconstructedPage: ...


def _post_json(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers=request_headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
            if not isinstance(body, dict):
                raise RuntimeError("Provider returned a non-object JSON response.")
            return body
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Provider failed ({error.code}): {body[:2000]}") from error
    except URLError as error:
        raise RuntimeError(f"Provider connection failed: {error.reason}") from error


def _structured_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, re.I)
    if fenced:
        cleaned = fenced.group(1)
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("Structured response must be a JSON object.")
    return payload


class OpenAIVisionOcrProvider:
    def __init__(self, settings: Settings):
        self.api_key = settings.openai_api_key
        self.model = settings.openai_model

    def detect(self, image_base64: str, image_width: int, image_height: int) -> OcrResult:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")
        if image_width < 1 or image_height < 1:
            raise ValueError("OCR image dimensions must be positive.")
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["blocks", "fullText"],
            "properties": {
                "blocks": {
                    "type": "array",
                    "maxItems": 1000,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "text", "x", "y", "width", "height", "confidence"],
                        "properties": {
                            "id": {"type": "string"},
                            "text": {"type": "string"},
                            "x": {"type": "number", "minimum": 0},
                            "y": {"type": "number", "minimum": 0},
                            "width": {"type": "number", "exclusiveMinimum": 0},
                            "height": {"type": "number", "exclusiveMinimum": 0},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                    },
                },
                "fullText": {"type": "string"},
            },
        }
        prompt = "\n".join([
            "Transcribe every visible text token in this Canva page exactly as shown.",
            f"The source PNG is exactly {image_width} pixels wide and {image_height} pixels high.",
            "Return one block per word or contiguous punctuation token in natural reading order.",
            "Coordinates are axis-aligned pixel bounds in the original source PNG, with origin at its top-left.",
            "Keep every x, y, width, and height inside the stated source dimensions.",
            "Preserve spelling, capitalization, punctuation, and repeated visible text. Do not translate or infer hidden text.",
            "fullText must contain the same detected text in natural reading order.",
            "Confidence expresses transcription and localization confidence from 0 to 1.",
        ])
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                attempt_prompt = prompt if attempt == 0 else f"{prompt}\nThe previous response was invalid. Return only JSON matching the schema exactly."
                payload = {
                    "model": self.model,
                    "store": False,
                    "input": [{
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": attempt_prompt},
                            {"type": "input_image", "image_url": f"data:image/png;base64,{image_base64}", "detail": "high"},
                        ],
                    }],
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": "canva_ocr_result",
                            "strict": True,
                            "schema": schema,
                        },
                    },
                    "max_output_tokens": 32768,
                }
                body = _post_json(
                    "https://api.openai.com/v1/responses",
                    payload,
                    timeout=90,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                return OcrResult.model_validate(_structured_json(_openai_output_text(body)))
            except Exception as error:
                last_error = error
        raise last_error or RuntimeError("OpenAI OCR failed.")


def _openai_output_text(body: dict[str, Any]) -> str:
    if body.get("error"):
        error = body["error"]
        if isinstance(error, dict):
            raise RuntimeError(f"OpenAI failed: {error.get('message', 'unknown error')}")
        raise RuntimeError("OpenAI failed with an unknown error.")
    if body.get("status") == "failed":
        raise RuntimeError("OpenAI response failed.")
    if body.get("status") == "incomplete":
        reason = (body.get("incomplete_details") or {}).get("reason", "unknown reason")
        raise RuntimeError(f"OpenAI response was incomplete: {reason}")

    top_level = body.get("output_text")
    if isinstance(top_level, str) and top_level.strip():
        return top_level

    texts: list[str] = []
    refusals: list[str] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                texts.append(content["text"])
            elif content.get("type") == "refusal" and isinstance(content.get("refusal"), str):
                refusals.append(content["refusal"])
    if texts:
        return "".join(texts)
    if refusals:
        raise RuntimeError(f"OpenAI refused the image-analysis request: {' '.join(refusals)}")
    raise RuntimeError("OpenAI returned no structured content.")


class OpenAILayoutProvider:
    def __init__(self, settings: Settings):
        self.api_key = settings.openai_api_key
        self.model = settings.openai_model

    def analyze(self, image_base64: str, width: int, height: int, ocr: OcrResult, text_hints: list[dict], image_hints: list[dict]) -> ReconstructedPage:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")
        prompt = "\n".join([
            "Reconstruct this fixed-size Canva design as a flat list of editable visual elements.",
            f"Canvas is {width}x{height} logical pixels.",
            "Use only text, image, rectangle, ellipse, vector, group, or unsupported.",
            "Coordinates must be logical pixels relative to the top-left. Preserve z-order.",
            "Set clipsContent true only for a group whose rectangular bounds visibly clip its children; otherwise false.",
            "Classify non-rectangular masks or clipping paths as unsupported and start reason with 'Unsupported mask:'.",
            "Use gradient fields only for a confident simple two-stop linear gradient; otherwise leave them null and preserve the region as unsupported.",
            "Use shadow fields only for a confident ordinary drop shadow and blurRadius only for a simple layer blur. Complex effects must be unsupported with a reason starting 'Unsupported effect:'.",
            "SVG vectors may use safe rect, circle, ellipse, line, polyline, polygon, path, and g primitives. Filters, masks, animation, text, embedded images, and external references are unsupported.",
            "Text elements describe hierarchy, rotation, opacity, and z-order only; OCR supplies final text and bounds.",
            "Do not duplicate elements or classify the whole page as one image.",
            "Mark uncertain complex regions unsupported.",
            "Score typeConfidence, geometryConfidence, styleConfidence, and hierarchyConfidence independently.",
            "A property confidence must be low when that specific property cannot be verified from pixels; do not copy one confidence into every field by habit.",
            "OCR boxes and DOM hints are evidence; do not duplicate the same visible text.",
            f"OCR: {json.dumps([block.json_dict() for block in ocr.blocks[:400]], separators=(',', ':'))}",
            f"DOM text hints: {json.dumps(text_hints[:200], separators=(',', ':'))}",
            f"DOM image hints: {json.dumps(image_hints[:100], separators=(',', ':'))}",
        ])
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["backgroundColor", "elements"],
            "properties": {
                "backgroundColor": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
                "elements": {
                    "type": "array",
                    "maxItems": 1000,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "id", "type", "name", "parentId", "text", "x", "y", "width", "height",
                            "rotation", "opacity", "zIndex", "confidence", "fillColor", "strokeColor",
                            "typeConfidence", "geometryConfidence", "styleConfidence", "hierarchyConfidence",
                            "strokeWeight", "cornerRadius", "svg", "reason",
                            "clipsContent",
                            "gradientStartColor", "gradientEndColor", "gradientAngle",
                            "shadowColor", "shadowOffsetX", "shadowOffsetY", "shadowBlur", "shadowSpread", "blurRadius",
                        ],
                        "properties": {
                            "id": {"type": "string"},
                            "type": {"type": "string", "enum": ["text", "image", "rectangle", "ellipse", "vector", "group", "unsupported"]},
                            "name": {"type": ["string", "null"]},
                            "parentId": {"type": ["string", "null"]},
                            "text": {"type": ["string", "null"]},
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "width": {"type": "number", "exclusiveMinimum": 0},
                            "height": {"type": "number", "exclusiveMinimum": 0},
                            "rotation": {"type": "number"},
                            "opacity": {"type": "number", "minimum": 0, "maximum": 1},
                            "zIndex": {"type": "integer"},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "typeConfidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "geometryConfidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "styleConfidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "hierarchyConfidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "fillColor": {"type": ["string", "null"]},
                            "strokeColor": {"type": ["string", "null"]},
                            "strokeWeight": {"type": ["number", "null"], "minimum": 0},
                            "cornerRadius": {"type": ["number", "null"], "minimum": 0},
                            "svg": {"type": ["string", "null"]},
                            "reason": {"type": ["string", "null"]},
                            "clipsContent": {"type": "boolean"},
                            "gradientStartColor": {"type": ["string", "null"]},
                            "gradientEndColor": {"type": ["string", "null"]},
                            "gradientAngle": {"type": ["number", "null"], "minimum": -3600, "maximum": 3600},
                            "shadowColor": {"type": ["string", "null"]},
                            "shadowOffsetX": {"type": ["number", "null"], "minimum": -32768, "maximum": 32768},
                            "shadowOffsetY": {"type": ["number", "null"], "minimum": -32768, "maximum": 32768},
                            "shadowBlur": {"type": ["number", "null"], "minimum": 0, "maximum": 1000},
                            "shadowSpread": {"type": ["number", "null"], "minimum": -1000, "maximum": 1000},
                            "blurRadius": {"type": ["number", "null"], "minimum": 0, "maximum": 1000},
                        },
                    },
                },
            },
        }
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                attempt_prompt = prompt if attempt == 0 else f"{prompt}\nThe previous response was invalid. Return only JSON matching the schema exactly."
                payload = {
                    "model": self.model,
                    "store": False,
                    "input": [{
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": attempt_prompt},
                            {"type": "input_image", "image_url": f"data:image/png;base64,{image_base64}", "detail": "high"},
                        ],
                    }],
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": "canva_reconstructed_page",
                            "strict": True,
                            "schema": schema,
                        },
                    },
                    "max_output_tokens": 32768,
                }
                body = _post_json(
                    "https://api.openai.com/v1/responses",
                    payload,
                    timeout=90,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                return ReconstructedPage.model_validate(_structured_json(_openai_output_text(body)))
            except Exception as error:
                last_error = error
        raise last_error or RuntimeError("OpenAI reconstruction failed.")
