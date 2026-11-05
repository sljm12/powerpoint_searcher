"""Run the representative UNO fixture through the actual Windows sandbox."""

import argparse
import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path

from confinement_windows import run_windows_confined


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", default=r"C:\Program Files\LibreOffice\program\python.exe")
    parser.add_argument("--office", default=r"C:\Program Files\LibreOffice\program\soffice.com")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    fixture = output / "source.pptx"
    shutil.copyfile(args.fixture, fixture)
    result = output / "rendered"
    command = [args.python, str(args.worker.resolve()), "--input", str(fixture),
               "--output", str(result), "--office", args.office]
    code = run_windows_confined(command, output, 90, (args.worker.resolve(),))
    assert code == 0, f"Confined UNO worker failed with code {code}"
    report = json.loads((result / "uno.json").read_text(encoding="utf-8"))
    assert report["pages"], "No pages exported"
    assert any(obj["kind"] == "com.sun.star.drawing.GroupShape"
               for page in report["pages"] for obj in page["objects"]), "No group export exercised"
    images = []
    for page in report["pages"]:
        names = [page["file"], *(obj["file"] for obj in page["objects"] if obj["file"])]
        for name in names:
            data = (result / name).read_bytes()
            assert data[:8] == b"\x89PNG\r\n\x1a\n"
            width, height = struct.unpack(">II", data[16:24])
            assert width > 0 and height > 0
            images.append({"file": name, "width": width, "height": height,
                           "sha256": hashlib.sha256(data).hexdigest()})
    evidence = {"platform": sys.platform, "confinement": "zero-capability AppContainer and bounded Job Object",
                "exit_code": code, "pages": len(report["pages"]), "png_count": len(images),
                "source_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(), "images": images,
                "object_export_errors": [obj["export_error"] for page in report["pages"]
                                         for obj in page["objects"] if obj["export_error"]]}
    (output / "confinement-proof.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({key: evidence[key] for key in ("platform", "exit_code", "pages", "png_count", "object_export_errors")}))


if __name__ == "__main__":
    main()
