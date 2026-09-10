from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


PageOrientation = Literal["landscape", "portrait", "square"]


def orientation_for_dimensions(width: float, height: float) -> PageOrientation:
    difference = abs(width - height) / max(width, height)
    return "square" if difference <= 0.01 else "landscape" if width > height else "portrait"


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, allow_inf_nan=False)

    def json_dict(self, *, exclude_none: bool = True) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=exclude_none)


class CapturedPage(ApiModel):
    id: str
    index: int = Field(ge=0)
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)
    orientation: PageOrientation | None = None
    screenshot_base64: str = Field(alias="screenshotBase64", min_length=1)

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


class JobError(ApiModel):
    code: str
    message: str


class CaptureRequest(ApiModel):
    url: HttpUrl


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid4())


def validate_uuid(value: Any) -> str:
    return str(UUID(str(value)))
