"""Run repeatable trusted-fixture renderer comparison and source mapping."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal, cast

from PIL import Image
from pydantic import Field

from .fixtures import generate
from .source import Inventory, Record, inspect, stamp_copy


class Outcome(Record):
    occurrence_id: str
    status: Literal["isolated", "contextual_crop", "not_visible", "failed"]
    method: Literal["libreoffice_shape", "libreoffice_group", "slide_crop", "none"]
    file: str | None
    warnings: list[str] = Field(default_factory=list)
    alpha_extrema: tuple[int, int] | None = None


class Proof(Record):
    inventory: Inventory
    outcomes: list[Outcome]
    missing_imported_objects: list[str]
    missing_imported_text: list[str]
    renderer_object_count: int
    source_occurrence_count: int
    original_unchanged: bool
    slide_failures: list[int] = Field(default_factory=list)
    trusted_fixture_only: Literal[True] = True


def run_worker(
    source: Path, output: Path, bridge: str, office: str, timeout: float = 120
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    command = [
        bridge,
        str(Path(__file__).with_name("uno_worker.py")),
        "--input",
        str(source.resolve()),
        "--output",
        str(output.resolve()),
        "--office",
        office,
    ]
    with (output / "worker.log").open("wb") as log:
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=log,
            start_new_session=os.name != "nt",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        try:
            code = process.wait(timeout=timeout)
            if code:
                raise RuntimeError(f"UNO worker exited {code}; see {output / 'worker.log'}")
        finally:
            if os.name == "nt":
                # The worker owns this entire process subtree, including soffice.bin.
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                try:
                    getattr(os, "killpg")(process.pid, getattr(signal, "SIGKILL"))
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.kill()
                process.wait()
    return cast(dict[str, Any], json.loads((output / "uno.json").read_text(encoding="utf-8")))


def normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def map_outcomes(
    inventory: Inventory,
    uno_report: dict[str, Any],
    output: Path,
    force_crop: set[str] | None = None,
) -> Proof:
    outcomes: list[Outcome] = []
    missing_objects: list[str] = []
    missing_text: list[str] = []
    renderer_count = 0
    slide_failures: list[int] = []
    for slide in inventory.slides:
        page = next((page for page in uno_report["pages"] if page["number"] == slide.number), None)
        if page is None:
            slide_failures.append(slide.number)
            for occurrence in slide.occurrences:
                outcomes.append(
                    Outcome(
                        occurrence_id=occurrence.id,
                        status="failed",
                        method="none",
                        file=None,
                        warnings=["Required slide preview is missing"],
                    )
                )
            continue
        objects = page["objects"]
        renderer_count += len(objects)
        imported_text = normalize(" ".join(obj["text"] for obj in objects))
        by_name = {obj["name"]: obj for obj in objects if obj["name"]}
        slide_missing: list[str] = []
        for occurrence in slide.occurrences:
            x1, y1, x2, y2 = occurrence.bounds
            if x2 < 0 or y2 < 0 or x1 > slide.width or y1 > slide.height:
                continue
            if occurrence.warning:
                slide_missing.append(occurrence.id)
            imported = by_name.get(occurrence.renderer_name)
            if imported is None:
                missing_objects.append(occurrence.id)
                slide_missing.append(occurrence.id)
            if occurrence.text:
                candidate = (
                    imported_text
                    if occurrence.kind in ("chart", "table", "smartart", "group")
                    else normalize(imported["text"])
                    if imported
                    else imported_text
                )
                if any(
                    normalize(paragraph) not in candidate
                    for paragraph in occurrence.paragraphs
                    if normalize(paragraph)
                ):
                    missing_text.append(occurrence.id)
                    slide_missing.append(occurrence.id)
        with Image.open(output / page["file"]) as preview:
            preview.load()
            if preview.width * preview.height > 16_000_000:
                raise ValueError("Renderer preview exceeds pixel limit")
            if slide_missing:
                slide_failures.append(slide.number)
            for occurrence in slide.occurrences:
                x1, y1, x2, y2 = occurrence.bounds
                if x2 < 0 or y2 < 0 or x1 > slide.width or y1 > slide.height:
                    outcomes.append(
                        Outcome(
                            occurrence_id=occurrence.id,
                            status="not_visible",
                            method="none",
                            file=None,
                            warnings=["Source bounds are wholly outside slide"],
                        )
                    )
                    continue
                imported = by_name.get(occurrence.renderer_name)
                if slide_missing:
                    outcomes.append(
                        Outcome(
                            occurrence_id=occurrence.id,
                            status="failed",
                            method="none",
                            file=None,
                            warnings=[
                                "Imported slide lost required source content; crop cannot recover it",
                                *slide_missing,
                            ],
                        )
                    )
                    continue
                if imported and imported["file"] and occurrence.id not in (force_crop or set()):
                    with Image.open(output / imported["file"]) as image:
                        extrema = cast(
                            tuple[float, float], image.convert("RGBA").getchannel("A").getextrema()
                        )
                        alpha = (int(extrema[0]), int(extrema[1]))
                    outcomes.append(
                        Outcome(
                            occurrence_id=occurrence.id,
                            status="isolated",
                            method="libreoffice_group"
                            if occurrence.kind == "group"
                            else "libreoffice_shape",
                            file=imported["file"],
                            alpha_extrema=alpha,
                        )
                    )
                    continue
                x1, y1, x2, y2 = occurrence.bounds
                crop_box = (
                    max(0, math.floor(x1 * preview.width / slide.width)),
                    max(0, math.floor(y1 * preview.height / slide.height)),
                    min(preview.width, math.ceil(x2 * preview.width / slide.width)),
                    min(preview.height, math.ceil(y2 * preview.height / slide.height)),
                )
                if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
                    outcomes.append(
                        Outcome(
                            occurrence_id=occurrence.id,
                            status="failed",
                            method="none",
                            file=None,
                            warnings=["Source bounds do not intersect rendered slide"],
                        )
                    )
                    continue
                filename = (
                    "crop-" + hashlib.sha256(occurrence.id.encode()).hexdigest()[:20] + ".png"
                )
                preview.crop(crop_box).save(output / filename)
                outcomes.append(
                    Outcome(
                        occurrence_id=occurrence.id,
                        status="contextual_crop",
                        method="slide_crop",
                        file=filename,
                        warnings=[
                            "Contextual slide crop includes background and overlapping content; not isolated"
                        ],
                    )
                )
    return Proof(
        inventory=inventory,
        outcomes=outcomes,
        missing_imported_objects=missing_objects,
        missing_imported_text=missing_text,
        renderer_object_count=renderer_count,
        source_occurrence_count=sum(len(slide.occurrences) for slide in inventory.slides),
        original_unchanged=True,
        slide_failures=slide_failures,
    )


def prove(source: Path, output: Path, bridge: str, office: str) -> Proof:
    output.mkdir(parents=True, exist_ok=True)
    inventory = inspect(source, output / "original-assets")
    stamped = output / "private-stamped.pptx"
    stamp_copy(source, stamped)
    uno_report = run_worker(stamped, output, bridge, office)
    proof = map_outcomes(inventory, uno_report, output)
    proof.original_unchanged = (
        hashlib.sha256(source.read_bytes()).hexdigest() == inventory.original_sha256
    )
    (output / "proof.json").write_text(proof.model_dump_json(indent=2), encoding="utf-8")
    if inventory.slides and inventory.slides[0].occurrences:
        fallback = map_outcomes(
            inventory, uno_report, output, {inventory.slides[0].occurrences[0].id}
        )
        (output / "forced-crop-proof.json").write_text(
            fallback.model_dump_json(indent=2), encoding="utf-8"
        )
    return proof


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trusted fixture proof only; not an upload processor"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--bridge",
        default="C:/Program Files/LibreOffice/program/python.exe"
        if os.name == "nt"
        else "/usr/bin/python3",
    )
    parser.add_argument(
        "--office",
        default="C:/Program Files/LibreOffice/program/soffice.com"
        if os.name == "nt"
        else "/usr/bin/soffice",
    )
    parser.add_argument("--fixture", type=Path)
    args = parser.parse_args()
    if args.fixture is None:
        source = generate(args.output / "representative.pptx")
    else:
        source = args.fixture
    result = prove(source, args.output, args.bridge, args.office)
    print(
        json.dumps(
            {
                "outcomes": len(result.outcomes),
                "failed": sum(item.status == "failed" for item in result.outcomes),
                "missing_objects": len(result.missing_imported_objects),
                "missing_text": len(result.missing_imported_text),
                "original_unchanged": result.original_unchanged,
            }
        )
    )
    if result.slide_failures or any(item.status == "failed" for item in result.outcomes):
        sys.exit(1)


if __name__ == "__main__":
    main()
