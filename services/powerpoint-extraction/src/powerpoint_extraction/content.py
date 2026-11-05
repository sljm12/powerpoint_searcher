from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from .source import Occurrence, Record


class WarningDetail(Record):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    affected_occurrence_ids: list[str]


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
            if (
                self.asset_local_id is not None
                or self.method != "none"
                or self.pixel_size is not None
                or self.transparent is not None
                or self.crop_rectangle is not None
            ):
                raise ValueError("Not-visible rendition cannot have an asset or geometry")
        elif (
            not self.asset_local_id
            or self.method == "none"
            or self.pixel_size is None
            or min(self.pixel_size) <= 0
            or self.transparent is None
        ):
            raise ValueError("Rendered rendition requires an asset and dimensions")
        if (self.method == "slide_crop") != (self.crop_rectangle is not None):
            raise ValueError("Only slide crops have crop rectangles")
        return self


class OccurrenceContent(Record):
    source: Occurrence
    original_asset_local_id: str | None
    original_available: bool
    primary: bool
    rendition: Rendition

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (
            self.original_available != (self.original_asset_local_id is not None)
            or self.original_asset_local_id != self.source.asset_id
        ):
            raise ValueError("Original image reference is inconsistent")
        if self.primary != (self.source.parent_id is None):
            raise ValueError("Group children are not primary")
        return self


class SlideSource(Record):
    number: int = Field(ge=1)
    source_slide_id: int = Field(gt=0)
    hidden: bool
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class SlideContent(Record):
    source: SlideSource
    preview: Rendition
    occurrences: list[OccurrenceContent]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.preview.status != "rendered" or self.preview.method != "slide_render":
            raise ValueError("Every slide requires a preview")
        ids = [item.source.id for item in self.occurrences]
        if len(ids) != len(set(ids)) or any(
            item.source.slide != self.source.number for item in self.occurrences
        ):
            raise ValueError("Invalid occurrence identity or slide")
        return self


class ExtractionContent(Record):
    schema_version: Literal[1] = 1
    extractor_version: str = Field(min_length=1)
    renderer_version: str = Field(min_length=1)
    config_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    slides: list[SlideContent] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if [slide.source.number for slide in self.slides] != list(range(1, len(self.slides) + 1)):
            raise ValueError("Slides must be complete and ordered")
        return self
