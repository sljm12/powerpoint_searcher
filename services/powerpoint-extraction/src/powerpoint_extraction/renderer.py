from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast

from PIL import Image
from pydantic import Field

from .confinement import run_confined_async
from .content import (
    ExtractionContent,
    OccurrenceContent,
    Rendition,
    SlideContent,
    SlideSource,
    WarningDetail,
)
from .models import ExtractionFailure, ExtractionResult, OutputAsset, Stage
from .proof import map_outcomes
from .source import (
    DEFAULT_NOTES_TEXT_LIMIT,
    NOTES_PARSER_REVISION,
    NotesError,
    Record,
    inspect,
    stamp_copy,
)


class RendererObject(Record):
    name: str
    parent: str | None
    inherited: bool
    kind: str
    text: str
    position: tuple[int, int]
    size: tuple[int, int]
    file: str | None
    export_error: str | None
    text_introspection_error: str | None


class RendererPage(Record):
    number: int = Field(ge=1, le=100)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    file: str
    objects: list[RendererObject] = Field(max_length=10000)


class RendererReport(Record):
    bridge_python: str
    operating_system: str
    office_version: str = Field(min_length=1)
    pages: list[RendererPage] = Field(min_length=1, max_length=100)


def _file(directory: Path, name: str) -> Path:
    if not name or Path(name).name != name or "/" in name or "\\" in name:
        raise ValueError("Invalid renderer output filename")
    path = directory / name
    if path.is_symlink() or not path.is_file():
        raise ValueError("Renderer output file is missing or unsafe")
    return path


def _image(path: Path) -> tuple[tuple[int, int], bool]:
    with Image.open(path) as image:
        if image.format != "PNG" or image.width * image.height > 16_000_000:
            raise ValueError("Renderer output must be a bounded PNG")
        image.load()
        alpha = cast(tuple[int, int], image.convert("RGBA").getchannel("A").getextrema())
        return (image.width, image.height), alpha[0] < 255


