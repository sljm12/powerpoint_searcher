from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from tokenizers import Tokenizer

from .models import SlideInput, WarningDetail


@dataclass(frozen=True)
class Segment:
    slide_number: int
    channel: str
    ordinal: int
    text: str
    warnings: list[WarningDetail]


def profile_id(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def build_segments(
    slides: list[SlideInput], tokenizer: Tokenizer, document_prefix: str,
    chunk_tokens: int, overlap: int, max_input_tokens: int,
) -> list[Segment]:
    segments: list[Segment] = []
    for slide in slides:
        if slide.hidden:
            continue
        for channel, source in (("native", slide.native_text), ("notes", slide.notes_text)):
            text = re.sub(r"\s+", " ", source).strip()
            if not text:
                continue
            encoded = tokenizer.encode(text)
            ids = encoded.ids
            start = 0
            ordinal = 0
            while start < len(ids):
                end = min(start + chunk_tokens, len(ids))
                part = tokenizer.decode(ids[start:end]).strip()
                if len(tokenizer.encode(document_prefix + part).ids) > max_input_tokens:
                    raise ValueError("A text chunk exceeds the configured model input limit")
                if part:
                    segments.append(Segment(slide.slide_number, channel, ordinal, part,
                                            slide.warnings.copy()))
                    ordinal += 1
                if end == len(ids):
                    break
                start = end - overlap
    return segments


def collapse_path(rows: list[dict[str, Any]], score_key: str, descending: bool) -> list[dict[str, Any]]:
    best: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["deck_id"]), int(row["slide_number"]))
        prior = best.get(key)
        score = float(row[score_key])
        if prior is None or (score > float(prior[score_key]) if descending else score < float(prior[score_key])):
            best[key] = row
    return sorted(best.values(), key=lambda row: (
        -float(row[score_key]) if descending else float(row[score_key]),
        str(row["deck_id"]), int(row["slide_number"]), int(row["ordinal"]),
    ))


def reciprocal_rank_fusion(lexical: list[dict[str, Any]], semantic: list[dict[str, Any]],
                           constant: int = 60) -> list[dict[str, Any]]:
    fused: dict[tuple[str, int], dict[str, Any]] = {}
    for path, rows in (("lexical", lexical), ("semantic", semantic)):
        for rank, row in enumerate(rows, 1):
            key = (str(row["deck_id"]), int(row["slide_number"]))
            target = fused.setdefault(key, {**row, "score": 0.0, "retrieval_paths": []})
            target["score"] = float(target["score"]) + 1 / (constant + rank)
            target["retrieval_paths"].append(path)
            if path == "lexical":
                target["lexical_segment"] = row
    return sorted(fused.values(), key=lambda row: (
        -float(row["score"]), str(row["deck_id"]), int(row["slide_number"])
    ))
