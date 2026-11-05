"""Refresh source metadata against already-retained Windows render artifacts."""
import json
from pathlib import Path
from PIL import Image

from powerpoint_extraction.proof import map_outcomes
from powerpoint_extraction.source import inspect

for name, fixture in (("windows", "evidence/windows/representative.pptx"),
                      ("windows-smartart", "fixtures/smartart-chevron.pptx")):
    output = Path("evidence") / name
    previous = json.loads((output / "proof.json").read_text())
    inventory = inspect(Path(fixture))
    assert inventory.original_sha256 == previous["inventory"]["original_sha256"]
    imported = json.loads((output / "uno.json").read_text())
    result = map_outcomes(inventory, imported, output)
    assert not result.slide_failures
    (output / "proof.json").write_text(result.model_dump_json(indent=2))
    fallback = map_outcomes(inventory, imported, output, {inventory.slides[0].occurrences[0].id})
    (output / "forced-crop-proof.json").write_text(fallback.model_dump_json(indent=2))
    print(name, inventory.original_sha256, len(result.outcomes), "no slide failures")

samples = []
for name in ("windows", "linux"):
    output = Path("evidence") / name
    report = json.loads((output / "proof.json").read_text())
    picture = next(item for item in report["inventory"]["slides"][0]["occurrences"]
                   if item["source_name"] == "repeated-picture")
    outcome = next(item for item in report["outcomes"] if item["occurrence_id"] == picture["id"])
    with Image.open(output / outcome["file"]) as image:
        pixel = image.convert("RGBA").getpixel((image.width//4, image.height//2))
        assert abs(pixel[3] - 190) <= 1, (name, pixel)
        samples.append({"platform": name, "file": outcome["file"], "size": image.size,
                        "sample_position": [image.width//4, image.height//2], "rgba": pixel})
(Path("evidence") / "alpha-pixels.json").write_text(json.dumps(samples, indent=2))
