from __future__ import annotations

from datetime import datetime, timezone
import base64
import math
import re
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


PageOrientation = Literal["landscape", "portrait", "square"]
MAX_EMBEDDED_ASSET_BYTES = 25 * 1024 * 1024
MAX_DOCUMENT_ASSET_BYTES = 512 * 1024 * 1024
MAX_PAGE_NODE_COUNT = 5000
MAX_ASSET_BASE64_LENGTH = ((MAX_EMBEDDED_ASSET_BYTES + 2) // 3) * 4
UNSAFE_SVG = re.compile(
    r"<!DOCTYPE|<!ENTITY|@import|<(?:script|foreignObject|iframe|object|embed)\b|\bon[a-z]+\s*=|"
    r"(?:href|xlink:href)\s*=\s*['\"]\s*(?:https?:|data:|javascript:)|"
    r"url\(\s*['\"]?\s*(?:https?:|data:|javascript:|//)",
    re.I,
)
SAFE_SVG_ELEMENTS = {
    "svg", "g", "defs", "title", "desc",
    "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "lineargradient", "radialgradient", "stop",
}


def orientation_for_dimensions(width: float, height: float) -> PageOrientation:
    difference = abs(width - height) / max(width, height)
    return "square" if difference <= 0.01 else "landscape" if width > height else "portrait"


def validate_svg_document(value: str) -> None:
    if UNSAFE_SVG.search(value):
        raise ValueError("Vector SVG contains unsafe active or external content")
    try:
        root = ElementTree.fromstring(value)
    except ElementTree.ParseError as error:
        raise ValueError("Vector SVG must be well-formed XML") from error
    if root.tag.rsplit("}", 1)[-1].casefold() != "svg":
        raise ValueError("Vector content must have an SVG root element")
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].casefold()
        if tag in {"script", "foreignobject", "iframe", "object", "embed"}:
            raise ValueError("Vector SVG contains an unsafe element")
        if tag not in SAFE_SVG_ELEMENTS:
            raise ValueError(f"Vector SVG element is unsupported: {tag}")
        for raw_name, raw_value in element.attrib.items():
            name = raw_name.rsplit("}", 1)[-1].casefold()
            attribute_value = str(raw_value).strip()
            if name.startswith("on"):
                raise ValueError("Vector SVG contains an event handler")
            if name == "href" and attribute_value and not attribute_value.startswith("#"):
                raise ValueError("Vector SVG href references must remain inside the document")
            for match in re.finditer(r"url\(([^)]*)\)", attribute_value, re.I):
                target = match.group(1).strip(" \t\r\n'\"")
                if target and not target.startswith("#"):
                    raise ValueError("Vector SVG paint references must remain inside the document")


def image_signature_matches(mime_type: str, data: bytes) -> bool:
    return {
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": data.startswith(b"\xff\xd8\xff"),
        "image/gif": data.startswith((b"GIF87a", b"GIF89a")),
    }.get(mime_type, False)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, allow_inf_nan=False)

    def json_dict(self, *, exclude_none: bool = True) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=exclude_none)


class Box(ApiModel):
    x: float
    y: float
    width: float = Field(gt=0, le=32768)
    height: float = Field(gt=0, le=32768)


class Color(ApiModel):
    r: float = Field(ge=0, le=1)
    g: float = Field(ge=0, le=1)
    b: float = Field(ge=0, le=1)
    a: float = Field(default=1, ge=0, le=1)


class SolidFill(ApiModel):
    type: Literal["solid"] = "solid"
    color: Color


class GradientStop(ApiModel):
    position: float = Field(ge=0, le=1)
    color: Color


class GradientFill(ApiModel):
    type: Literal["linear-gradient", "radial-gradient"]
    stops: list[GradientStop] = Field(min_length=2, max_length=16)
    transform: list[list[float]] | None = None

    @field_validator("transform")
    @classmethod
    def validate_transform(cls, value: list[list[float]] | None) -> list[list[float]] | None:
        if value is None:
            return None
        if len(value) != 2 or any(len(row) != 3 for row in value):
            raise ValueError("Gradient transform must be a 2x3 affine matrix")
        if any(not math.isfinite(number) or abs(number) > 1_000_000 for row in value for number in row):
            raise ValueError("Gradient transform values must be finite and bounded")
        return value


