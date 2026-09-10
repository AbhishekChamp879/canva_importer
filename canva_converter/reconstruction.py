from __future__ import annotations

import base64
from collections import Counter
from difflib import SequenceMatcher
import hashlib
import io
import math
import re
from typing import Callable

from PIL import Image

from .asset_fetch import DownloadedCanvaAsset, download_canva_asset
from .errors import public_error_message

from .models import (
    Asset,
    Box,
    CaptureRecord,
    CapturedPage,
    Color,
    ConversionMetrics,
    DesignDocumentV1,
    DesignNode,
    DesignPage,
    DropShadowEffect,
    DomImageHint,
    DomTextHint,
    GradientFill,
    GradientStop,
    ImageFill,
    LayerBlurEffect,
    OcrBlock,
    OcrResult,
    ReconstructedElement,
    ReconstructedPage,
    SolidFill,
    SourceMeta,
    Stroke,
    TextRun,
    new_id,
    utc_now,
    validate_svg_document,
)
from .providers import LayoutProvider, OcrProvider
from .qa import evaluate_page_quality


MIN_CONFIDENCE = 0.68
MIN_TYPE_CONFIDENCE = 0.72
MIN_GEOMETRY_CONFIDENCE = 0.72
MIN_STYLE_CONFIDENCE = 0.70
MIN_HIERARCHY_CONFIDENCE = 0.78
Progress = Callable[[int, str], None]
Cancelled = Callable[[], bool]


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def parse_color(value: str | None = "#000000") -> Color:
    text = value or "#000000"
    match = re.fullmatch(r"#([0-9a-fA-F]{6})", text)
    if match:
        raw = match.group(1)
        return Color(r=int(raw[0:2], 16) / 255, g=int(raw[2:4], 16) / 255, b=int(raw[4:6], 16) / 255, a=1)
    match = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?", text, re.I)
    if match:
        return Color(r=int(match.group(1)) / 255, g=int(match.group(2)) / 255, b=int(match.group(3)) / 255, a=float(match.group(4) or 1))
    return Color(r=0, g=0, b=0, a=1)


def group_ocr_into_lines(blocks: list[OcrBlock]) -> list[OcrBlock]:
    lines: list[list[OcrBlock]] = []
    for block in sorted(blocks, key=lambda item: (item.y, item.x)):
        target = next((
            line for line in lines
            if abs((line[0].y + line[0].height / 2) - (block.y + block.height / 2)) <= max(line[0].height, block.height) * 0.55
            and block.x - max(item.x + item.width for item in line) <= max(80, block.height * 4)
        ), None)
        if target is None:
            lines.append([block])
        else:
            target.append(block)
    output: list[OcrBlock] = []
    for index, line in enumerate(lines):
        line.sort(key=lambda item: item.x)
        x, y = min(item.x for item in line), min(item.y for item in line)
        right, bottom = max(item.x + item.width for item in line), max(item.y + item.height for item in line)
        text = ""
        for item in line:
            token = item.text.strip()
            if not token:
                continue
            if not text or re.fullmatch(r"[.,!?;:%)\]}]+", token) or token.startswith(("'", "’")):
                text += token
            elif text.endswith(("(", "[", "{", "'", "’")):
                text += token
            else:
                text += " " + token
        output.append(OcrBlock(id=f"line-{index}", text=text, x=x, y=y, width=max(1, right - x), height=max(1, bottom - y), confidence=min(item.confidence for item in line)))
    return output


def closest_text_hint(block: OcrBlock, hints: list[DomTextHint]) -> DomTextHint | None:
    block_text = block.text.casefold()
    candidates = [hint for hint in hints if block_text in hint.text.casefold() or hint.text.casefold() in block_text]
    return min(candidates, key=lambda hint: math.hypot(hint.x - block.x, hint.y - block.y), default=None)


def overlap(first, second) -> float:
    width = max(0, min(first.x + first.width, second.x + second.width) - max(first.x, second.x))
    height = max(0, min(first.y + first.height, second.y + second.height) - max(first.y, second.y))
    return width * height


def intersection_over_union(first, second) -> float:
    intersection = overlap(first, second)
    if intersection <= 0:
        return 0
    union = first.width * first.height + second.width * second.height - intersection
    return intersection / union if union > 0 else 0


