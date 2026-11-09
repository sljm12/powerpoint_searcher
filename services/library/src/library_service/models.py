from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ErrorDetail(Record):
    code: str
    message: str
    retryable: bool = False
    stage: str | None = None


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False) -> None:
        self.status = status
        self.detail = ErrorDetail(code=code, message=message, retryable=retryable)


class WarningDetail(Record):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    affected_occurrence_ids: list[str]


class Job(Record):
    job_id: UUID
    deck_id: UUID
    source_revision: int = Field(gt=0)
    generation_id: UUID
    include_notes: StrictBool = False
    notes_text_limit: int = Field(default=4096, ge=1, le=4096, strict=True)
    state: Literal["queued", "processing", "succeeded", "failed", "cancelled"]
    stage: Literal["queued", "extracting", "rendering", "publishing"]
    attempt: int = Field(gt=0)
    completed_slides: int = Field(ge=0)
    total_slides: int = Field(ge=0, le=100)
    created_at: str
    updated_at: str
    error: ErrorDetail | None = None
    warnings: list[WarningDetail] = Field(default_factory=list)
    status_url: str
    manifest_url: str | None = None

    @model_validator(mode="after")
    def counts(self) -> Self:
        if self.completed_slides > self.total_slides:
            raise ValueError("Invalid progress")
        if self.state == "failed" and self.error is None:
            raise ValueError("Failed job requires an error")
        return self


class Deletion(Record):
    deck_id: UUID
    state: Literal["pending", "complete"]
    error: ErrorDetail | None = None


class TextBlock(Record):
    text: str
    paragraphs: list[str]
    source_kind: str
    cell_row: int | None = Field(default=None, ge=0)
    cell_column: int | None = Field(default=None, ge=0)
    reading_order: int | None = Field(default=None, ge=0)


class OccurrenceSource(Record):
    id: str
    slide: int = Field(ge=1)
    source_part: str
    source_shape_id: int = Field(ge=1)
    source_name: str
    renderer_name: str
    parent_id: str | None
    kind: str
    inherited: bool
    source_scope: Literal["slide", "layout", "master"]
    text: str
    transform: tuple[float, float, float, float, float, float]
    bounds: tuple[float, float, float, float]
    source_size: tuple[int, int]
    local_geometry: tuple[int, int, int, int]
    group_path: list[str]
    stacking_order: int
    alt_text: str
    paragraphs: list[str]
    text_blocks: list[TextBlock]
    crop: tuple[float, float, float, float] | None = None
    asset_id: str | None = None
    warning: str | None = None


class Rendition(Record):
    asset_local_id: str | None
    status: Literal["rendered", "not_visible"]
    method: Literal["slide_render", "libreoffice_shape", "libreoffice_group", "slide_crop", "none"]
    pixel_size: tuple[int, int] | None
    transparent: bool | None
    crop_rectangle: tuple[int, int, int, int] | None = None
    warnings: list[WarningDetail] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.status == "not_visible":
            if any(value is not None for value in (
                self.asset_local_id, self.pixel_size, self.transparent, self.crop_rectangle
            )) or self.method != "none":
                raise ValueError("Invalid not-visible rendition")
        elif (not self.asset_local_id or self.method == "none" or self.pixel_size is None
              or min(self.pixel_size) <= 0 or self.transparent is None):
            raise ValueError("Invalid rendered rendition")
        if (self.method == "slide_crop") != (self.crop_rectangle is not None):
            raise ValueError("Invalid crop metadata")
        return self


class Occurrence(Record):
    source: OccurrenceSource
    original_asset_local_id: str | None
    original_available: bool
    primary: bool
    rendition: Rendition

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.original_available != (self.original_asset_local_id is not None)
                or self.original_asset_local_id != self.source.asset_id
                or self.primary != (self.source.parent_id is None)):
            raise ValueError("Invalid occurrence metadata")
        return self


class SlideSource(Record):
    number: int = Field(ge=1)
    source_slide_id: int = Field(gt=0)
    hidden: bool
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class Notes(Record):
    state: Literal["present", "empty", "excluded"]
    paragraphs: list[str]
    truncated: StrictBool

    @model_validator(mode="after")
    def consistent(self) -> Self:
        size = sum(map(len, self.paragraphs)) + max(0, len(self.paragraphs) - 1)
        if size > 4096:
            raise ValueError("Speaker notes exceed the supported limit")
        if self.state in ("empty", "excluded"):
            if self.paragraphs or self.truncated:
                raise ValueError("Empty or excluded notes cannot contain text")
        elif not self.paragraphs or (not self.truncated and not any(item.strip() for item in self.paragraphs)):
            raise ValueError("Present notes require speaker text")
        return self


