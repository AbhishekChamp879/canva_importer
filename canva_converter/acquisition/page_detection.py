from __future__ import annotations

import hashlib
import math
from typing import Any


def score_page_candidate(candidate: dict[str, Any], viewport: dict[str, float]) -> float:
    try:
        x, y = float(candidate["x"]), float(candidate["y"])
        width, height = float(candidate["width"]), float(candidate["height"])
        vw, vh = float(viewport["width"]), float(viewport["height"])
    except (KeyError, TypeError, ValueError):
        return float("-inf")
    if not all(math.isfinite(value) for value in (x, y, width, height)):
        return float("-inf")
    if width < 200 or height < 200 or x >= vw or y >= vh or x + width <= 0 or y + height <= 0:
        return float("-inf")
    if width > vw * 1.05 or height > vh * 1.1:
        return float("-inf")
    aspect = width / height
    if aspect < 0.15 or aspect > 6.5:
        return float("-inf")
    coverage = width * height / (vw * vh)
    if coverage >= 0.9:
        return float("-inf")
    label = " ".join(str(candidate.get(key, "")) for key in ("role", "ariaLabel", "className", "dataPageId", "dataPageIndex", "dataPageNumber")).lower()
    score = math.log2(width * height)
    if any(candidate.get(key) is not None for key in ("dataPageId", "dataPageIndex", "dataPageNumber")):
        score += 12
    if str(candidate.get("tagName", "")).lower() == "canvas":
        score += 5
    if str(candidate.get("role", "")).lower() == "img":
        score += 4
    if any(word in label for word in ("page", "slide", "design", "canvas")):
        score += 3
    if any(word in label for word in ("toolbar", "navigation", "thumbnail", "button", "menu", "dialog")):
        score -= 8
    if any(word in label for word in ("viewer", "shell")) and not any(candidate.get(key) is not None for key in ("dataPageId", "dataPageIndex", "dataPageNumber")):
        score -= 5
    if coverage >= 0.12:
        score += 3
    if y < -20:
        score -= 4
    return score


def select_best_page_candidate(candidates: list[dict[str, Any]], viewport: dict[str, float]) -> dict[str, Any] | None:
    scored = [{**candidate, "score": score_page_candidate(candidate, viewport)} for candidate in candidates]
    scored = [candidate for candidate in scored if math.isfinite(candidate["score"])]
    scored.sort(key=lambda item: (-item["score"], -(item["width"] * item["height"]), str(item.get("id", ""))))
    return scored[0] if scored else None


def screenshot_fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
