from __future__ import annotations

import hashlib
import math
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from lxml import etree
from PIL import Image
from pptx import Presentation
from pydantic import ValidationError

from powerpoint_extraction.fixtures import generate
from powerpoint_extraction.proof import Outcome, map_outcomes
from powerpoint_extraction.source import checked_archive, inspect, stamp_copy


def test_enabled_inherited_footer_is_retained_and_fails_until_field_resolution(
    tmp_path: Path,
) -> None:
    source = generate(tmp_path / "footer.pptx")
    presentation = Presentation(str(source))
    etree.SubElement(
        presentation.slides[0]._element,
        "{http://schemas.openxmlformats.org/presentationml/2006/main}hf",
        ftr="1",
        dt="0",
        sldNum="0",
    )
    presentation.save(str(source))
    inventory = inspect(source)
    unsupported = [item for item in inventory.slides[0].occurrences if item.warning]
    assert unsupported
    assert any(item.source_scope == "master" for item in unsupported)
    assert "field resolution" in unsupported[0].warning
    Image.new("RGB", (1000, 750), (255, 255, 255)).save(tmp_path / "page.png")
    report = {
        "pages": [
            {
                "number": slide.number,
                "file": "page.png",
                "objects": [
                    {"name": item.renderer_name, "text": item.text, "file": None}
                    for item in slide.occurrences
                ],
            }
            for slide in inventory.slides
        ]
    }
    proof = map_outcomes(inventory, report, tmp_path)
    assert proof.slide_failures == [1]


def test_source_inventory_preserves_occurrences_assets_groups_and_inheritance(
    tmp_path: Path,
) -> None:
    source = generate(tmp_path / "fixture.pptx")
    inventory = inspect(source, tmp_path / "assets")
    assert [slide.hidden for slide in inventory.slides] == [False, True]
    assert len({slide.source_slide_id for slide in inventory.slides}) == 2
    assert len(inventory.assets) == 1
    slide = inventory.slides[0]
    pictures = [item for item in slide.occurrences if item.kind == "picture"]
    assert len(pictures) == 3
    assert len({item.id for item in pictures}) == 3
    assert len({item.asset_id for item in pictures}) == 1
    original_asset = next((tmp_path / "assets").iterdir()).read_bytes()
    assert hashlib.sha256(original_asset).hexdigest() == inventory.assets[0].sha256
    assert pictures[0].crop == (0.12, 0, 0.18, 0)
    assert pictures[0].bounds[2] - pictures[0].bounds[0] > pictures[0].source_size[0]
    nested = next(item for item in pictures if item.source_name == "nested-picture")
    assert len(nested.group_path) == 2
    assert nested.parent_id == nested.group_path[-1]
    assert nested.transform[1] != 0
    assert all(
        any(
            item.source_scope == "master" and item.text == "Inherited master"
            for item in slide.occurrences
        )
        for slide in inventory.slides
    )
    assert next(
        item for item in slide.occurrences if item.source_name == "ordinary-text"
    ).paragraphs == ["Source inventory proof", "Second paragraph"]
    table = next(item for item in slide.occurrences if item.kind == "table")
    north = next(block for block in table.text_blocks if block.text == "North")
    assert (north.cell_row, north.cell_column) == (1, 0)


def test_original_is_unchanged_and_stamped_identity_is_repeatable(tmp_path: Path) -> None:
    source = generate(tmp_path / "fixture.pptx")
    original = source.read_bytes()
    stamp_copy(source, tmp_path / "private.pptx")
    assert source.read_bytes() == original
    original_inventory = inspect(source)
    stamped_inventory = inspect(tmp_path / "private.pptx")
    for before, after in zip(original_inventory.slides, stamped_inventory.slides, strict=True):
        assert [item.id for item in before.occurrences] == [item.id for item in after.occurrences]
        assert [item.renderer_name for item in before.occurrences] == [
            item.source_name for item in after.occurrences
        ]


