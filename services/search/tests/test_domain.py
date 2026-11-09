from __future__ import annotations

from tokenizers import Tokenizer, models, pre_tokenizers

from search_service.domain import build_segments, collapse_path, reciprocal_rank_fusion
from search_service.models import SlideInput


def tokenizer() -> Tokenizer:
    vocab = {"[UNK]": 0, "red": 1, "blue": 2, "rocket": 3, "note": 4, "one": 5,
             "two": 6, "three": 7, "四": 8}
    result = Tokenizer(models.WordLevel(vocab, unk_token="[UNK]"))
    result.pre_tokenizer = pre_tokenizers.Whitespace()
    return result


def test_chunks_native_and_notes_and_skips_hidden_slides() -> None:
    segments = build_segments(
        [SlideInput(slide_number=1, native_text="red blue rocket", notes_text="note two"),
         SlideInput(slide_number=2, hidden=True, native_text="red rocket")],
        tokenizer(), "", 2, 1, 4,
    )
    assert [(item.slide_number, item.channel, item.ordinal) for item in segments] == [
        (1, "native", 0), (1, "native", 1), (1, "notes", 0),
    ]
    assert segments[0].text == "red blue"
    assert segments[1].text == "blue rocket"


def test_rrf_gives_a_slide_one_vote_per_path_and_deterministic_ties() -> None:
    lexical = [
        {"deck_id": "b", "slide_number": 1, "ordinal": 0, "score": 1.0, "channel": "native"},
        {"deck_id": "b", "slide_number": 1, "ordinal": 1, "score": 0.8, "channel": "notes"},
        {"deck_id": "a", "slide_number": 2, "ordinal": 0, "score": 0.5, "channel": "native"},
    ]
    semantic = [
        {"deck_id": "a", "slide_number": 2, "ordinal": 0, "score": 0.1, "channel": "notes"},
        {"deck_id": "b", "slide_number": 1, "ordinal": 0, "score": 0.2, "channel": "native"},
    ]
    lexical_slides = collapse_path(lexical, "score", True)
    semantic_slides = collapse_path(semantic, "score", False)
    ranked = reciprocal_rank_fusion(lexical_slides, semantic_slides)
    assert [(row["deck_id"], row["slide_number"]) for row in ranked] == [("a", 2), ("b", 1)]
    assert ranked[0]["score"] == 1 / 61 + 1 / 62
    assert ranked[0]["retrieval_paths"] == ["lexical", "semantic"]