class ImageFill(ApiModel):
    type: Literal["image"] = "image"
    asset_id: str = Field(alias="assetId", min_length=1, max_length=256)
    scale_mode: Literal["fill", "fit", "crop", "tile"] = Field(default="fill", alias="scaleMode")
    transform: list[list[float]] | None = None

    @field_validator("transform")
    @classmethod
    def validate_transform(cls, value: list[list[float]] | None) -> list[list[float]] | None:
        if value is None:
            return None
        if len(value) != 2 or any(len(row) != 3 for row in value):
            raise ValueError("Image transform must be a 2x3 affine matrix")
        if any(not math.isfinite(number) or abs(number) > 1_000_000 for row in value for number in row):
            raise ValueError("Image transform values must be finite and bounded")
        return value

    @model_validator(mode="after")
    def require_crop_mode_for_transform(self):
        if self.transform is not None and self.scale_mode != "crop":
            raise ValueError("Image transforms require scaleMode=crop")
        return self


FillValue = SolidFill | GradientFill | ImageFill


class Stroke(ApiModel):
    color: Color
    weight: float = Field(ge=0, le=100)
    align: Literal["inside", "outside", "center"] = "inside"


class DropShadowEffect(ApiModel):
    type: Literal["drop-shadow"] = "drop-shadow"
    color: Color
    offset_x: float = Field(default=0, alias="offsetX", ge=-32768, le=32768)
    offset_y: float = Field(default=0, alias="offsetY", ge=-32768, le=32768)
    radius: float = Field(ge=0, le=1000)
    spread: float = Field(default=0, ge=-1000, le=1000)
    visible: bool = True


class LayerBlurEffect(ApiModel):
    type: Literal["layer-blur"] = "layer-blur"
    radius: float = Field(gt=0, le=1000)
    visible: bool = True


EffectValue = DropShadowEffect | LayerBlurEffect


class SourceMeta(ApiModel):
    canva_id: str | None = Field(default=None, alias="canvaId", max_length=512)
    source_type: str = Field(alias="sourceType", min_length=1, max_length=64)
    extraction: Literal["dom", "ocr", "vision", "asset", "fallback"]


