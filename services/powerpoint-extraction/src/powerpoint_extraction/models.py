from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from .content import ExtractionContent, WarningDetail
from .source import DEFAULT_NOTES_TEXT_LIMIT

State = Literal["queued", "processing", "succeeded", "failed", "cancelled"]
Stage = Literal["queued", "extracting", "rendering", "publishing"]
Progress = Callable[[Stage, int, int], None]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ErrorDetail(Record):
    code: str
    message: str
    retryable: bool = False
    stage: Stage | None = None


class ErrorResponse(Record):
    error: ErrorDetail
    request_id: UUID


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False) -> None:
        self.status = status
        self.detail = ErrorDetail(code=code, message=message, retryable=retryable)


class ExtractionFailure(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        self.code, self.message, self.retryable = code, message, retryable


class Job(Record):
    job_id: UUID
    deck_id: UUID
    source_revision: int = Field(gt=0)
    generation_id: UUID
    state: State
    stage: Stage
    attempt: int = Field(gt=0)
    include_notes: StrictBool = False
    notes_text_limit: int = Field(default=DEFAULT_NOTES_TEXT_LIMIT, ge=1,
                                   le=DEFAULT_NOTES_TEXT_LIMIT, strict=True)
    completed_slides: int = Field(ge=0)
    total_slides: int = Field(ge=0)
    created_at: str
    updated_at: str
    error: ErrorDetail | None = None
    warnings: list[WarningDetail] = Field(default_factory=list)
    status_url: str
    manifest_url: str | None = None


class Deletion(Record):
    deck_id: UUID
    state: Literal["pending", "complete"]
    error: ErrorDetail | None = None


class Health(Record):
    ready: bool


class OutputAsset(Record):
    local_id: str = Field(min_length=1, max_length=200)
    relative_path: str = Field(min_length=1, max_length=250)
    media_type: Literal["image/png", "image/jpeg", "image/gif", "image/tiff", "image/bmp", "image/svg+xml", "application/octet-stream"]
    role: Literal["slide_preview", "original_picture", "isolated_render", "group_render", "contextual_crop"]
    pixel_size: tuple[int, int] | None = None

    @model_validator(mode="after")
    def dimensions(self) -> Self:
        if self.media_type.startswith("image/") and self.media_type != "image/svg+xml" and (self.pixel_size is None or min(self.pixel_size) <= 0):
            raise ValueError("Raster image assets require positive pixel dimensions")
        return self


class ExtractionResult(Record):
    content: ExtractionContent
    assets: list[OutputAsset]
    slide_count: int = Field(ge=1, le=100)
    warnings: list[WarningDetail] = Field(default_factory=list)

    @model_validator(mode="after")
    def slide_coverage(self) -> Self:
        if self.slide_count != len(self.content.slides):
            raise ValueError("Slide count must match complete content")
        return self


class Asset(Record):
    asset_id: UUID
    local_id: str
    media_type: str
    role: str
    pixel_size: tuple[int, int] | None = None
    byte_length: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    download_url: str


class Manifest(Record):
    schema_version: Literal[1] = 1
    deck_id: UUID
    source_revision: int = Field(gt=0)
    generation_id: UUID
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_filename: str = Field(min_length=1, max_length=255)
    canonical_original_reference: str | None = None
    content: ExtractionContent
    assets: list[Asset]
    warnings: list[WarningDetail]

    @model_validator(mode="after")
    def asset_coverage(self) -> Self:
        assets = {item.local_id: item for item in self.assets}
        if len(assets) != len(self.assets):
            raise ValueError("Duplicate asset identity")
        referenced: set[str] = set()
        occurrence_ids = {item.source.id for slide in self.content.slides for item in slide.occurrences}
        for slide in self.content.slides:
            renditions = [(slide.preview, "slide_preview")]
            for item in slide.occurrences:
                if item.original_asset_local_id:
                    original = assets.get(item.original_asset_local_id)
                    if original is None or original.role != "original_picture":
                        raise ValueError("Original asset is not registered")
                    referenced.add(item.original_asset_local_id)
                role = {"slide_crop": "contextual_crop", "libreoffice_group": "group_render"}.get(item.rendition.method, "isolated_render")
                renditions.append((item.rendition, role))
            for rendition, role in renditions:
                if rendition.asset_local_id is not None:
                    asset = assets.get(rendition.asset_local_id)
                    if asset is None or asset.role != role or asset.pixel_size != rendition.pixel_size or asset.media_type != "image/png":
                        raise ValueError("Rendition asset metadata is inconsistent")
                    referenced.add(rendition.asset_local_id)
                for warning in rendition.warnings:
                    if not set(warning.affected_occurrence_ids) <= occurrence_ids:
                        raise ValueError("Warning references an unknown occurrence")
        if referenced != set(assets):
            raise ValueError("Generation assets must be fully referenced")
        for warning in self.warnings:
            if not set(warning.affected_occurrence_ids) <= occurrence_ids:
                raise ValueError("Warning references an unknown occurrence")
        return self


class Claim(Record):
    job: Job
    fence: int
    source_path: Path
    output_path: Path


class Extractor(Protocol):
    async def __call__(
        self,
        source: Path,
        output: Path,
        progress: Progress,
        include_notes: bool = True,
        notes_text_limit: int = DEFAULT_NOTES_TEXT_LIMIT,
    ) -> ExtractionResult:
        """On cancellation, stop and reap subprocesses before returning. Write only output."""
        ...