def test_genuine_smartart_package_has_diagram_data_and_public_source_identity() -> None:
    source = Path(__file__).parents[1] / "fixtures" / "smartart-chevron.pptx"
    assert (
        hashlib.sha256(source.read_bytes()).hexdigest()
        == "a6a9011c6d7b542a91dfcc1e6ba4c2e7d468f027b8ceab9663882b0f77efdf08"
    )
    with ZipFile(source) as archive:
        assert any(name.startswith("ppt/diagrams/data") for name in archive.namelist())
        assert any(name.startswith("ppt/diagrams/layout") for name in archive.namelist())
    diagrams = [
        item
        for slide in inspect(source).slides
        for item in slide.occurrences
        if item.kind == "smartart"
    ]
    assert len(diagrams) == 1
    assert diagrams[0].paragraphs == ["a", "b", "c"]


@pytest.mark.parametrize(
    "member,data",
    [
        ("../escape", b"x"),
        ("C:/escape", b"x"),
        ("ppt/slides/utf16.xml", '<!DOCTYPE x [<!ENTITY bad "x">]><x>&bad;</x>'.encode("utf-16")),
        ("ppt/slides/slide1.xml", b'<!DOCTYPE x [<!ENTITY bad "x">]><x/>'),
        (
            "ppt/slides/_rels/slide1.xml.rels",
            b'<Relationships><Relationship TargetMode="External" Target="https://example.com/x"/></Relationships>',
        ),
        ("ppt/vbaProject.bin", b"active"),
    ],
)
def test_archive_rejects_unsafe_content(tmp_path: Path, member: str, data: bytes) -> None:
    source = tmp_path / "unsafe.pptx"
    with ZipFile(source, "w", ZIP_DEFLATED) as archive:
        archive.writestr(member, data)
    with pytest.raises(ValueError):
        checked_archive(source)


def renderer_report(tmp_path: Path) -> tuple[object, dict]:
    inventory = inspect(generate(tmp_path / "fixture.pptx"))
    Image.new("RGB", (1000, 750), (255, 255, 255)).save(tmp_path / "page.png")
    Image.new("RGBA", (20, 20), (30, 60, 90, 128)).save(tmp_path / "object.png")
    report = {
        "pages": [
            {
                "number": slide.number,
                "file": "page.png",
                "objects": [
                    {"name": item.renderer_name, "text": item.text, "file": "object.png"}
                    for item in slide.occurrences
                ],
            }
            for slide in inventory.slides
        ]
    }
    return inventory, report


def test_mapping_preserves_explicit_outcome_and_alpha_for_every_occurrence(tmp_path: Path) -> None:
    inventory, report = renderer_report(tmp_path)
    proof = map_outcomes(inventory, report, tmp_path)
    assert proof.source_occurrence_count == len(proof.outcomes) == 14
    assert not proof.slide_failures
    assert all(
        item.status == "isolated" and item.alpha_extrema == (128, 128) for item in proof.outcomes
    )


def test_missing_graphic_or_text_fails_slide_instead_of_claiming_crop(tmp_path: Path) -> None:
    inventory, report = renderer_report(tmp_path)
    report["pages"][0]["objects"].pop(2)
    proof = map_outcomes(inventory, report, tmp_path)
    assert proof.slide_failures == [1]
    assert proof.missing_imported_objects
    assert all(item.status == "failed" for item in proof.outcomes[:12])


def test_missing_slide_fails_even_when_source_slide_has_no_occurrences(tmp_path: Path) -> None:
    inventory, report = renderer_report(tmp_path)
    inventory.slides[0].occurrences.clear()
    report["pages"].pop(0)
    proof = map_outcomes(inventory, report, tmp_path)
    assert proof.slide_failures == [1]


def test_fallback_uses_affine_source_bounds_and_actual_png_dimensions(tmp_path: Path) -> None:
    inventory, report = renderer_report(tmp_path)
    picture = next(
        item
        for item in inventory.slides[0].occurrences
        if item.source_name == "cropped-alpha-picture"
    )
    proof = map_outcomes(inventory, report, tmp_path, {picture.id})
    crop = next(item for item in proof.outcomes if item.occurrence_id == picture.id)
    assert crop.status == "contextual_crop"
    assert crop.warnings and crop.file
    with Image.open(tmp_path / crop.file) as image:
        expected_width = 100 * (2 * math.cos(math.radians(24)) + 1.5 * math.sin(math.radians(24)))
        assert abs(image.width - expected_width) < 2
        assert image.width != 200


def test_outcome_rejects_unknown_fields_and_nonfinite_source_values() -> None:
    with pytest.raises(ValidationError):
        Outcome(occurrence_id="x", status="ready", method="none", file=None)