class TextRun(ApiModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    font_family: str = Field(default="Inter", alias="fontFamily", min_length=1, max_length=200)
    font_style: str = Field(default="Regular", alias="fontStyle", min_length=1, max_length=100)
    font_size: float = Field(alias="fontSize", gt=0)
    color: Color
    letter_spacing: float | None = Field(default=None, alias="letterSpacing")
    line_height: float | None = Field(default=None, alias="lineHeight", gt=0)
    text_decoration: Literal["none", "underline", "strikethrough"] = Field(default="none", alias="textDecoration")


class DesignNode(ApiModel):
    id: str = Field(min_length=1, max_length=256)
    type: Literal["group", "text", "image", "rectangle", "ellipse", "vector", "raster-fallback"]
    name: str | None = Field(default=None, max_length=500)
    box: Box
    rotation: float = 0
    opacity: float = Field(default=1, ge=0, le=1)
    visible: bool = True
    locked: bool = False
    z_index: int = Field(alias="zIndex")
    confidence: float = Field(ge=0, le=1)
    type_confidence: float | None = Field(default=None, alias="typeConfidence", ge=0, le=1)
    geometry_confidence: float | None = Field(default=None, alias="geometryConfidence", ge=0, le=1)
    style_confidence: float | None = Field(default=None, alias="styleConfidence", ge=0, le=1)
    hierarchy_confidence: float | None = Field(default=None, alias="hierarchyConfidence", ge=0, le=1)
    source: SourceMeta
    children: list["DesignNode"] | None = None
    clips_content: bool | None = Field(default=None, alias="clipsContent")
    text: str | None = Field(default=None, max_length=200_000)
    runs: list[TextRun] | None = None
    horizontal_align: Literal["left", "center", "right", "justify"] | None = Field(default=None, alias="horizontalAlign")
    vertical_align: Literal["top", "center", "bottom"] | None = Field(default=None, alias="verticalAlign")
    fill: ImageFill | None = None
    fills: list[FillValue] | None = None
    stroke: Stroke | None = None
    effects: list[EffectValue] | None = Field(default=None, max_length=8)
    corner_radius: float | None = Field(default=None, alias="cornerRadius", ge=0)
    svg: str | None = Field(default=None, max_length=5_000_000)
    asset_id: str | None = Field(default=None, alias="assetId", max_length=256)
    reason: str | None = Field(default=None, max_length=1200)

    @model_validator(mode="after")
    def validate_node_shape(self):
        if self.type == "group" and self.children is None:
            raise ValueError("Group nodes require children")
        if self.type != "group" and self.children is not None:
            raise ValueError("Only group nodes may contain children")
        if self.type != "group" and self.clips_content is not None:
            raise ValueError("Only group nodes may define clipsContent")
        if self.type == "text":
            if self.text is None or not self.runs:
                raise ValueError("Text nodes require text and at least one run")
            previous_end = 0
            for run in self.runs:
                if run.end <= run.start or run.end > len(self.text) or run.start != previous_end:
                    raise ValueError("Text runs must be contiguous, ordered, and inside the text")
                previous_end = run.end
            if previous_end != len(self.text):
                raise ValueError("Text runs must cover the complete text")
        if self.type == "image" and self.fill is None:
            raise ValueError("Image nodes require an image fill")
        if self.type in {"rectangle", "ellipse"} and not self.fills:
            raise ValueError(f"{self.type} nodes require at least one fill")
        if self.type == "vector" and not self.svg:
            raise ValueError("Vector nodes require SVG content")
        if self.type == "vector" and self.svg:
            validate_svg_document(self.svg)
        if self.type == "raster-fallback" and (not self.asset_id or not self.reason):
            raise ValueError("Raster fallback nodes require assetId and reason")
        return self


class Asset(ApiModel):
    id: str = Field(min_length=1, max_length=256)
    mime_type: Literal["image/png", "image/jpeg", "image/gif"] = Field(alias="mimeType")
    data_base64: str | None = Field(default=None, alias="dataBase64", max_length=MAX_ASSET_BASE64_LENGTH)
    url: HttpUrl | None = None
    width: float | None = Field(default=None, gt=0)
    height: float | None = Field(default=None, gt=0)
    source: Literal["canva", "crop", "reference"]

    @model_validator(mode="after")
    def require_data(self):
        if bool(self.data_base64) == bool(self.url):
            raise ValueError("Asset requires exactly one of dataBase64 or url")
        if self.data_base64:
            try:
                decoded = base64.b64decode(self.data_base64, validate=True)
            except ValueError as error:
                raise ValueError("Asset dataBase64 must contain valid base64") from error
            if len(decoded) > MAX_EMBEDDED_ASSET_BYTES:
                raise ValueError("Embedded asset exceeds the 25MB decoded-byte limit")
            if not image_signature_matches(self.mime_type, decoded):
                raise ValueError("Asset bytes do not match the declared image mimeType")
        return self


class ConversionMetrics(ApiModel):
    native_coverage: float = Field(alias="nativeCoverage", ge=0, le=1)
    fallback_coverage: float = Field(alias="fallbackCoverage", ge=0, le=1)
    exact_text_rate: float = Field(alias="exactTextRate", ge=0, le=1)
    visual_similarity: float = Field(default=0, alias="visualSimilarity", ge=0, le=1)
    pixel_difference: float = Field(default=1, alias="pixelDifference", ge=0, le=1)
    missing_region_rate: float = Field(default=0, alias="missingRegionRate", ge=0, le=1)
    duplicate_text_blocks: int = Field(default=0, alias="duplicateTextBlocks", ge=0)
    missing_fonts: list[str] = Field(alias="missingFonts")
    warnings: list[str]

    @model_validator(mode="after")
    def validate_non_overlapping_coverage(self):
        if self.native_coverage + self.fallback_coverage > 1.000001:
            raise ValueError("Native and fallback coverage cannot exceed the visible page area")
        return self


class DesignPage(ApiModel):
    id: str = Field(min_length=1, max_length=256)
    name: str = Field(max_length=500)
    width: float = Field(gt=0, le=32768)
    height: float = Field(gt=0, le=32768)
    orientation: PageOrientation | None = None
    background: FillValue | None = None
    children: list[DesignNode] = Field(max_length=5000)
    qa_reference_asset_id: str = Field(alias="qaReferenceAssetId", min_length=1, max_length=256)
    metrics: ConversionMetrics

    @model_validator(mode="after")
    def detect_orientation(self):
        detected = orientation_for_dimensions(self.width, self.height)
        if self.orientation is not None and self.orientation != detected:
            raise ValueError("Design page orientation does not match its dimensions")
        self.orientation = detected
        return self


class DesignDocumentV1(ApiModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=500)
    source_url: HttpUrl = Field(alias="sourceUrl")
    created_at: str = Field(alias="createdAt", min_length=1, max_length=64)
    pages: list[DesignPage] = Field(min_length=1)
    assets: dict[str, Asset]

    @field_validator("source_url")
    @classmethod
    def validate_canva_source(cls, value: HttpUrl) -> HttpUrl:
        host = (urlsplit(str(value)).hostname or "").casefold()
        if host not in {"canva.com", "www.canva.com", "canva.link"}:
            raise ValueError("sourceUrl must be a Canva design or share URL")
        path = urlsplit(str(value)).path
        if host == "canva.link":
            if not re.fullmatch(r"/[A-Za-z0-9_-]{1,256}/?", path):
                raise ValueError("sourceUrl must contain a valid Canva short-link path")
        elif not re.match(r"^/(?:[A-Za-z]{2}(?:[_-][A-Za-z]{2})?/)?design/[A-Za-z0-9_-]{1,256}(?:/|$)", path, re.I):
            raise ValueError("sourceUrl must contain a Canva design path")
        return value

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("createdAt must be an ISO-8601 timestamp") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("createdAt must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_cross_references(self):
        node_ids: set[str] = set()
        page_ids: set[str] = set()
        page_node_count = 0

        def walk(nodes: list[DesignNode]) -> None:
            nonlocal page_node_count
            for node in nodes:
                page_node_count += 1
                if page_node_count > MAX_PAGE_NODE_COUNT:
                    raise ValueError(f"Page exceeds the {MAX_PAGE_NODE_COUNT}-node limit")
                if node.id in node_ids:
                    raise ValueError(f"Duplicate node id: {node.id}")
                node_ids.add(node.id)
                if node.asset_id and node.asset_id not in self.assets:
                    raise ValueError(f"Missing node asset: {node.asset_id}")
                if node.fill and node.fill.asset_id not in self.assets:
                    raise ValueError(f"Missing image fill asset: {node.fill.asset_id}")
                for fill in node.fills or []:
                    if isinstance(fill, ImageFill) and fill.asset_id not in self.assets:
                        raise ValueError(f"Missing fill asset: {fill.asset_id}")
                walk(node.children or [])

        for page in self.pages:
            if page.id in page_ids:
                raise ValueError(f"Duplicate page id: {page.id}")
            page_ids.add(page.id)
            page_node_count = 0
            if page.qa_reference_asset_id not in self.assets:
                raise ValueError(f"Missing QA reference asset: {page.qa_reference_asset_id}")
            qa_asset = self.assets[page.qa_reference_asset_id]
            if qa_asset.source != "reference":
                raise ValueError(f"QA reference asset must use source=reference: {page.qa_reference_asset_id}")
            if isinstance(page.background, ImageFill) and page.background.asset_id not in self.assets:
                raise ValueError(f"Missing page background asset: {page.background.asset_id}")
            walk(page.children)
        for key, asset in self.assets.items():
            if key != asset.id:
                raise ValueError(f"Asset map key does not match asset id: {key}")
        decoded_total = sum(
            len(base64.b64decode(asset.data_base64, validate=True))
            for asset in self.assets.values()
            if asset.data_base64
        )
        if decoded_total > MAX_DOCUMENT_ASSET_BYTES:
            raise ValueError("Design document exceeds the 512MB embedded-asset limit")
        return self


class DomTextHint(ApiModel):
    text: str
    x: float
    y: float
    width: float
    height: float
    font_size: float | None = Field(default=None, alias="fontSize")
    font_family: str | None = Field(default=None, alias="fontFamily")
    font_weight: str | None = Field(default=None, alias="fontWeight")
    font_style: str | None = Field(default=None, alias="fontStyle")
    color: str | None = None
    text_align: str | None = Field(default=None, alias="textAlign")
    line_height: float | None = Field(default=None, alias="lineHeight")
    letter_spacing: float | None = Field(default=None, alias="letterSpacing")
    text_decoration: str | None = Field(default=None, alias="textDecoration")


class DomImageHint(ApiModel):
    src: str = Field(min_length=1, max_length=4096)
    x: float
    y: float
    width: float = Field(gt=0, le=32768)
    height: float = Field(gt=0, le=32768)
    alt: str | None = Field(default=None, max_length=1000)
    natural_width: int | None = Field(default=None, alias="naturalWidth", gt=0, le=32768)
    natural_height: int | None = Field(default=None, alias="naturalHeight", gt=0, le=32768)
    object_fit: Literal["fill", "contain", "cover", "none", "scale-down"] = Field(default="fill", alias="objectFit")
    object_position_x: float = Field(default=0.5, alias="objectPositionX", ge=0, le=1)
    object_position_y: float = Field(default=0.5, alias="objectPositionY", ge=0, le=1)
    corner_radius: float = Field(default=0, alias="cornerRadius", ge=0, le=16384)


class CapturedPage(ApiModel):
    id: str
    index: int = Field(ge=0)
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)
    orientation: PageOrientation | None = None
    screenshot_base64: str = Field(alias="screenshotBase64", min_length=1)
    text_hints: list[DomTextHint] = Field(default_factory=list, alias="textHints")
    image_hints: list[DomImageHint] = Field(default_factory=list, alias="imageHints")

    @model_validator(mode="after")
    def detect_orientation(self):
        # Browser scaling can introduce a one-pixel rounding difference for a
        # square Canva page, so dimensions within 1% are treated as square.
        detected = orientation_for_dimensions(self.width, self.height)
        if self.orientation is not None and self.orientation != detected:
            raise ValueError("Captured page orientation does not match its dimensions")
        self.orientation = detected
        return self


class CaptureRecord(ApiModel):
    id: str
    source_url: str = Field(alias="sourceUrl")
    title: str
    created_at: str = Field(alias="createdAt")
    expires_at: float = Field(alias="expiresAt")
    pages: list[CapturedPage]


CaptureJobStatus = Literal["queued", "capturing", "completed", "failed", "cancelled"]


class CaptureJob(ApiModel):
    id: str
    url: str
    source: Literal["public-url", "canva-oauth"] = "public-url"
    status: CaptureJobStatus
    progress: int = Field(ge=0, le=100)
    current_page: int | None = Field(default=None, alias="currentPage", ge=0)
    total_pages: int = Field(default=0, alias="totalPages", ge=0)
    message: str
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    capture_id: str | None = Field(default=None, alias="captureId")
    error: "JobError | None" = None
    cancelled: bool = False


JobStatus = Literal["queued", "capturing", "analyzing", "rendering", "completed", "failed", "cancelled"]


class JobError(ApiModel):
    code: str
    message: str


class ReconstructionJob(ApiModel):
    id: str
    capture_id: str = Field(alias="captureId")
    page_ids: list[str] = Field(alias="pageIds")
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    current_page: int | None = Field(default=None, alias="currentPage")
    total_pages: int = Field(alias="totalPages", ge=1)
    message: str
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    error: JobError | None = None
    result: DesignDocumentV1 | None = None
    cancelled: bool = False


class OcrBlock(ApiModel):
    id: str
    text: str
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    confidence: float = Field(ge=0, le=1)


class OcrResult(ApiModel):
    blocks: list[OcrBlock]
    full_text: str = Field(alias="fullText")


class ReconstructedElement(ApiModel):
    id: str
    type: Literal["text", "image", "rectangle", "ellipse", "vector", "group", "unsupported"]
    name: str | None = None
    parent_id: str | None = Field(default=None, alias="parentId")
    text: str | None = None
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: float = 0
    opacity: float = Field(default=1, ge=0, le=1)
    z_index: int = Field(alias="zIndex")
    confidence: float = Field(ge=0, le=1)
    type_confidence: float | None = Field(default=None, alias="typeConfidence", ge=0, le=1)
    geometry_confidence: float | None = Field(default=None, alias="geometryConfidence", ge=0, le=1)
    style_confidence: float | None = Field(default=None, alias="styleConfidence", ge=0, le=1)
    hierarchy_confidence: float | None = Field(default=None, alias="hierarchyConfidence", ge=0, le=1)
    fill_color: str | None = Field(default=None, alias="fillColor")
    stroke_color: str | None = Field(default=None, alias="strokeColor")
    stroke_weight: float | None = Field(default=None, alias="strokeWeight", ge=0)
    corner_radius: float | None = Field(default=None, alias="cornerRadius", ge=0)
    gradient_start_color: str | None = Field(default=None, alias="gradientStartColor")
    gradient_end_color: str | None = Field(default=None, alias="gradientEndColor")
    gradient_angle: float | None = Field(default=None, alias="gradientAngle", ge=-3600, le=3600)
    shadow_color: str | None = Field(default=None, alias="shadowColor")
    shadow_offset_x: float | None = Field(default=None, alias="shadowOffsetX", ge=-32768, le=32768)
    shadow_offset_y: float | None = Field(default=None, alias="shadowOffsetY", ge=-32768, le=32768)
    shadow_blur: float | None = Field(default=None, alias="shadowBlur", ge=0, le=1000)
    shadow_spread: float | None = Field(default=None, alias="shadowSpread", ge=-1000, le=1000)
    blur_radius: float | None = Field(default=None, alias="blurRadius", ge=0, le=1000)
    svg: str | None = None
    reason: str | None = None
    clips_content: bool = Field(default=False, alias="clipsContent")


class ReconstructedPage(ApiModel):
    background_color: str = Field(default="#ffffff", alias="backgroundColor", pattern=r"^#[0-9a-fA-F]{6}$")
    elements: list[ReconstructedElement] = Field(max_length=1000)


class CaptureRequest(ApiModel):
    url: HttpUrl


class ReconstructionRequest(ApiModel):
    capture_id: str = Field(alias="captureId")
    page_ids: list[str] = Field(alias="pageIds", min_length=1)


class FigmaQaPageSubmission(ApiModel):
    page_id: str = Field(alias="pageId", min_length=1, max_length=256)
    export_width: int = Field(alias="exportWidth", gt=0, le=8192)
    export_height: int = Field(alias="exportHeight", gt=0, le=8192)
    compared_width: int = Field(alias="comparedWidth", gt=0, le=2048)
    compared_height: int = Field(alias="comparedHeight", gt=0, le=2048)
    export_byte_length: int = Field(alias="exportByteLength", gt=0, le=25 * 1024 * 1024)
    export_sha256: str = Field(alias="exportSha256", pattern=r"^[0-9a-f]{64}$")
    pixel_difference: float = Field(alias="pixelDifference", ge=0, le=1)
    visual_similarity: float = Field(alias="visualSimilarity", ge=0, le=1)
    mismatch_rate: float = Field(alias="mismatchRate", ge=0, le=1)

    @model_validator(mode="after")
    def validate_similarity_pair(self):
        if abs((self.pixel_difference + self.visual_similarity) - 1) > 0.001:
            raise ValueError("Figma QA visualSimilarity must equal 1 - pixelDifference")
        export_ratio = self.export_width / self.export_height
        compared_ratio = self.compared_width / self.compared_height
        if abs(export_ratio - compared_ratio) / max(export_ratio, compared_ratio) > 0.01:
            raise ValueError("Figma QA comparison dimensions must preserve the export aspect ratio")
        return self


class FigmaQaSubmission(ApiModel):
    pages: list[FigmaQaPageSubmission] = Field(min_length=1, max_length=10_000)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid4())


def validate_uuid(value: Any) -> str:
    return str(UUID(str(value)))
