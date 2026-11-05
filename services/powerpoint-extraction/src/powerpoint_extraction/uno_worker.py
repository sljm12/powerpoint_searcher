"""Stdlib-only trusted-fixture UNO bridge, run with LibreOffice's Python."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import uno  # type: ignore[import-not-found]
from com.sun.star.beans import PropertyValue  # type: ignore[import-not-found]


def property_value(name: str, value: Any) -> Any:
    result = PropertyValue()
    result.Name, result.Value = name, value
    return result


def properties(**values: Any) -> tuple[Any, ...]:
    return tuple(property_value(key, value) for key, value in values.items())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--office", required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    pipe_name = "pptx_" + uuid.uuid4().hex
    profile = output / "private-profile"
    office = subprocess.Popen(
        [
            args.office,
            "--headless",
            "--nologo",
            "--nodefault",
            "--norestore",
            "--nofirststartwizard",
            "-env:UserInstallation=" + profile.as_uri(),
            f"--accept=pipe,name={pipe_name};urp;StarOffice.ComponentContext",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    desktop = document = None
    try:
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext(
            "com.sun.star.bridge.UnoUrlResolver", local
        )
        deadline = time.monotonic() + 25
        while True:
            try:
                context = resolver.resolve(
                    f"uno:pipe,name={pipe_name};urp;StarOffice.ComponentContext"
                )
                break
            except Exception:
                if time.monotonic() >= deadline or office.poll() is not None:
                    raise RuntimeError(
                        "LibreOffice private pipe did not become available"
                    ) from None
                time.sleep(0.1)
        manager = context.ServiceManager
        desktop = manager.createInstanceWithContext("com.sun.star.frame.Desktop", context)
        document = desktop.loadComponentFromURL(
            args.input.resolve().as_uri(),
            "_blank",
            0,
            properties(
                Hidden=True,
                ReadOnly=True,
                MacroExecutionMode=uno.getConstantByName(
                    "com.sun.star.document.MacroExecMode.NEVER_EXECUTE"
                ),
                UpdateDocMode=uno.getConstantByName(
                    "com.sun.star.document.UpdateDocMode.NO_UPDATE"
                ),
            ),
        )
        if document is None:
            raise RuntimeError("LibreOffice did not load the presentation")

        def export(component: Any, path: Path, width: int, height: int, alpha: bool) -> None:
            if width < 1 or height < 1 or width * height > 16_000_000:
                raise ValueError("Requested PNG exceeds renderer pixel limit")
            exporter = manager.createInstanceWithContext(
                "com.sun.star.drawing.GraphicExportFilter", context
            )
            exporter.setSourceDocument(component)
            options = properties(PixelWidth=width, PixelHeight=height, Translucent=alpha)
            if not exporter.filter(
                properties(MediaType="image/png", URL=path.as_uri(), FilterData=options)
            ):
                raise RuntimeError("GraphicExportFilter returned false")
            if not path.is_file() or path.stat().st_size == 0:
                raise RuntimeError("GraphicExportFilter produced no PNG")

        pages = []
        for index in range(document.getDrawPages().getCount()):
            page = document.getDrawPages().getByIndex(index)
            width, height = int(page.Width), int(page.Height)
            page_file = f"slide-{index + 1}.png"
            export(page, output / page_file, 1280, max(1, round(1280 * height / width)), False)
            objects: list[dict[str, Any]] = []

            def visit(shapes: Any, parent: str | None, inherited: bool, depth: int = 0) -> None:
                if depth > 16 or len(objects) > 10000:
                    raise ValueError("Imported shape tree exceeds proof limits")
                for position in range(shapes.getCount()):
                    shape = shapes.getByIndex(position)
                    name = str(getattr(shape, "Name", ""))
                    size, location = shape.getSize(), shape.getPosition()
                    text = str(getattr(shape, "String", ""))
                    introspection_error = None
                    try:
                        if shape.getShapeType() == "com.sun.star.drawing.TableShape":
                            table = shape.Model
                            text = "\n".join(
                                table.getCellByPosition(column, row).String
                                for row in range(table.getRows().getCount())
                                for column in range(table.getColumns().getCount())
                            )
                        elif shape.supportsService("com.sun.star.drawing.OLE2Shape"):
                            model = shape.Model
                            if model.supportsService("com.sun.star.chart.ChartDocument"):
                                data = model.getData()
                                labels = [*data.getColumnDescriptions(), *data.getRowDescriptions()]
                                text = "\n".join(str(value) for value in labels)
                    except Exception as error:
                        introspection_error = str(error)
                    filename = f"slide-{index + 1}-object-{len(objects) + 1}.png"
                    record = {
                        "name": name,
                        "parent": parent,
                        "inherited": inherited,
                        "kind": shape.getShapeType(),
                        "text": text,
                        "position": [location.X, location.Y],
                        "size": [size.Width, size.Height],
                        "file": filename,
                        "export_error": None,
                        "text_introspection_error": introspection_error,
                    }
                    try:
                        export(
                            shape,
                            output / filename,
                            max(1, round(abs(size.Width) * 1280 / width)),
                            max(1, round(abs(size.Height) * 1280 / width)),
                            True,
                        )
                    except Exception as error:
                        record["file"] = None
                        record["export_error"] = str(error)
                    objects.append(record)
                    if shape.supportsService("com.sun.star.drawing.GroupShape"):
                        visit(shape, name, inherited, depth + 1)

            visit(page, None, False)
            visit(page.getMasterPage(), None, True)
            pages.append(
                {
                    "number": index + 1,
                    "width": width,
                    "height": height,
                    "file": page_file,
                    "objects": objects,
                }
            )
        version = subprocess.run(
            [args.office, "--version"], capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()
        report = {
            "bridge_python": sys.version,
            "operating_system": platform.platform(),
            "office_version": version,
            "pages": pages,
        }
        (output / "uno.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    finally:
        try:
            if document is not None:
                document.close(True)
        finally:
            if desktop is not None:
                desktop.terminate()
        try:
            office.wait(timeout=5)
        except subprocess.TimeoutExpired:
            office.kill()
            office.wait()


if __name__ == "__main__":
    main()
