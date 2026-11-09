from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class WarningDetail(Record):
    code: StrictStr = Field(min_length=1)
    message: StrictStr = Field(min_length=1, max_length=500)


class JobError(WarningDetail):
    retryable: StrictBool = False


class SlideInput(Record):
    slide_number: StrictInt = Field(ge=1)
    hidden: StrictBool = False
    native_text: StrictStr = Field(default="", max_length=32768)
    notes_text: StrictStr = Field(default="", max_length=32768)
    warnings: list[WarningDetail] = Field(default_factory=list, max_length=100)


class IndexRequest(Record):
    library_id: UUID
    deck_id: UUID
    generation_id: UUID
    source_revision: StrictInt = Field(gt=0)
    index_revision: StrictInt = Field(gt=0)
    profile_id: StrictStr = Field(min_length=1, max_length=128)
    slides: list[SlideInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def slide_sequence(self) -> IndexRequest:
        if [slide.slide_number for slide in self.slides] != list(range(1, len(self.slides) + 1)):
            raise ValueError("Slides must have consecutive one-based numbers")
        return self


class JobAccepted(Record):
    job_id: UUID
    state: Literal["queued", "processing", "ready", "failed"]
    status_url: str


class JobStatus(Record):
    job_id: UUID
    library_id: UUID
    deck_id: UUID
    generation_id: UUID
    source_revision: int
    index_revision: int
    profile_id: str
    state: Literal["queued", "processing", "ready", "failed", "cancelled"]
    completed_segments: int = Field(ge=0)
    total_segments: int = Field(ge=0)
    eligible_slides: int = Field(default=0, ge=0)
    searchable_slides: int = Field(default=0, ge=0)
    blank_slides: int = Field(default=0, ge=0)
    hidden_slides: int = Field(default=0, ge=0)
    warnings: list[WarningDetail] = Field(default_factory=list)
    error: JobError | None = None


class SearchRequest(Record):
    library_id: UUID
    profile_id: StrictStr = Field(min_length=1, max_length=128)
    query: StrictStr = Field(min_length=1, max_length=2000)
    deck_id: UUID | None = None
    limit: StrictInt = Field(default=20, ge=1, le=100)
    candidate_offset: StrictInt = Field(default=0, ge=0, le=10000)
    candidate_limit: StrictInt = Field(default=2000, ge=1, le=2000)


class Match(Record):
    slide_number: int
    generation_id: UUID
    index_revision: int
    score: float
    score_kind: Literal["rrf", "lexical"]
    retrieval_paths: list[Literal["lexical", "semantic"]]
    matched_channel: Literal["native", "notes"]
    snippet: str = Field(max_length=500)
    warning_metadata: list[WarningDetail]


class DeckMatches(Record):
    deck_id: UUID
    matches: list[Match]
    has_more_matches: bool


class SearchResponse(Record):
    query_id: UUID
    profile_id: str
    mode: Literal["hybrid", "lexical"]
    degraded: bool
    warnings: list[WarningDetail]
    coverage: dict[str, int]
    decks: list[DeckMatches]
    next_offset: int | None = None


class DeleteResponse(Record):
    deck_id: UUID
    state: Literal["complete"]
