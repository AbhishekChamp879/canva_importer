"""PDFium runs only in this killable process. Never execute document actions."""
from __future__ import annotations

import ctypes as ct
from contextlib import closing
import json
import math
import re
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageStat
import pypdfium2 as pdf
import pypdfium2.raw as raw

from .editable_models import EditableScene


def color(obj, stroke=False):
    channels = [ct.c_uint() for _ in range(4)]
    method = raw.FPDFPageObj_GetStrokeColor if stroke else raw.FPDFPageObj_GetFillColor
    if not method(obj, *channels):
        return None
    return [c.value / 255 for c in channels]


def has_clip(obj):
    clip = raw.FPDFPageObj_GetClipPath(obj)
    return bool(clip and raw.FPDFClipPath_CountPaths(clip) > 0)


def convert(source: Path, target: Path, width: int, height: int):
    assets, fonts, nodes, warnings = [], {}, [], []
    total_bytes = 0

    def save(image, identifier):
        nonlocal total_bytes
        path = target / f"{identifier}.png"
        image.save(path, format="PNG")
        size = path.stat().st_size
        total_bytes += size
        if size > 25 * 1024 * 1024 or total_bytes > 512 * 1024 * 1024:
            raise ValueError("PDF image assets exceed the supported byte limit")
        assets.append({"id": identifier, "width": image.width, "height": image.height})
        return identifier

    with pdf.PdfDocument(source) as document:
        if len(document) != 1:
            raise ValueError("Expected exactly one exported PDF page; reload the Canva pages")
        with closing(document[0]) as page:
            pw, ph = page.get_size()
            if not pw or not ph or abs((width / height) / (pw / ph) - 1) > 0.02:
                raise ValueError("PDF dimensions changed; reload the Canva design before converting")
            scale = min(width / pw * 2, height / ph * 2, math.sqrt(32_000_000 / (pw * ph)), 8192 / max(pw, ph))
            if scale <= 0:
                raise ValueError("Invalid PDF page dimensions")

            def render():
                with closing(page.render(scale=scale, fill_color=(0, 0, 0, 0), may_draw_forms=False)) as bitmap:
                    return bitmap.to_pil().convert("RGBA").copy()

            reference = render()
            save(reference, "reference")
            sx, sy = width / reference.width, height / reference.height
            left, bottom, right, top = page.get_bbox()
            rotate = page.get_rotation()

            def point(x, y):
                # PDF box coordinates to the rotated top-left page coordinate system.
                u, v = x - left, top - y
                bw, bh = right - left, top - bottom
                if rotate == 90:
                    u, v = bh - v, u
                elif rotate == 180:
                    u, v = bw - u, bh - v
                elif rotate == 270:
                    u, v = v, bw - u
                return u * width / pw, v * height / ph

            with closing(page.get_textpage()) as textpage:
                leaves, switches = [], []
                def collect(container=None, ancestors=(), parent_matrix=None, depth=0):
                    if depth > 32:
                        raise ValueError("PDF nesting exceeds 32 levels")
                    parent_matrix = parent_matrix or pdf.PdfMatrix()
                    for obj in page.get_objects(max_depth=1, form=container, textpage=textpage):
                        if len(switches) >= 10000:
                            raise ValueError("PDF exceeds 10,000 objects")
                        switches.append(obj)
                        if obj.type == raw.FPDF_PAGEOBJ_FORM and not has_clip(obj) and not raw.FPDFPageObj_HasTransparency(obj):
                            collect(obj, (*ancestors, obj), obj.get_matrix().multiply(parent_matrix), depth + 1)
                        else:
                            leaves.append((obj, ancestors, obj.get_matrix().multiply(parent_matrix)))
                collect()
                for obj in switches:
                    if not raw.FPDFPageObj_SetIsActive(obj, False):
                        raise ValueError("PDF object isolation is unavailable")
                composite = Image.new("RGBA", reference.size)
                for index, (obj, ancestors, matrix) in enumerate(leaves):
                    for active in (*ancestors, obj):
                        raw.FPDFPageObj_SetIsActive(active, True)
                    isolated = render()
                    for active in reversed((*ancestors, obj)):
                        raw.FPDFPageObj_SetIsActive(active, False)
                    composite = Image.alpha_composite(composite, isolated)
                    box = isolated.getchannel("A").getbbox()
                    if box is None:
                        continue
                    x0, y0, x1, y1 = box
                    aid = save(isolated.crop(box), f"object-{index}")
                    base = {"id": f"node-{index}", "name": f"PDF object {index + 1}", "x": x0 * sx, "y": y0 * sy,
                            "width": (x1 - x0) * sx, "height": (y1 - y0) * sy}
                    node = {**base, "type": "image", "assetId": aid}
                    reason = "Unsupported PDF object preserved as an image"
                    clipped = has_clip(obj)
                    if obj.type == raw.FPDF_PAGEOBJ_IMAGE:
                        reason = None
                        node["name"] = "PDF image"
                    elif obj.type == raw.FPDF_PAGEOBJ_TEXT:
                        value = obj.extract().strip("\x00")[:10000]
                        font = obj.get_font()
                        original = font.get_base_name()[:256]
                        family = re.sub(r"^[A-Z]{6}\+", "", font.get_family_name() or original)[:256]
                        weight = font.get_weight() or 400
                        italic = bool(re.search("italic|oblique", original, re.I))
                        weights = {100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium", 600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black"}
                        style = weights[min(weights, key=lambda w: abs(w - weight))] + (" Italic" if italic else "")
                        style = style.replace("Regular Italic", "Italic")
                        key = (family, original, style)
                        if key not in fonts:
                            fonts[key] = {"id": f"font-{len(fonts)}", "family": family, "originalName": original,
                                          "style": style, "weight": max(1, min(1000, weight)), "sample": value[:4096], "cropAsset": aid}
                        # Shaping/rotation/clip effects must not be guessed by the native renderer.
                        simple = (value and not clipped and rotate == 0 and not matrix.b and not matrix.c
                                  and matrix.a > 0 and matrix.d > 0 and abs(matrix.a - matrix.d) < 0.001
                                  and all(32 <= ord(c) < 0x300 for c in value)
                                  and raw.FPDFTextObj_GetTextRenderMode(obj) == raw.FPDF_TEXTRENDERMODE_FILL)
                        reason = "Text shaping, transform or clipping preserved as an image"
                        node.update(text=value or None, fontId=fonts[key]["id"])
                        if simple and color(obj):
                            node = {**base, "type": "text", "name": "Text: " + value[:80], "text": value,
                                    "fontId": fonts[key]["id"], "fontSize": obj.get_font_size() * matrix.d * height / ph,
                                    "baseline": point(matrix.e, matrix.f)[1], "fill": color(obj), "fallbackAsset": aid}
                            reason = None
                    elif obj.type == raw.FPDF_PAGEOBJ_PATH and not clipped:
                        try:
                            fillmode, stroked = ct.c_int(), ct.c_int()
                            if not raw.FPDFPath_GetDrawMode(obj, fillmode, stroked):
                                raise ValueError("Unknown path mode")
                            count = raw.FPDFPath_CountSegments(obj)
                            if count > 50000:
                                raise ValueError("Path is too complex")
                            commands, curve = [], []
                            for j in range(count):
                                segment = raw.FPDFPath_GetPathSegment(obj, j)
                                px, py = ct.c_float(), ct.c_float()
                                if not raw.FPDFPathSegment_GetPoint(segment, px, py):
                                    raise ValueError("Invalid path segment")
                                xx, yy = point(*matrix.on_point(px.value, py.value))
                                kind = raw.FPDFPathSegment_GetType(segment)
                                coordinates = f"{xx:.5f} {yy:.5f}"
                                if kind == raw.FPDF_SEGMENT_BEZIERTO:
                                    curve.append(coordinates)
                                    if len(curve) == 3:
                                        commands.append("C " + " ".join(curve)); curve.clear()
                                else:
                                    if curve or kind not in (raw.FPDF_SEGMENT_MOVETO, raw.FPDF_SEGMENT_LINETO):
                                        raise ValueError("Unsupported path segment")
                                    commands.append(("M " if kind == raw.FPDF_SEGMENT_MOVETO else "L ") + coordinates)
                                if raw.FPDFPathSegment_GetClose(segment):
                                    commands.append("Z")
                            stroke_width = ct.c_float()
                            raw.FPDFPageObj_GetStrokeWidth(obj, stroke_width)
                            # Complex stroke styles need the rendered appearance, not an approximation.
                            stroke_ok = (not stroked.value or (raw.FPDFPageObj_GetDashCount(obj) == 0
                                         and raw.FPDFPageObj_GetLineCap(obj) == 0 and raw.FPDFPageObj_GetLineJoin(obj) == 0
                                         and abs(math.hypot(matrix.a, matrix.b) - math.hypot(matrix.c, matrix.d)) < 0.001))
                            if curve or not commands or not stroke_ok:
                                raise ValueError("Unsupported stroke")
                            node = {**base, "type": "vector", "name": "PDF vector", "path": " ".join(commands),
                                    "fill": color(obj) if fillmode.value else None, "stroke": color(obj, True) if stroked.value else None,
                                    "strokeWidth": stroke_width.value * math.hypot(matrix.a, matrix.b) * width / pw,
                                    "fillRule": "EVENODD" if fillmode.value == raw.FPDF_FILLMODE_ALTERNATE else "NONZERO", "fallbackAsset": aid}
                            reason = None
                        except (ValueError, AttributeError):
                            reason = "Complex PDF path preserved as an image"
                    if reason:
                        node["fallbackReason"] = reason
                        warnings.append(f"{base['id']}: {reason}")
                    nodes.append(node)
                # Detect backdrop-dependent blending and transparency groups. Their separate
                # renderings cannot safely be composited with other editable layers.
                for obj in switches:
                    raw.FPDFPageObj_SetIsActive(obj, True)
                white = Image.new("RGBA", reference.size, "white")
                diff = ImageChops.difference(Image.alpha_composite(white, reference).convert("RGB"),
                                            Image.alpha_composite(white, composite).convert("RGB"))
                mismatch = max(ImageStat.Stat(diff).mean) > 0.7 or sum(diff.convert("L").point(lambda v: 255 if v > 12 else 0).histogram()[128:]) / (reference.width * reference.height) > 0.003
                if mismatch:
                    nodes = [{"id": "page-fallback", "type": "image", "name": "PDF page image (fallback)", "x": 0, "y": 0,
                              "width": width, "height": height, "assetId": "reference", "fallbackReason": "PDF effects depend on surrounding content"}]
                    warnings = ["This page requires whole-page image import because its effects cannot be safely separated."]
                whole = mismatch or bool(nodes) and not any(n["type"] in {"text", "vector"} for n in nodes)
                if whole and not mismatch:
                    warnings.append("No editable text or vectors were recoverable; image import remains available.")
                scene = EditableScene(width=width, height=height, nodes=nodes, fonts=list(fonts.values()), assets=assets,
                                      referenceAsset="reference", warnings=warnings, wholePageFallback=whole)
                (target / "scene.json").write_text(json.dumps(scene.json_dict()), encoding="utf-8")


def main():
    target = Path(sys.argv[2])
    try:
        convert(Path(sys.argv[1]), target, int(sys.argv[3]), int(sys.argv[4]))
    except Exception:
        # Native parsing details may contain PDF strings. Keep diagnostics bounded and private.
        (target / "worker-error.json").write_text(json.dumps({"message": "PDF conversion failed: invalid, encrypted, changed-size or over-complex PDF. Reload pages or use image import."}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
