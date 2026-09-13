"""Bounded wire contracts for deterministic PDF conversion (no executable content)."""
from __future__ import annotations

from typing import Literal
from pydantic import Field, model_validator
from .models import ApiModel


class EditableRequest(ApiModel):
    captureId: str
    pageId: str


class Asset(ApiModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)


class FontRequirement(ApiModel):
    id: str
    family: str = Field(max_length=256)
    originalName: str = Field(max_length=256)
    style: str = Field(max_length=128)
    weight: int = Field(ge=1, le=1000)
    sample: str = Field(max_length=4096)
    cropAsset: str


class SceneNode(ApiModel):
    id: str
    type: Literal["text", "vector", "image", "group"]
    name: str = Field(max_length=256)
    x: float
    y: float
    width: float = Field(gt=0, le=32768)
    height: float = Field(gt=0, le=32768)
    # SVG-style affine coefficients; vectors are already in page coordinates.
    transform: list[float] = Field(default_factory=lambda: [1, 0, 0, 1, 0, 0], min_length=6, max_length=6)
    opacity: float = Field(default=1, ge=0, le=1)
    assetId: str | None = None
    fallbackAsset: str | None = None
    fallbackReason: str | None = Field(default=None, max_length=512)
    text: str | None = Field(default=None, max_length=10000)
    fontId: str | None = None
    fontSize: float | None = Field(default=None, gt=0, le=8192)
    baseline: float | None = None
    path: str | None = Field(default=None, max_length=1_000_000)
    fill: list[float] | None = Field(default=None, min_length=4, max_length=4)
    stroke: list[float] | None = Field(default=None, min_length=4, max_length=4)
    strokeWidth: float = Field(default=0, ge=0, le=8192)
    fillRule: Literal["NONZERO", "EVENODD"] = "NONZERO"
    children: list[SceneNode] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def check_node(self):
        if any(abs(v) > 1e7 for v in [self.x, self.y, *self.transform]):
            raise ValueError("Object coordinates exceed the scene limit")
        for color in (self.fill, self.stroke):
            if color and any(not 0 <= c <= 1 for c in color):
                raise ValueError("Invalid color")
        if self.type == "image" and not self.assetId:
            raise ValueError("Image asset is required")
        if self.type == "text" and (not self.text or not self.fontId or not self.fontSize or not self.fallbackAsset):
            raise ValueError("Text requires a font and appearance fallback")
        if self.type == "vector" and not self.path:
            raise ValueError("Vector path is required")
        return self


class EditableScene(ApiModel):
    version: Literal[1] = 1
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)
    nodes: list[SceneNode] = Field(max_length=10000)
    fonts: list[FontRequirement] = Field(max_length=1000)
    assets: list[Asset] = Field(max_length=10002)
    referenceAsset: str
    warnings: list[str] = Field(default_factory=list, max_length=10001)
    wholePageFallback: bool = False

    @model_validator(mode="after")
    def check_references(self):
        assets = {a.id for a in self.assets}
        fonts = {f.id for f in self.fonts}
        if len(assets) != len(self.assets) or len(fonts) != len(self.fonts) or self.referenceAsset not in assets:
            raise ValueError("Invalid scene references")
        seen = set()
        def visit(nodes, depth):
            if depth > 32:
                raise ValueError("Scene nesting limit exceeded")
            for node in nodes:
                if node.id in seen or len(seen) >= 10000:
                    raise ValueError("Duplicate node or node limit exceeded")
                seen.add(node.id)
                if any(a and a not in assets for a in (node.assetId, node.fallbackAsset)) or node.fontId and node.fontId not in fonts:
                    raise ValueError("Missing node dependency")
                visit(node.children, depth + 1)
        visit(self.nodes, 0)
        if any(font.cropAsset not in assets for font in self.fonts):
            raise ValueError("Missing font crop")
        return self
