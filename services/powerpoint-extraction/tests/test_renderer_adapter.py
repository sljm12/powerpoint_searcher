import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches
from pydantic import ValidationError

from powerpoint_extraction.content import ExtractionContent, Rendition
from powerpoint_extraction.fixtures import generate
from powerpoint_extraction.renderer import Renderer, RendererReport


def test_renderer_report_rejects_incomplete_and_extra_fields():
    with pytest.raises(ValidationError):
        RendererReport.model_validate({"pages": []})
    with pytest.raises(ValidationError):
        Rendition(
            asset_local_id=None,
            status="rendered",
            method="slide_render",
            pixel_size=None,
            transparent=None,
        )


def test_missing_report_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def fake_run(*args: object, **kwargs: object) -> int:
        return 0

    monkeypatch.setattr("powerpoint_extraction.renderer.run_confined_async", fake_run)
    renderer = Renderer()
    renderer.ready = True
    with pytest.raises(ValueError, match="missing or unsafe"):
        asyncio.run(renderer(generate(tmp_path / "input.pptx"), tmp_path / "output"))


def test_missing_preview_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def fake_run(command: list[str], work: Path, timeout: float) -> int:
        report = {
            "bridge_python": "3",
            "operating_system": "Linux",
            "office_version": "1",
            "pages": [
                {"number": n, "width": 100, "height": 100, "file": f"slide-{n}.png", "objects": []}
                for n in (1, 2)
            ],
        }
        (work / "rendered" / "uno.json").write_text(json.dumps(report))
        return 0

    monkeypatch.setattr("powerpoint_extraction.renderer.run_confined_async", fake_run)
    renderer = Renderer()
    renderer.ready = True
    with pytest.raises(ValueError, match="missing or unsafe"):
        asyncio.run(renderer(generate(tmp_path / "input.pptx"), tmp_path / "output"))


@pytest.mark.skipif(
    os.getenv("EX1_REAL_RENDERER") != "1", reason="Requires Linux confinement and LibreOffice"
)
def test_actual_confined_conversion(tmp_path: Path):
    source = generate(tmp_path / "source.pptx")
    presentation = Presentation(source)
    shape = presentation.slides[0].shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(20), Inches(20), Inches(1), Inches(1)
    )
    shape.name = "offslide"
    presentation.save(source)
    renderer = Renderer()

    async def run():
        await renderer.initialize()
        assert renderer.ready
        return await renderer(source, tmp_path / "output")

    result = asyncio.run(run())
    content = ExtractionContent.model_validate(result.content)
    assert [slide.source.number for slide in content.slides] == [1, 2]
    assert content.slides[1].source.hidden
    offslide = next(
        item for item in content.slides[0].occurrences if item.source.source_name == "offslide"
    )
    assert offslide.rendition.status == "not_visible"
    assert offslide.rendition.asset_local_id is None
    assets = {asset.local_id: asset for asset in result.assets}
    pictures = [
        item for slide in content.slides for item in slide.occurrences if item.original_available
    ]
    assert len(pictures) >= 3
    assert len({item.original_asset_local_id for item in pictures}) == 1
    picture = assets[pictures[0].original_asset_local_id]
    assert (
        hashlib.sha256((tmp_path / "output" / picture.relative_path).read_bytes()).hexdigest()
        == picture.local_id
    )
    for slide in content.slides:
        assert slide.preview.asset_local_id in assets
        for item in slide.occurrences:
            assert item.rendition.status == "not_visible" or item.rendition.asset_local_id in assets