class Slide(Record):
    source: SlideSource
    notes: Notes | None = None
    preview: Rendition
    occurrences: list[Occurrence]


class Content(Record):
    schema_version: Literal[1]
    extractor_version: str = Field(min_length=1)
    renderer_version: str = Field(min_length=1)
    config_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    slides: list[Slide] = Field(min_length=1, max_length=100)


class Asset(Record):
    asset_id: UUID
    local_id: str
    media_type: Literal["image/png", "image/jpeg", "image/gif", "image/tiff", "image/bmp", "image/svg+xml", "application/octet-stream"]
    role: Literal["slide_preview", "original_picture", "isolated_render", "group_render", "contextual_crop"]
    pixel_size: tuple[int, int] | None = None
    byte_length: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    download_url: str


class Manifest(Record):
    schema_version: Literal[1]
    deck_id: UUID
    source_revision: int = Field(gt=0)
    generation_id: UUID
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_filename: str = Field(min_length=1, max_length=255)
    canonical_original_reference: str | None = None
    content: Content
    assets: list[Asset]
    warnings: list[WarningDetail]

    @model_validator(mode="after")
    def coverage(self) -> Self:
        if [slide.source.number for slide in self.content.slides] != list(range(1, len(self.content.slides) + 1)):
            raise ValueError("Incomplete slide sequence")
        assets = {asset.local_id: asset for asset in self.assets}
        if len(assets) != len(self.assets) or len({asset.asset_id for asset in self.assets}) != len(self.assets):
            raise ValueError("Duplicate asset")
        referenced: set[str] = set()
        occurrences = {item.source.id for slide in self.content.slides for item in slide.occurrences}
        for slide in self.content.slides:
            ids = [item.source.id for item in slide.occurrences]
            if len(ids) != len(set(ids)) or any(item.source.slide != slide.source.number for item in slide.occurrences):
                raise ValueError("Invalid occurrence identity")
            if slide.preview.status != "rendered" or slide.preview.method != "slide_render":
                raise ValueError("Missing slide preview")
            renditions = [(slide.preview, "slide_preview")]
            for item in slide.occurrences:
                if item.original_asset_local_id:
                    original = assets.get(item.original_asset_local_id)
                    if original is None or original.role != "original_picture":
                        raise ValueError("Unknown original asset")
                    referenced.add(item.original_asset_local_id)
                role = {"slide_crop": "contextual_crop", "libreoffice_group": "group_render"}.get(item.rendition.method, "isolated_render")
                renditions.append((item.rendition, role))
            for rendition, role in renditions:
                if rendition.asset_local_id is not None:
                    asset = assets.get(rendition.asset_local_id)
                    if asset is None or asset.role != role or asset.media_type != "image/png" or asset.pixel_size != rendition.pixel_size:
                        raise ValueError("Invalid rendition asset")
                    referenced.add(rendition.asset_local_id)
                if any(not set(w.affected_occurrence_ids) <= occurrences for w in rendition.warnings):
                    raise ValueError("Invalid warning references")
        if referenced != set(assets) or any(not set(w.affected_occurrence_ids) <= occurrences for w in self.warnings):
            raise ValueError("Invalid asset or warning coverage")
        return self


DeckState = Literal["queued", "processing", "extracted", "failed", "deleting", "deleted"]
OperationState = Literal["pending", "processing", "succeeded", "failed", "cancelled"]


class Operation(Record):
    operation_id: UUID
    deck_id: UUID
    kind: Literal["extract", "retry", "delete"]
    state: OperationState
    phase: Literal["submit", "retry", "poll", "delete", "complete"]
    source_revision: int
    completed_slides: int = 0
    total_slides: int = 0
    stage: str = "queued"
    error: ErrorDetail | None = None
    warnings: list[WarningDetail] = Field(default_factory=list)
    created_at: str
    updated_at: str
    status_url: str


class UploadPolicy(Record):
    include_notes: StrictBool


class Deck(Record):
    deck_id: UUID
    filename: str | None
    source_sha256: str | None
    byte_length: int | None
    source_revision: int
    state: DeckState
    operation_id: UUID
    generation_id: UUID | None
    completed_slides: int
    total_slides: int
    stage: str
    error: ErrorDetail | None
    warnings: list[WarningDetail]
    status_url: str
    manifest_url: str | None
    original_url: str | None
    updated_at: str


class Accepted(Record):
    deck_id: UUID
    operation_id: UUID
    status_url: str
    operation_url: str


class DeletionStatus(Record):
    deck_id: UUID
    state: Literal["deleting", "deleted"]
    operation_id: UUID
    updated_at: str