class Renderer:
    def __init__(self, bridge: str | None = None, office: str | None = None) -> None:
        self.bridge = bridge or os.environ.get("EX1_BRIDGE", "/usr/bin/python3")
        self.office = office or os.environ.get("EX1_OFFICE", "/usr/bin/soffice")
        self.ready = False

    async def initialize(self) -> None:
        self.ready = False
        try:
            with tempfile.TemporaryDirectory() as directory:
                result = await run_confined_async(
                    [
                        self.bridge,
                        "-c",
                        'import uno, subprocess, sys; subprocess.run([sys.argv[1], "--headless", "--version"], check=True, timeout=10)',
                        self.office,
                    ],
                    Path(directory),
                    20,
                )
            self.ready = result == 0
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            self.ready = False

    async def __call__(
        self,
        source: Path,
        output: Path,
        progress: Callable[[Stage, int, int], None] | None = None,
        include_notes: bool = True,
        notes_text_limit: int = DEFAULT_NOTES_TEXT_LIMIT,
    ) -> ExtractionResult:
        if not self.ready:
            raise RuntimeError("Renderer confinement is not ready")
        output.mkdir(parents=True, exist_ok=True)
        try:
            inventory = inspect(source, output / "original-assets", include_notes=include_notes,
                                notes_text_limit=notes_text_limit)
        except NotesError:
            raise ExtractionFailure("invalid_notes", "Speaker notes are malformed; verify the deck and retry.") from None
        if any(item.warning and "inherited placeholder field" in item.warning for slide in inventory.slides for item in slide.occurrences):
            raise ExtractionFailure("unsupported_inherited_fields", "Inherited date, footer or slide-number fields cannot be resolved safely. Replace them with ordinary text and upload a new revision.")
        if not inventory.slides:
            raise ValueError("Presentation contains no slides")
        if progress:
            progress("extracting", 0, len(inventory.slides))
        assets: list[OutputAsset] = []
        media_types: dict[
            str,
            Literal[
                "image/png",
                "image/jpeg",
                "image/gif",
                "image/tiff",
                "image/bmp",
                "image/svg+xml",
                "application/octet-stream",
            ],
        ] = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "tif": "image/tiff",
            "tiff": "image/tiff",
            "bmp": "image/bmp",
            "svg": "image/svg+xml",
        }
        for asset in inventory.assets:
            name = f"{asset.sha256}.{asset.extension}"
            path = _file(output / "original-assets", name)
            if hashlib.sha256(path.read_bytes()).hexdigest() != asset.sha256:
                raise ValueError("Original image checksum mismatch")
            assets.append(
                OutputAsset(
                    local_id=asset.id,
                    relative_path=f"original-assets/{name}",
                    media_type=media_types.get(asset.extension.lower(), "application/octet-stream"),
                    role="original_picture",
                    pixel_size=Image.open(path).size if asset.extension.lower() in {"png", "jpg", "jpeg", "gif", "tif", "tiff", "bmp"} else None,
                )
            )
        with tempfile.TemporaryDirectory(dir=output, prefix="work-") as temp:
            work = Path(temp)
            stamp_copy(source, work / "stamped.pptx")
            shutil.copyfile(Path(__file__).with_name("uno_worker.py"), work / "uno_worker.py")
            (work / "rendered").mkdir()
            if progress:
                progress("rendering", 0, len(inventory.slides))
            code = await run_confined_async(
                [
                    self.bridge,
                    "/work/uno_worker.py",
                    "--input",
                    "/work/stamped.pptx",
                    "--output",
                    "/work/rendered",
                    "--office",
                    self.office,
                ],
                work,
                120,
            )
            if code:
                raise RuntimeError("Confined renderer failed")
            rendered = work / "rendered"
            if rendered.is_symlink() or not rendered.is_dir():
                raise ValueError("Renderer output directory is unsafe")
            report_file = _file(rendered, "uno.json")
            if report_file.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("Renderer report exceeds limit")
            report = RendererReport.model_validate_json(report_file.read_bytes())
            if [p.number for p in report.pages] != list(range(1, len(inventory.slides) + 1)):
                raise ValueError("Renderer report does not cover every slide in order")
            for page in report.pages:
                _image(_file(rendered, page.file))
                for obj in page.objects:
                    if obj.file:
                        _image(_file(rendered, obj.file))
            proof = map_outcomes(inventory, report.model_dump(mode="json"), rendered)
            if proof.slide_failures or any(o.status == "failed" for o in proof.outcomes):
                raise ValueError("Renderer lost required source content or a required rendition")
            if len(proof.outcomes) != sum(len(s.occurrences) for s in inventory.slides):
                raise ValueError("Renderer outcomes do not cover source occurrences")
            if progress:
                progress("rendering", len(inventory.slides), len(inventory.slides))
            (output / "renditions").mkdir(exist_ok=True)

            def save(
                name: str,
                local_id: str,
                role: Literal[
                    "slide_preview", "isolated_render", "group_render", "contextual_crop"
                ],
                method: Literal[
                    "slide_render", "libreoffice_shape", "libreoffice_group", "slide_crop", "none"
                ],
                warnings: list[WarningDetail],
                crop: tuple[int, int, int, int] | None = None,
            ) -> Rendition:
                path = _file(rendered, name)
                size, transparent = _image(path)
                target = hashlib.sha256(local_id.encode()).hexdigest() + ".png"
                shutil.copyfile(path, output / "renditions" / target)
                assets.append(
                    OutputAsset(
                        local_id=local_id,
                        relative_path=f"renditions/{target}",
                        media_type="image/png",
                        role=role,
                        pixel_size=size,
                    )
                )
                return Rendition(
                    asset_local_id=local_id,
                    status="rendered",
                    method=method,
                    pixel_size=size,
                    transparent=transparent,
                    crop_rectangle=crop,
                    warnings=warnings,
                )

            by_id = {item.occurrence_id: item for item in proof.outcomes}
            slides = []
            for slide, page in zip(inventory.slides, report.pages, strict=True):
                preview = save(
                    page.file, f"slide-{slide.number}", "slide_preview", "slide_render", []
                )
                occurrences = []
                for occurrence in slide.occurrences:
                    outcome = by_id[occurrence.id]
                    warnings = [WarningDetail(code="contextual_crop" if outcome.method == "slide_crop" else "rendering_fidelity", message=warning, affected_occurrence_ids=[occurrence.id]) for warning in outcome.warnings]
                    if outcome.status == "not_visible":
                        rendition = Rendition(
                            asset_local_id=None,
                            status="not_visible",
                            method="none",
                            pixel_size=None,
                            transparent=None,
                            warnings=warnings,
                        )
                    else:
                        assert outcome.file is not None
                        crop = None
                        if outcome.method == "slide_crop":
                            x1, y1, x2, y2 = occurrence.bounds
                            assert preview.pixel_size is not None
                            width, height = preview.pixel_size
                            crop = (
                                max(0, math.floor(x1 * width / slide.width)),
                                max(0, math.floor(y1 * height / slide.height)),
                                min(width, math.ceil(x2 * width / slide.width)),
                                min(height, math.ceil(y2 * height / slide.height)),
                            )
                        role: Literal["contextual_crop", "group_render", "isolated_render"] = (
                            "contextual_crop"
                            if crop
                            else "group_render"
                            if occurrence.kind == "group"
                            else "isolated_render"
                        )
                        rendition = save(
                            outcome.file,
                            f"rendition:{occurrence.id}",
                            role,
                            outcome.method,
                            warnings,
                            crop,
                        )
                    occurrences.append(
                        OccurrenceContent(
                            source=occurrence,
                            original_asset_local_id=occurrence.asset_id,
                            original_available=occurrence.asset_id is not None,
                            primary=occurrence.parent_id is None,
                            rendition=rendition,
                        )
                    )
                slides.append(
                    SlideContent(
                        source=SlideSource.model_validate(
                            slide.model_dump(exclude={"occurrences", "notes"})
                        ),
                        notes=slide.notes,
                        preview=preview,
                        occurrences=occurrences,
                    )
                )
        fingerprint = hashlib.sha256(
            json.dumps({
                "bridge": self.bridge,
                "office": self.office,
                "include_notes": include_notes,
                "notes_text_limit": notes_text_limit,
                "notes_parser_revision": NOTES_PARSER_REVISION,
            }, sort_keys=True).encode()
        ).hexdigest()
        content = ExtractionContent(
            extractor_version="0.1.0",
            renderer_version=report.office_version,
            config_fingerprint=fingerprint,
            slides=slides,
        )
        warnings = [
            warning
            for slide in slides
            for occurrence in slide.occurrences
            for warning in occurrence.rendition.warnings
        ]
        return ExtractionResult(
            content=content,
            assets=assets,
            slide_count=len(slides),
            warnings=warnings,
        )
