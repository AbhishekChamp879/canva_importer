from __future__ import annotations

import base64
import io
from typing import Iterable

from PIL import Image, ImageChops, ImageDraw, ImageOps, ImageStat

from .models import Asset, DesignNode, DesignPage, GradientFill, ImageFill, SolidFill


MAX_QA_DIMENSION = 768


def _rgba(color, opacity: float = 1) -> tuple[int, int, int, int]:
    return (
        round(color.r * 255), round(color.g * 255), round(color.b * 255),
        round(color.a * opacity * 255),
    )


def _asset_image(asset: Asset | None) -> Image.Image | None:
    if not asset or not asset.data_base64:
        return None
    try:
        return Image.open(io.BytesIO(base64.b64decode(asset.data_base64, validate=True))).convert("RGBA")
    except Exception:
        return None


def _render_image_fill(image: Image.Image, size: tuple[int, int], fill: ImageFill) -> Image.Image | None:
    if fill.transform is not None:
        # Final-Figma QA evaluates affine crop transforms. Excluding them from
        # the backend approximation is safer than reporting a stretched match.
        return None
    if fill.scale_mode in {"fill", "crop"}:
        return ImageOps.fit(image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    if fill.scale_mode == "fit":
        contained = ImageOps.contain(image, size, method=Image.Resampling.LANCZOS)
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        layer.alpha_composite(contained, ((size[0] - contained.width) // 2, (size[1] - contained.height) // 2))
        return layer
    if fill.scale_mode == "tile":
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        for left in range(0, size[0], image.width):
            for top in range(0, size[1], image.height):
                layer.alpha_composite(image, (left, top))
        return layer
    return None


def _count_mask(mask: Image.Image) -> int:
    histogram = mask.histogram()
    return sum(histogram[1:])


def _node_bounds(node: DesignNode, offset_x: float, offset_y: float, scale: float) -> tuple[int, int, int, int]:
    left = round((offset_x + node.box.x) * scale)
    top = round((offset_y + node.box.y) * scale)
    right = max(left + 1, round((offset_x + node.box.x + node.box.width) * scale))
    bottom = max(top + 1, round((offset_y + node.box.y + node.box.height) * scale))
    return left, top, right, bottom


def _paste_layer(canvas: Image.Image, layer: Image.Image, left: int, top: int, opacity: float, rotation: float) -> None:
    if opacity < 1:
        alpha = layer.getchannel("A").point(lambda value: round(value * opacity))
        layer.putalpha(alpha)
    if rotation:
        center_x, center_y = left + layer.width / 2, top + layer.height / 2
        layer = layer.rotate(-rotation, expand=True, resample=Image.Resampling.BICUBIC)
        left, top = round(center_x - layer.width / 2), round(center_y - layer.height / 2)
    canvas.alpha_composite(layer, (left, top))


def _intersect_bounds(
    first: tuple[float, float, float, float] | None,
    second: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    if first is None:
        return second
    return (
        max(first[0], second[0]),
        max(first[1], second[1]),
        min(first[2], second[2]),
        min(first[3], second[3]),
    )


def _walk_nodes(
    nodes: Iterable[DesignNode],
    offset_x: float = 0,
    offset_y: float = 0,
    clip: tuple[float, float, float, float] | None = None,
):
    for node in sorted(nodes, key=lambda item: (item.z_index, item.id)):
        absolute_x, absolute_y = offset_x + node.box.x, offset_y + node.box.y
        yield node, offset_x, offset_y, clip
        if node.children:
            child_clip = clip
            if node.type == "group" and node.clips_content:
                child_clip = _intersect_bounds(clip, (
                    absolute_x,
                    absolute_y,
                    absolute_x + node.box.width,
                    absolute_y + node.box.height,
                ))
            yield from _walk_nodes(node.children, absolute_x, absolute_y, child_clip)


def _duplicate_text_count(page: DesignPage) -> int:
    text_nodes: list[tuple[str, tuple[float, float, float, float]]] = []
    for node, offset_x, offset_y, _clip in _walk_nodes(page.children):
        text = " ".join((node.text or "").casefold().split())
        if node.type == "text" and text:
            text_nodes.append((text, (offset_x + node.box.x, offset_y + node.box.y, node.box.width, node.box.height)))
    duplicates = 0
    for index, (text, first) in enumerate(text_nodes):
        x1, y1, w1, h1 = first
        for other_text, second in text_nodes[:index]:
            if text != other_text:
                continue
            x2, y2, w2, h2 = second
            intersection = max(0, min(x1 + w1, x2 + w2) - max(x1, x2)) * max(0, min(y1 + h1, y2 + h2) - max(y1, y2))
            union = w1 * h1 + w2 * h2 - intersection
            if union and intersection / union >= 0.72:
                duplicates += 1
                break
    return duplicates


def evaluate_page_quality(page: DesignPage, assets: dict[str, Asset], reference: Image.Image) -> dict[str, float | int]:
    """Render supported IR regions and compare them with the captured Canva reference.

    Text and SVG pixels are excluded from pixel comparison because the backend cannot
    reproduce Figma's font/vector renderer exactly; they remain part of coverage and
    are measured separately through exactTextRate and duplicateTextBlocks.
    """
    scale = min(1.0, MAX_QA_DIMENSION / max(page.width, page.height))
    size = (max(1, round(page.width * scale)), max(1, round(page.height * scale)))
    reference = reference.convert("RGB").resize(size, Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", size, (255, 255, 255, 255))
    background_is_image = isinstance(page.background, ImageFill)
    if isinstance(page.background, SolidFill):
        canvas.paste(_rgba(page.background.color), (0, 0, *size))
    elif isinstance(page.background, ImageFill):
        image = _asset_image(assets.get(page.background.asset_id))
        if image:
            rendered = _render_image_fill(image, size, page.background)
            if rendered:
                canvas.alpha_composite(rendered)

    background_render = canvas.convert("RGB").copy()
    coverage = Image.new("L", size, 255 if background_is_image else 0)
    compare_mask = Image.new("L", size, 255)
    mask_draw = ImageDraw.Draw(coverage)
    compare_draw = ImageDraw.Draw(compare_mask)

    for node, offset_x, offset_y, clip in _walk_nodes(page.children):
        if not node.visible or node.opacity <= 0 or node.type == "group":
            continue
        left, top, right, bottom = _node_bounds(node, offset_x, offset_y, scale)
        if clip is not None:
            clip_pixels = (
                round(clip[0] * scale), round(clip[1] * scale),
                round(clip[2] * scale), round(clip[3] * scale),
            )
            visible_left, visible_top, visible_right, visible_bottom = _intersect_bounds(
                (left, top, right, bottom), clip_pixels,
            )
            if visible_right <= visible_left or visible_bottom <= visible_top:
                continue
        else:
            clip_pixels = None
            visible_left, visible_top, visible_right, visible_bottom = left, top, right, bottom
        width, height = right - left, bottom - top
        mask_draw.rectangle((visible_left, visible_top, visible_right, visible_bottom), fill=255)
        if node.type in {"text", "vector"}:
            compare_draw.rectangle((visible_left, visible_top, visible_right, visible_bottom), fill=0)
            continue

        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        if node.type in {"image", "raster-fallback"}:
            asset_id = node.asset_id if node.type == "raster-fallback" else node.fill.asset_id if node.fill else None
            image = _asset_image(assets.get(asset_id or ""))
            if image:
                rendered = image.resize((width, height), Image.Resampling.LANCZOS) if node.type == "raster-fallback" else _render_image_fill(image, (width, height), node.fill)
                if rendered:
                    layer.alpha_composite(rendered)
                else:
                    compare_draw.rectangle((visible_left, visible_top, visible_right, visible_bottom), fill=0)
                    continue
            else:
                compare_draw.rectangle((visible_left, visible_top, visible_right, visible_bottom), fill=0)
                continue
        elif node.type in {"rectangle", "ellipse"}:
            solid = next((fill for fill in node.fills or [] if isinstance(fill, SolidFill)), None)
            gradient = next((fill for fill in node.fills or [] if isinstance(fill, GradientFill)), None)
            if gradient:
                # Figma export QA is authoritative for transformed gradients.
                # Keep the region in native coverage without pretending the
                # Pillow approximation can reproduce Figma gradient handles.
                compare_draw.rectangle((visible_left, visible_top, visible_right, visible_bottom), fill=0)
            fill = _rgba(solid.color) if solid else (0, 0, 0, 0)
            stroke = _rgba(node.stroke.color) if node.stroke else None
            stroke_width = max(1, round(node.stroke.weight * scale)) if node.stroke else 1
            bounds = (0, 0, width - 1, height - 1)
            if node.type == "ellipse":
                draw.ellipse(bounds, fill=fill, outline=stroke, width=stroke_width)
            else:
                radius = max(0, round((node.corner_radius or 0) * scale))
                draw.rounded_rectangle(bounds, radius=radius, fill=fill, outline=stroke, width=stroke_width)
        else:
            compare_draw.rectangle((visible_left, visible_top, visible_right, visible_bottom), fill=0)
            continue
        overlay = Image.new("RGBA", size, (0, 0, 0, 0))
        _paste_layer(overlay, layer, left, top, node.opacity, node.rotation)
        if clip_pixels is not None:
            clip_mask = Image.new("L", size, 0)
            ImageDraw.Draw(clip_mask).rectangle(clip_pixels, fill=255)
            overlay.putalpha(ImageChops.multiply(overlay.getchannel("A"), clip_mask))
        canvas.alpha_composite(overlay)

    difference = ImageChops.difference(reference, canvas.convert("RGB")).convert("L")
    comparable = _count_mask(compare_mask)
    pixel_difference = 0.0 if comparable == 0 else ImageStat.Stat(difference, mask=compare_mask).mean[0] / 255

    foreground_delta = ImageChops.difference(reference, background_render).convert("L")
    foreground = foreground_delta.point(lambda value: 255 if value >= 18 else 0)
    missing = ImageChops.multiply(foreground, ImageChops.invert(coverage))
    foreground_pixels = _count_mask(foreground)
    missing_rate = _count_mask(missing) / foreground_pixels if foreground_pixels else 0

    return {
        "visualSimilarity": max(0, min(1, 1 - pixel_difference)),
        "pixelDifference": max(0, min(1, pixel_difference)),
        "missingRegionRate": max(0, min(1, missing_rate)),
        "duplicateTextBlocks": _duplicate_text_count(page),
    }
