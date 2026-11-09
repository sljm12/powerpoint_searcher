from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest
from lxml import etree
from pptx import Presentation
from pydantic import ValidationError

from powerpoint_extraction.fixtures import generate_notes
from powerpoint_extraction.source import Notes, NotesError, inspect


def test_notes_body_is_slide_level_and_bounded(tmp_path: Path) -> None:
    source = generate_notes(tmp_path / "notes.pptx")
    checksum_before = source.read_bytes()
    presentation = Presentation(source)
    assert not presentation.slides[4].has_notes_slide

    inventory = inspect(source, notes_text_limit=4096)

    assert source.read_bytes() == checksum_before
    assert len(inventory.slides) == 5
    assert inventory.slides[1].hidden
    assert inventory.slides[0].notes == Notes(
        state="present",
        paragraphs=["First notes paragraph", "Unicode: résumé 東京 🙂", "", "Last notes paragraph"],
        truncated=False,
    )
    assert inventory.slides[1].notes == Notes(
        state="present", paragraphs=["Hidden slide notes"], truncated=False
    )
    assert inventory.slides[2].notes == Notes.empty()
    assert inventory.slides[3].notes == Notes(
        state="present", paragraphs=["L" * 4096], truncated=True
    )
    assert inventory.slides[4].notes == Notes.empty()
    assert "SENTINEL" not in inventory.slides[0].notes.model_dump_json()
    assert all(
        "SENTINEL" not in occurrence.text
        for slide in inventory.slides
        for occurrence in slide.occurrences
    )
    excluded = inspect(source, include_notes=False)
    assert all(slide.notes == Notes.empty("excluded") for slide in excluded.slides)


def test_note_limit_counts_paragraph_boundaries_and_keeps_truncation(tmp_path: Path) -> None:
    source = generate_notes(tmp_path / "notes.pptx")
    notes = inspect(source, notes_text_limit=1).slides[0].notes

    assert notes == Notes(state="present", paragraphs=["F"], truncated=True)
    whitespace_prefix = Notes(state="present", paragraphs=["", ""], truncated=True)
    assert whitespace_prefix.state == "present"


def test_malformed_notes_relationship_fails_without_reading_text(tmp_path: Path) -> None:
    source = generate_notes(tmp_path / "notes.pptx")
    malformed = tmp_path / "malformed.pptx"
    with ZipFile(source) as original, ZipFile(malformed, "w") as changed:
        for item in original.infolist():
            data = original.read(item)
            if item.filename == "ppt/slides/_rels/slide1.xml.rels":
                root = etree.fromstring(data)
                for relation in root:
                    if relation.get("Type", "").endswith("/notesSlide"):
                        relation.set("Target", "missing-notes.xml")
                data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            changed.writestr(item, data)

    with pytest.raises(NotesError, match="Speaker notes are malformed"):
        inspect(malformed)


@pytest.mark.parametrize(
    "value",
    [
        {"state": "empty", "paragraphs": ["text"], "truncated": False},
        {"state": "excluded", "paragraphs": [], "truncated": True},
        {"state": "present", "paragraphs": ["   "], "truncated": False},
        {"state": "present", "paragraphs": ["x" * 4097], "truncated": False},
    ],
)
def test_notes_model_rejects_invalid_states(value: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Notes.model_validate(value)