def normalized_text(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def property_confidence(element: ReconstructedElement, property_name: str) -> float:
    """Return property evidence while accepting legacy single-confidence results."""
    value = getattr(element, f"{property_name}_confidence", None)
    return element.confidence if value is None else value


def native_fallback_reason(element: ReconstructedElement) -> str | None:
    if property_confidence(element, "type") < MIN_TYPE_CONFIDENCE:
        return f"Low type confidence ({property_confidence(element, 'type'):.2f})"
    if property_confidence(element, "geometry") < MIN_GEOMETRY_CONFIDENCE:
        return f"Low geometry confidence ({property_confidence(element, 'geometry'):.2f})"
    if element.type in {"rectangle", "ellipse", "vector"} and property_confidence(element, "style") < MIN_STYLE_CONFIDENCE:
        return f"Low style confidence ({property_confidence(element, 'style'):.2f})"
    return None


def deduplicate_ocr_blocks(blocks: list[OcrBlock]) -> list[OcrBlock]:
    kept: list[OcrBlock] = []
    for block in sorted(blocks, key=lambda item: (-item.confidence, item.y, item.x, item.id)):
        text = normalized_text(block.text)
        if not text:
            continue
        duplicate = any(
            normalized_text(other.text) == text and intersection_over_union(block, other) >= 0.72
            for other in kept
        )
        if not duplicate:
            kept.append(block)
    return sorted(kept, key=lambda item: (item.y, item.x, item.id))


def deduplicate_layout_elements(elements: list[ReconstructedElement]) -> list[ReconstructedElement]:
    """Remove model duplicates deterministically while preserving the strongest region."""
    kept: list[ReconstructedElement] = []
    for element in sorted(elements, key=lambda item: (-item.confidence, item.z_index, item.id)):
        duplicate = element.type != "group" and any(
            element.type == other.type
            and normalized_text(element.text) == normalized_text(other.text)
            and intersection_over_union(element, other) >= 0.82
            for other in kept
        )
        if not duplicate:
            kept.append(element)
    return sorted(kept, key=lambda item: (item.z_index, item.id))


def normalize_layout_coordinates(layout: ReconstructedPage, page: CapturedPage, source: Image.Image) -> ReconstructedPage:
    """Correct a common model error where screenshot pixels are returned instead of logical pixels."""
    candidates = [element for element in layout.elements if element.type != "group"] or layout.elements
    if not candidates or source.width <= page.width * 1.2 and source.height <= page.height * 1.2:
        return layout
    outside = sum(
        element.x + element.width > page.width * 1.15 or element.y + element.height > page.height * 1.15
        for element in candidates
    )
    if outside / len(candidates) < 0.5:
        return layout
    scale_x, scale_y = page.width / source.width, page.height / source.height
    return layout.model_copy(update={
        "elements": [
            element.model_copy(update={
                "x": element.x * scale_x,
                "y": element.y * scale_y,
                "width": element.width * scale_x,
                "height": element.height * scale_y,
            })
            for element in layout.elements
        ]
    })


def figma_font_style(hint: DomTextHint | None) -> str:
    if not hint:
        return "Regular"
    raw_weight = (hint.font_weight or "400").strip().casefold()
    named = {
        "thin": "Thin", "extralight": "Extra Light", "extra light": "Extra Light",
        "light": "Light", "normal": "Regular", "regular": "Regular",
        "medium": "Medium", "semibold": "Semi Bold", "semi bold": "Semi Bold",
        "bold": "Bold", "extrabold": "Extra Bold", "extra bold": "Extra Bold", "black": "Black",
    }
    try:
        numeric = int(float(raw_weight))
        style = (
            "Thin" if numeric < 200 else "Extra Light" if numeric < 300 else
            "Light" if numeric < 400 else "Regular" if numeric < 500 else
            "Medium" if numeric < 600 else "Semi Bold" if numeric < 700 else
            "Bold" if numeric < 800 else "Extra Bold" if numeric < 900 else "Black"
        )
    except ValueError:
        style = named.get(raw_weight.replace("-", " "), named.get(raw_weight, "Regular"))
    if (hint.font_style or "").casefold() in {"italic", "oblique"}:
        style += " Italic"
    return style


def normalize_font_family(value: str | None) -> str:
    """Turn a CSS font-family declaration into one deterministic Figma family."""
    if not value:
        return "Inter"
    first = value.split(",", 1)[0].strip().strip("'\"")
    first = re.sub(r"\s+", " ", first)
    generic = {
        "sans-serif": "Inter",
        "system-ui": "Inter",
        "-apple-system": "Inter",
        "blinkmacsystemfont": "Inter",
        "serif": "Times New Roman",
        "monospace": "Roboto Mono",
    }
    if not first or first.casefold().startswith("var("):
        return "Inter"
    return generic.get(first.casefold(), first[:200])


def text_metrics(line: OcrBlock, hint: DomTextHint | None) -> tuple[float, float, float | None]:
    """Estimate Figma metrics while keeping OCR geometry authoritative."""
    measured_size = hint.font_size if hint and hint.font_size and 4 <= hint.font_size <= 512 else line.height * 0.82
    font_size = clamp(measured_size, 4, 512)
    hinted_line_height = hint.line_height if hint and hint.line_height and hint.line_height >= font_size * 0.65 else None
    line_height = clamp(hinted_line_height or max(line.height * 1.2, font_size * 1.12), 1, 4096)
    letter_spacing = hint.letter_spacing if hint and hint.letter_spacing is not None and abs(hint.letter_spacing) <= 1000 else None
    return font_size, line_height, letter_spacing


def text_decoration(hint: DomTextHint | None) -> str:
    value = (hint.text_decoration if hint else "") or ""
    if "line-through" in value:
        return "strikethrough"
    if "underline" in value:
        return "underline"
    return "none"


def _quantized_rgb(pixel) -> tuple[int, int, int]:
    red, green, blue = pixel[:3]
    return tuple(min(255, (int(channel) // 16) * 16 + 8) for channel in (red, green, blue))


def sample_dominant_color(source: Image.Image, page: CapturedPage, element: ReconstructedElement, minimum_dominance: float = 0.42) -> Color | None:
    scale_x, scale_y = source.width / page.width, source.height / page.height
    left = int(clamp(math.floor(element.x * scale_x), 0, source.width - 1))
    top = int(clamp(math.floor(element.y * scale_y), 0, source.height - 1))
    right = int(clamp(math.ceil((element.x + element.width) * scale_x), left + 1, source.width))
    bottom = int(clamp(math.ceil((element.y + element.height) * scale_y), top + 1, source.height))
    region = source.crop((left, top, right, bottom)).convert("RGB")
    region.thumbnail((64, 64), Image.Resampling.BILINEAR)
    inset_x, inset_y = max(1, round(region.width * 0.08)), max(1, round(region.height * 0.08))
    samples = []
    for y in range(inset_y, max(inset_y + 1, region.height - inset_y)):
        for x in range(inset_x, max(inset_x + 1, region.width - inset_x)):
            if element.type == "ellipse":
                normalized_x = (x + 0.5 - region.width / 2) / max(1, region.width / 2 - inset_x)
                normalized_y = (y + 0.5 - region.height / 2) / max(1, region.height / 2 - inset_y)
                if normalized_x * normalized_x + normalized_y * normalized_y > 0.82:
                    continue
            samples.append(_quantized_rgb(region.getpixel((x, y))))
    if not samples:
        return None
    (red, green, blue), count = Counter(samples).most_common(1)[0]
    if count / len(samples) < minimum_dominance:
        return None
    return Color(r=red / 255, g=green / 255, b=blue / 255, a=1)


def sample_background_color(source: Image.Image, page: CapturedPage) -> Color | None:
    image = source.convert("RGB")
    inset_x = max(0, round((image.width - 1) * 0.02))
    inset_y = max(0, round((image.height - 1) * 0.02))
    right, bottom = image.width - 1 - inset_x, image.height - 1 - inset_y
    middle_x, middle_y = image.width // 2, image.height // 2
    points = [
        (inset_x, inset_y), (right, inset_y), (inset_x, bottom), (right, bottom),
        (middle_x, inset_y), (middle_x, bottom), (inset_x, middle_y), (right, middle_y),
    ]
    (red, green, blue), count = Counter(_quantized_rgb(image.getpixel(point)) for point in points).most_common(1)[0]
    if count < 3:
        return None
    return Color(r=red / 255, g=green / 255, b=blue / 255, a=1)


def crop_asset(page: CapturedPage, element: ReconstructedElement, asset_id: str, source: Image.Image | None = None) -> Asset:
    source = source.convert("RGBA") if source is not None else Image.open(io.BytesIO(base64.b64decode(page.screenshot_base64))).convert("RGBA")
    scale_x, scale_y = source.width / page.width, source.height / page.height
    left = int(clamp(math.floor(element.x * scale_x), 0, max(0, source.width - 1)))
    top = int(clamp(math.floor(element.y * scale_y), 0, max(0, source.height - 1)))
    width = int(clamp(math.ceil(element.width * scale_x), 1, max(1, source.width - left)))
    height = int(clamp(math.ceil(element.height * scale_y), 1, max(1, source.height - top)))
    cropped = source.crop((left, top, left + width, top + height))
    buffer = io.BytesIO()
    cropped.save(buffer, format="PNG")
    return Asset(id=asset_id, mimeType="image/png", dataBase64=base64.b64encode(buffer.getvalue()).decode("ascii"), width=width, height=height, source="crop")


def normalize_elements(layout: ReconstructedPage, page: CapturedPage) -> list[ReconstructedElement]:
    seen: set[str] = set()
    normalized: list[ReconstructedElement] = []
    id_map: dict[str, str] = {}
    namespace = f"{page.id}-"
    for index, element in enumerate(layout.elements):
        local_id = re.sub(r"[^A-Za-z0-9._:-]", "-", element.id).strip("-") or f"element-{index}"
        # Model-generated IDs are page-local. DesignDocumentV1 IDs are document-global,
        # so namespace them before building nodes and preserve the mapping for parents.
        base_id = f"{namespace}{local_id[:max(1, 247 - len(namespace))]}"
        element_id = base_id
        suffix = 2
        while element_id in seen:
            suffix_text = f"-{suffix}"
            element_id = f"{base_id[:256 - len(suffix_text)]}{suffix_text}"
            suffix += 1
        seen.add(element_id)
        id_map.setdefault(element.id, element_id)
        x, y = clamp(element.x, 0, page.width - 1), clamp(element.y, 0, page.height - 1)
        width = clamp(element.width, 1, page.width - x)
        height = clamp(element.height, 1, page.height - y)
        normalized.append(element.model_copy(update={"id": element_id, "x": x, "y": y, "width": width, "height": height}))
    normalized = [element.model_copy(update={
        "parent_id": id_map.get(element.parent_id)
        if element.parent_id and property_confidence(element, "hierarchy") >= MIN_HIERARCHY_CONFIDENCE
        else None,
    }) for element in normalized]
    element_types = {element.id: element.type for element in normalized}
    parent_by_id = {
        element.id: element.parent_id
        if element.parent_id != element.id and element_types.get(element.parent_id or "") == "group"
        else None
        for element in normalized
    }
    safe: list[ReconstructedElement] = []
    for element in normalized:
        parent_id = parent_by_id[element.id]
        visited = {element.id}
        cursor = parent_id
        cyclic = False
        while cursor:
            if cursor in visited:
                cyclic = True
                break
            visited.add(cursor)
            cursor = parent_by_id.get(cursor)
        safe.append(element.model_copy(update={"parent_id": None if cyclic else parent_id}))
    return safe


def _geometry_contains(parent: ReconstructedElement, child: ReconstructedElement, tolerance: float = 2) -> bool:
    return (
        child.x >= parent.x - tolerance
        and child.y >= parent.y - tolerance
        and child.x + child.width <= parent.x + parent.width + tolerance
        and child.y + child.height <= parent.y + parent.height + tolerance
    )


def infer_geometry_hierarchy(elements: list[ReconstructedElement], page: CapturedPage) -> list[ReconstructedElement]:
    """Infer conservative card/container membership when the model omitted it.

    Existing valid model relationships remain authoritative. Geometry inference is
    restricted to confident, non-page-sized groups containing at least two root
    visual elements, preventing a large decorative rectangle from swallowing the
    entire page tree.
    """
    groups = [
        element for element in elements
        if element.type == "group"
        and property_confidence(element, "type") >= MIN_TYPE_CONFIDENCE
        and property_confidence(element, "geometry") >= MIN_GEOMETRY_CONFIDENCE
        and property_confidence(element, "hierarchy") >= MIN_HIERARCHY_CONFIDENCE
        and element.width * element.height < page.width * page.height * 0.95
    ]
    roots = [element for element in elements if not element.parent_id and element.type != "group"]
    eligible: set[str] = set()
    for group in groups:
        members = [child for child in roots if child.z_index >= group.z_index and _geometry_contains(group, child)]
        if len(members) >= 2:
            eligible.add(group.id)

    inferred: list[ReconstructedElement] = []
    for element in elements:
        if element.parent_id or element.type == "group":
            inferred.append(element)
            continue
        candidates = [
            group for group in groups
            if group.id in eligible and group.z_index <= element.z_index and _geometry_contains(group, element)
        ]
        parent = min(candidates, key=lambda group: (group.width * group.height, group.z_index, group.id), default=None)
        inferred.append(element.model_copy(update={"parent_id": parent.id if parent else None}))
    return inferred


def gradient_transform(angle: float) -> list[list[float]]:
    radians = math.radians(angle % 360)
    cosine, sine = math.cos(radians), math.sin(radians)
    return [
        [cosine, sine, 0.5 - 0.5 * cosine - 0.5 * sine],
        [-sine, cosine, 0.5 + 0.5 * sine - 0.5 * cosine],
    ]


def simple_effects(element: ReconstructedElement) -> list[DropShadowEffect | LayerBlurEffect]:
    effects: list[DropShadowEffect | LayerBlurEffect] = []
    if property_confidence(element, "style") < MIN_STYLE_CONFIDENCE:
        return effects
    if element.shadow_color and element.shadow_blur is not None:
        effects.append(DropShadowEffect(
            color=parse_color(element.shadow_color),
            offsetX=element.shadow_offset_x or 0,
            offsetY=element.shadow_offset_y or 0,
            radius=element.shadow_blur,
            spread=element.shadow_spread or 0,
        ))
    if element.blur_radius:
        effects.append(LayerBlurEffect(radius=element.blur_radius))
    return effects


def unsupported_native_reason(element: ReconstructedElement) -> str | None:
    gradient_values = (element.gradient_start_color, element.gradient_end_color, element.gradient_angle)
    if any(value is not None for value in gradient_values):
        if element.type not in {"rectangle", "ellipse"} or not all(value is not None for value in gradient_values):
            return "Unsupported effect: incomplete or non-shape gradient; preserved screenshot pixels"
    shadow_values = (
        element.shadow_color, element.shadow_offset_x, element.shadow_offset_y,
        element.shadow_blur, element.shadow_spread,
    )
    if any(value is not None for value in shadow_values) and not (element.shadow_color and element.shadow_blur is not None):
        return "Unsupported effect: incomplete shadow evidence; preserved screenshot pixels"
    if element.type == "vector" and element.svg:
        try:
            validate_svg_document(element.svg)
        except ValueError as error:
            return f"Unsupported vector: {error}; preserved screenshot pixels"
    return None


def node_base(element: ReconstructedElement, extraction: str) -> dict:
    return {
        "id": element.id,
        "name": element.name,
        "box": Box(x=element.x, y=element.y, width=element.width, height=element.height),
        "rotation": element.rotation,
        "opacity": element.opacity,
        "visible": True,
        "locked": False,
        "zIndex": element.z_index,
        "confidence": min(
            element.confidence,
            property_confidence(element, "type"),
            property_confidence(element, "geometry"),
        ),
        "typeConfidence": property_confidence(element, "type"),
        "geometryConfidence": property_confidence(element, "geometry"),
        "styleConfidence": property_confidence(element, "style"),
        "hierarchyConfidence": property_confidence(element, "hierarchy"),
        "source": SourceMeta(sourceType=element.type, extraction=extraction),
        "effects": simple_effects(element) or None,
    }


def best_image_hint_match(element: ReconstructedElement, hints: list[DomImageHint]) -> DomImageHint | None:
    scored: list[tuple[float, DomImageHint]] = []
    element_area = element.width * element.height
    element_aspect = element.width / element.height
    for hint in hints:
        intersection = overlap(element, hint)
        if intersection <= 0:
            continue
        hint_area = hint.width * hint.height
        element_coverage = intersection / max(1, element_area)
        hint_coverage = intersection / max(1, hint_area)
        if element_coverage < 0.65 or hint_coverage < 0.5:
            continue
        hint_aspect = hint.width / hint.height
        aspect_score = min(element_aspect, hint_aspect) / max(element_aspect, hint_aspect)
        element_center = (element.x + element.width / 2, element.y + element.height / 2)
        hint_center = (hint.x + hint.width / 2, hint.y + hint.height / 2)
        distance = math.hypot(element_center[0] - hint_center[0], element_center[1] - hint_center[1])
        diagonal = max(1, math.hypot(element.width, element.height))
        center_score = 1 - clamp(distance / diagonal, 0, 1)
        score = element_coverage * 0.45 + hint_coverage * 0.25 + aspect_score * 0.2 + center_score * 0.1
        if score >= 0.72:
            scored.append((score, hint))
    return max(scored, key=lambda item: item[0], default=(0, None))[1]


def original_image_fill(hint: DomImageHint, downloaded: DownloadedCanvaAsset, asset_id: str) -> tuple[ImageFill | None, str | None]:
    centered = abs(hint.object_position_x - 0.5) <= 0.02 and abs(hint.object_position_y - 0.5) <= 0.02
    if hint.object_fit == "contain":
        return ImageFill(assetId=asset_id, scaleMode="fit"), None
    if hint.object_fit == "cover" and centered:
        return ImageFill(assetId=asset_id, scaleMode="fill"), None
    if hint.object_fit == "fill":
        rendered_aspect = hint.width / hint.height
        source_aspect = downloaded.width / downloaded.height
        if min(rendered_aspect, source_aspect) / max(rendered_aspect, source_aspect) >= 0.98:
            return ImageFill(assetId=asset_id, scaleMode="fill"), None
        return None, "Original asset uses unsupported non-uniform stretching; preserved screenshot crop"
    if hint.object_fit == "cover":
        return None, "non-centered-cover"
    return None, f"Unsupported image fit mode ({hint.object_fit}); preserved screenshot crop"


def clipped_original_image_node(
    element: ReconstructedElement,
    hint: DomImageHint,
    downloaded: DownloadedCanvaAsset,
    asset_id: str,
) -> DesignNode:
    scale = max(element.width / downloaded.width, element.height / downloaded.height)
    content_width = downloaded.width * scale
    content_height = downloaded.height * scale
    if content_width > 32768 or content_height > 32768:
        raise ValueError("Non-centered crop geometry exceeds the Design IR node limit.")
    content_x = (element.width - content_width) * hint.object_position_x
    content_y = (element.height - content_height) * hint.object_position_y
    content_id = f"{element.id[:247]}-content"
    content = DesignNode(
        id=content_id,
        type="image",
        name=f"{element.name or 'Image'} · original content",
        box=Box(x=content_x, y=content_y, width=content_width, height=content_height),
        rotation=0,
        opacity=1,
        visible=True,
        locked=False,
        zIndex=0,
        confidence=element.confidence,
        source=SourceMeta(sourceType="image", extraction="asset"),
        fill=ImageFill(assetId=asset_id, scaleMode="fit"),
    )
    base = node_base(element, "asset")
    return DesignNode(
        **base,
        type="group",
        children=[content],
        clipsContent=True,
        cornerRadius=max(element.corner_radius or 0, hint.corner_radius),
    )


def convert_visual_element(
    page: CapturedPage,
    source: Image.Image,
    element: ReconstructedElement,
    assets: dict[str, Asset],
    original_asset_cache: dict[str, DownloadedCanvaAsset | str],
    warnings: list[str],
) -> DesignNode:
    invalid_vector = element.type == "vector" and not element.svg
    unsupported_reason = unsupported_native_reason(element)
    confidence_reason = native_fallback_reason(element)
    sampled_fill = sample_dominant_color(source, page, element) if element.type in {"rectangle", "ellipse"} else None
    has_gradient = bool(element.gradient_start_color and element.gradient_end_color and element.gradient_angle is not None)
    pixel_reason = None
    if element.type in {"rectangle", "ellipse"} and not has_gradient and sampled_fill is None:
        pixel_reason = "Pixels do not support a confident flat native shape"
    fallback = (
        element.confidence < MIN_CONFIDENCE
        or element.type == "unsupported"
        or invalid_vector
        or unsupported_reason is not None
        or confidence_reason is not None
        or pixel_reason is not None
    )
    base = node_base(element, "fallback" if fallback else "vision")
    if fallback:
        asset_id = f"fallback-{page.id}-{element.id}"
        assets[asset_id] = crop_asset(page, element, asset_id, source)
        reason = unsupported_reason or element.reason or confidence_reason or pixel_reason or ("Vector region did not include valid SVG data" if invalid_vector else f"Low confidence ({element.confidence:.2f})")
        if re.search(r"\b(?:mask|clip(?:ping)?)\b", reason, re.I) and not reason.casefold().startswith("unsupported mask:"):
            reason = f"Unsupported mask: {reason}"
        return DesignNode(**base, type="raster-fallback", assetId=asset_id, reason=reason)
    if element.type == "image":
        hint = best_image_hint_match(element, page.image_hints)
        crop_reason = "Screenshot crop: no matching Canva image hint"
        if hint is not None:
            downloaded = original_asset_cache.get(hint.src)
            if downloaded is None:
                try:
                    downloaded = download_canva_asset(hint.src)
                except Exception as error:
                    downloaded = public_error_message(error, "Original Canva asset unavailable")
                original_asset_cache[hint.src] = downloaded
            if isinstance(downloaded, DownloadedCanvaAsset):
                original_id = f"canva-{hashlib.sha256(hint.src.encode('utf-8')).hexdigest()[:32]}"
                fill, unsupported_reason = original_image_fill(hint, downloaded, original_id)
                if fill is not None or unsupported_reason == "non-centered-cover":
                    if original_id not in assets:
                        assets[original_id] = Asset(
                            id=original_id,
                            mimeType=downloaded.mime_type,
                            dataBase64=base64.b64encode(downloaded.data).decode("ascii"),
                            width=downloaded.width,
                            height=downloaded.height,
                            source="canva",
                        )
                if unsupported_reason == "non-centered-cover":
                    try:
                        return clipped_original_image_node(element, hint, downloaded, original_id)
                    except ValueError:
                        unsupported_reason = "Unsupported image crop geometry; preserved screenshot crop"
                if fill is not None:
                    asset_base = node_base(element, "asset")
                    return DesignNode(
                        **asset_base,
                        type="image",
                        fill=fill,
                        cornerRadius=max(element.corner_radius or 0, hint.corner_radius),
                    )
                crop_reason = unsupported_reason or "Unsupported image crop; preserved screenshot crop"
            else:
                crop_reason = "Original Canva asset unavailable; preserved screenshot crop"
                warnings.append(f"{element.id}: {downloaded}")
        asset_id = f"image-{page.id}-{element.id}"
        assets[asset_id] = crop_asset(page, element, asset_id, source)
        crop_base = node_base(element, "fallback")
        return DesignNode(
            **crop_base,
            type="image",
            fill=ImageFill(assetId=asset_id, scaleMode="fill"),
            cornerRadius=max(element.corner_radius or 0, hint.corner_radius if hint else 0),
            reason=crop_reason,
        )
    if element.gradient_start_color and element.gradient_end_color and element.gradient_angle is not None:
        fills = [GradientFill(
            type="linear-gradient",
            stops=[
                GradientStop(position=0, color=parse_color(element.gradient_start_color)),
                GradientStop(position=1, color=parse_color(element.gradient_end_color)),
            ],
            transform=gradient_transform(element.gradient_angle),
        )]
    else:
        fills = [SolidFill(color=sampled_fill or parse_color(element.fill_color or "#ffffff"))]
    stroke = Stroke(color=parse_color(element.stroke_color), weight=element.stroke_weight or 1, align="inside") if element.stroke_color else None
    if element.type == "ellipse":
        return DesignNode(**base, type="ellipse", fills=fills, stroke=stroke)
    if element.type == "vector" and element.svg:
        return DesignNode(**base, type="vector", svg=element.svg)
    if element.type == "group":
        return DesignNode(**base, type="group", children=[], clipsContent=element.clips_content)
    return DesignNode(**base, type="rectangle", fills=fills, stroke=stroke, cornerRadius=element.corner_radius or 0)


def best_model_text_match(line: OcrBlock, candidates: list[ReconstructedElement]) -> ReconstructedElement | None:
    scored: list[tuple[float, ReconstructedElement]] = []
    line_text = normalized_text(line.text)
    for candidate in candidates:
        intersection = overlap(line, candidate)
        if intersection <= 0:
            continue
        containment = intersection / max(1, min(line.width * line.height, candidate.width * candidate.height))
        similarity = SequenceMatcher(None, line_text, normalized_text(candidate.text)).ratio() if candidate.text else 0
        score = containment * 0.7 + similarity * 0.3
        if score >= 0.25:
            scored.append((score, candidate))
    return max(scored, key=lambda item: (item[0], item[1].confidence, -item[1].z_index), default=(0, None))[1]


def sort_node_tree(nodes: list[DesignNode]) -> None:
    nodes.sort(key=lambda node: (node.z_index, node.id))
    for node in nodes:
        if node.children:
            sort_node_tree(node.children)


def text_accuracy(reference: str, nodes: list[DesignNode]) -> float:
    reconstructed: list[str] = []

    def walk(items: list[DesignNode]) -> None:
        for node in items:
            if node.type == "text" and node.text:
                reconstructed.append(node.text)
            walk(node.children or [])

    walk(nodes)
    expected = normalized_text(reference)
    actual = normalized_text(" ".join(reconstructed))
    if not expected and not actual:
        return 1
    if not expected or not actual:
        return 0
    return SequenceMatcher(None, expected, actual).ratio()


def rectangle_union_area(rectangles: list[tuple[float, float, float, float]]) -> float:
    valid = [(left, top, right, bottom) for left, top, right, bottom in rectangles if right > left and bottom > top]
    x_points = sorted({point for rectangle in valid for point in (rectangle[0], rectangle[2])})
    total = 0.0
    for left, right in zip(x_points, x_points[1:]):
        intervals = sorted((top, bottom) for x1, top, x2, bottom in valid if x1 < right and x2 > left)
        covered = 0.0
        cursor_top = cursor_bottom = None
        for top, bottom in intervals:
            if cursor_top is None:
                cursor_top, cursor_bottom = top, bottom
            elif top <= cursor_bottom:
                cursor_bottom = max(cursor_bottom, bottom)
            else:
                covered += cursor_bottom - cursor_top
                cursor_top, cursor_bottom = top, bottom
        if cursor_top is not None:
            covered += cursor_bottom - cursor_top
        total += (right - left) * covered
    return total


def fallback_coverage(nodes: list[DesignNode], page: CapturedPage) -> float:
    rectangles: list[tuple[float, float, float, float]] = []

    def walk(items: list[DesignNode], offset_x: float = 0, offset_y: float = 0) -> None:
        for node in items:
            absolute_x = offset_x + node.box.x
            absolute_y = offset_y + node.box.y
            if node.type == "raster-fallback":
                rectangles.append((
                    clamp(absolute_x, 0, page.width),
                    clamp(absolute_y, 0, page.height),
                    clamp(absolute_x + node.box.width, 0, page.width),
                    clamp(absolute_y + node.box.height, 0, page.height),
                ))
            walk(node.children or [], absolute_x, absolute_y)

    walk(nodes)
    return clamp(rectangle_union_area(rectangles) / (page.width * page.height), 0, 1)


def reconstruct_page(
    page: CapturedPage,
    ocr_provider: OcrProvider,
    layout_provider: LayoutProvider,
    assets: dict[str, Asset],
    original_asset_cache: dict[str, DownloadedCanvaAsset | str] | None = None,
) -> DesignPage:
    original_asset_cache = original_asset_cache if original_asset_cache is not None else {}
    source = Image.open(io.BytesIO(base64.b64decode(page.screenshot_base64))).convert("RGBA")
    raw_ocr = ocr_provider.detect(page.screenshot_base64, source.width, source.height)
    scale_x, scale_y = page.width / source.width, page.height / source.height
    ocr = OcrResult(
        fullText=raw_ocr.full_text,
        blocks=deduplicate_ocr_blocks([
            block.model_copy(update={"x": block.x * scale_x, "y": block.y * scale_y, "width": block.width * scale_x, "height": block.height * scale_y})
            for block in raw_ocr.blocks
        ]),
    )
    pipeline_warnings: list[str] = []
    try:
        # Signed Canva asset URLs are backend-only recovery evidence. The
        # layout model needs geometry/style hints, not expiring URL tokens.
        layout_image_hints = [
            {key: value for key, value in hint.json_dict().items() if key != "src"}
            for hint in page.image_hints
        ]
        layout = layout_provider.analyze(page.screenshot_base64, page.width, page.height, ocr, [hint.json_dict() for hint in page.text_hints], layout_image_hints)
    except Exception as error:
        safe_error = public_error_message(error, "Layout analysis failed")
        pipeline_warnings.append(f"Layout analysis failed; preserved the page as a raster fallback: {safe_error}")
        layout = ReconstructedPage(backgroundColor="#ffffff", elements=[ReconstructedElement(id="page-fallback", type="unsupported", x=0, y=0, width=page.width, height=page.height, rotation=0, opacity=1, zIndex=0, confidence=0, reason=f"Layout analysis failed: {safe_error}")])
    layout = normalize_layout_coordinates(layout, page, source)
    layout = layout.model_copy(update={"elements": deduplicate_layout_elements(layout.elements)})
    elements = infer_geometry_hierarchy(normalize_elements(layout, page), page)
    visual_elements = sorted((element for element in elements if element.type != "text"), key=lambda item: item.z_index)
    pairs = [(
        element,
        convert_visual_element(page, source, element, assets, original_asset_cache, pipeline_warnings),
    ) for element in visual_elements]
    raster_owned = [
        element for element, node in pairs
        if element.type == "image" or node.type == "raster-fallback"
    ]
    by_id = {element.id: node for element, node in pairs}
    roots: list[DesignNode] = []
    for element, node in pairs:
        parent = by_id.get(element.parent_id or "")
        if parent and parent.type == "group" and parent.id != node.id:
            node.box.x -= parent.box.x
            node.box.y -= parent.box.y
            parent.children.append(node)
        else:
            roots.append(node)

    model_text = [element for element in elements if element.type == "text"]
    base_z = max([element.z_index for element in elements] + [0]) + 1
    suppressed_ocr = 0
    for index, line in enumerate(group_ocr_into_lines(ocr.blocks)):
        hint = closest_text_hint(line, page.text_hints)
        matching = best_model_text_match(line, model_text)
        if not matching and any(overlap(line, owner) / max(1, line.width * line.height) >= 0.65 for owner in raster_owned):
            suppressed_ocr += 1
            continue
        font_size, line_height, letter_spacing = text_metrics(line, hint)
        if matching:
            text_effect_reason = unsupported_native_reason(matching)
            if text_effect_reason:
                pipeline_warnings.append(f"{matching.id}: {text_effect_reason}; text remains editable without that effect")
        text_node = DesignNode(
            id=f"{page.id}-{line.id}", type="text", name=f"Text: {line.text[:40]}",
            box=Box(x=line.x, y=line.y, width=max(line.width, 1), height=max(line.height, line_height)),
            rotation=matching.rotation if matching else 0, opacity=matching.opacity if matching else 1, visible=True, locked=False,
            zIndex=matching.z_index if matching else base_z + index,
            confidence=min(line.confidence, matching.confidence) if matching else line.confidence,
            source=SourceMeta(sourceType="text", extraction="ocr"), text=line.text,
            effects=simple_effects(matching) if matching and not unsupported_native_reason(matching) else None,
            runs=[TextRun(
                start=0,
                end=len(line.text),
                fontFamily=normalize_font_family(hint.font_family if hint else None),
                fontStyle=figma_font_style(hint),
                fontSize=font_size,
                color=parse_color(hint.color if hint else "#000000"),
                letterSpacing=letter_spacing,
                lineHeight=line_height,
                textDecoration=text_decoration(hint),
            )],
            horizontalAlign=(hint.text_align if hint and hint.text_align in {"left", "center", "right", "justify"} else "left"), verticalAlign="top",
        )
        parent = by_id.get(matching.parent_id or "") if matching else None
        if parent and parent.type == "group":
            text_node.box.x -= parent.box.x
            text_node.box.y -= parent.box.y
            parent.children.append(text_node)
        else:
            roots.append(text_node)

    sort_node_tree(roots)

    qa_id = f"qa-{page.id}"
    assets[qa_id] = Asset(id=qa_id, mimeType="image/png", dataBase64=page.screenshot_base64, width=page.width, height=page.height, source="reference")
    ratio = fallback_coverage(roots, page)
    fallback_count = 0
    stack = list(roots)
    while stack:
        node = stack.pop()
        fallback_count += int(node.type == "raster-fallback")
        stack.extend(node.children or [])
    if fallback_count:
        pipeline_warnings.append(f"{fallback_count} raster fallback region(s)")
    if suppressed_ocr:
        pipeline_warnings.append(f"Suppressed {suppressed_ocr} OCR line(s) already owned by raster regions")
    sampled_background = sample_background_color(source, page)
    result = DesignPage(
        id=page.id, name=f"Canva Page {page.index + 1}", width=page.width, height=page.height,
        background=SolidFill(color=sampled_background or parse_color(layout.background_color)),
        children=roots, qaReferenceAssetId=qa_id,
        metrics=ConversionMetrics(
            nativeCoverage=1-ratio,
            fallbackCoverage=ratio,
            exactTextRate=text_accuracy(ocr.full_text, roots),
            missingFonts=[],
            warnings=pipeline_warnings,
        ),
    )
    quality = evaluate_page_quality(result, assets, source)
    quality_warnings: list[str] = []
    if result.metrics.exact_text_rate < 0.95:
        quality_warnings.append(f"Text accuracy below target: {result.metrics.exact_text_rate:.1%}")
    if result.metrics.native_coverage < 0.80:
        quality_warnings.append(f"Native-layer coverage below target: {result.metrics.native_coverage:.1%}")
    if quality["visualSimilarity"] < 0.95:
        quality_warnings.append(f"Visual similarity below target: {quality['visualSimilarity']:.1%}")
    if quality["missingRegionRate"] > 0:
        quality_warnings.append(f"Potential missing visible region: {quality['missingRegionRate']:.1%}")
    if quality["duplicateTextBlocks"]:
        quality_warnings.append(f"Duplicate visible text blocks: {quality['duplicateTextBlocks']}")
    return result.model_copy(update={"metrics": result.metrics.model_copy(update={
        "visual_similarity": quality["visualSimilarity"],
        "pixel_difference": quality["pixelDifference"],
        "missing_region_rate": quality["missingRegionRate"],
        "duplicate_text_blocks": quality["duplicateTextBlocks"],
        "warnings": [*result.metrics.warnings, *quality_warnings],
    })})


def fallback_page(page: CapturedPage, message: str, assets: dict[str, Asset]) -> DesignPage:
    qa_id, fallback_id = f"qa-{page.id}", f"fallback-{page.id}"
    assets[qa_id] = Asset(id=qa_id, mimeType="image/png", dataBase64=page.screenshot_base64, width=page.width, height=page.height, source="reference")
    assets[fallback_id] = Asset(id=fallback_id, mimeType="image/png", dataBase64=page.screenshot_base64, width=page.width, height=page.height, source="crop")
    node = DesignNode(id=f"{page.id}-fallback", type="raster-fallback", name="Page fallback", box=Box(x=0, y=0, width=page.width, height=page.height), rotation=0, opacity=1, visible=True, locked=False, zIndex=0, confidence=0, source=SourceMeta(sourceType="page", extraction="fallback"), assetId=fallback_id, reason=message)
    return DesignPage(id=page.id, name=f"Canva Page {page.index + 1}", width=page.width, height=page.height, background=SolidFill(color=parse_color("#ffffff")), children=[node], qaReferenceAssetId=qa_id, metrics=ConversionMetrics(nativeCoverage=0, fallbackCoverage=1, exactTextRate=0, visualSimilarity=1, pixelDifference=0, missingRegionRate=0, duplicateTextBlocks=0, missingFonts=[], warnings=[message]))


def reconstruct_document(capture: CaptureRecord, page_ids: list[str], ocr_provider: OcrProvider, layout_provider: LayoutProvider, on_progress: Progress, is_cancelled: Cancelled) -> DesignDocumentV1:
    selected = [next((page for page in capture.pages if page.id == page_id), None) for page_id in page_ids]
    selected = [page for page in selected if page is not None]
    if not selected:
        raise RuntimeError("No valid captured pages were selected.")
    assets: dict[str, Asset] = {}
    original_asset_cache: dict[str, DownloadedCanvaAsset | str] = {}
    pages: list[DesignPage] = []
    for index, page in enumerate(selected):
        if is_cancelled():
            raise RuntimeError("Job cancelled.")
        on_progress(index, f"Analyzing page {index + 1} of {len(selected)}")
        try:
            pages.append(reconstruct_page(page, ocr_provider, layout_provider, assets, original_asset_cache))
        except Exception as error:
            pages.append(fallback_page(page, public_error_message(error, "Page reconstruction failed"), assets))
        on_progress(index + 1, f"Completed page {index + 1} of {len(selected)}")
    return DesignDocumentV1(schemaVersion=1, id=new_id(), title=capture.title, sourceUrl=capture.source_url, createdAt=utc_now(), pages=pages, assets=assets)
