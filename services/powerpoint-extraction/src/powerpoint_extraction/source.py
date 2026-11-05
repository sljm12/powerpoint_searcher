"""Bounded source inventory. Original image bytes use python-pptx's public API."""

from __future__ import annotations

import hashlib
import math
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from zipfile import BadZipFile, ZipFile

from lxml import etree  # type: ignore[import-untyped]
from PIL import Image
from pptx import Presentation
from pydantic import BaseModel, ConfigDict, Field

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}
Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1, 0, 0, 1, 0, 0)


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TextBlock(Record):
    text: str
    paragraphs: list[str]
    source_kind: str
    cell_row: int | None = Field(default=None, ge=0)
    cell_column: int | None = Field(default=None, ge=0)
    reading_order: int | None = Field(default=None, ge=0)


class Occurrence(Record):
    id: str
    slide: int = Field(ge=1)
    source_part: str
    source_shape_id: int = Field(ge=1)
    source_name: str
    renderer_name: str
    parent_id: str | None
    kind: str
    inherited: bool
    source_scope: Literal["slide", "layout", "master"]
    text: str
    transform: Matrix
    bounds: tuple[float, float, float, float]
    source_size: tuple[int, int]
    local_geometry: tuple[int, int, int, int]
    group_path: list[str]
    stacking_order: int
    alt_text: str
    paragraphs: list[str]
    text_blocks: list[TextBlock]
    crop: tuple[float, float, float, float] | None = None
    asset_id: str | None = None
    warning: str | None = None


class Slide(Record):
    number: int = Field(ge=1)
    source_slide_id: int = Field(gt=0)
    hidden: bool
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    occurrences: list[Occurrence]


class Asset(Record):
    id: str
    sha256: str
    extension: str
    size: int = Field(gt=0)


class Inventory(Record):
    schema_version: Literal[1] = 1
    original_sha256: str
    slides: list[Slide]
    assets: list[Asset]


def multiply(first: Matrix, second: Matrix) -> Matrix:
    a, b, c, d, e, f = first
    g, h, i, j, k, offset_y = second
    return (
        a * g + c * h,
        b * g + d * h,
        a * i + c * j,
        b * i + d * j,
        a * k + c * offset_y + e,
        b * k + d * offset_y + f,
    )


def translated(x: float, y: float) -> Matrix:
    return (1, 0, 0, 1, x, y)


def bounds(matrix: Matrix, width: float, height: float) -> tuple[float, float, float, float]:
    a, b, c, d, e, f = matrix
    points = [
        (a * x + c * y + e, b * x + d * y + f)
        for x, y in ((0, 0), (width, 0), (width, height), (0, height))
    ]
    return (
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    )


def shape_matrix(shape: Any, group: bool = False) -> Matrix:
    element = shape.element
    nodes = element.xpath("./p:spPr/a:xfrm | ./p:grpSpPr/a:xfrm | ./p:xfrm")
    xfrm = nodes[0] if nodes else None
    x, y = int(shape.left or 0), int(shape.top or 0)
    w, h = int(shape.width or 0), int(shape.height or 0)
    if xfrm is None:
        return translated(x, y)
    angle = math.radians(int(xfrm.get("rot", "0")) / 60000)
    flip_x = -1 if xfrm.get("flipH") == "1" else 1
    flip_y = -1 if xfrm.get("flipV") == "1" else 1
    rotation: Matrix = (
        math.cos(angle) * flip_x,
        math.sin(angle) * flip_x,
        -math.sin(angle) * flip_y,
        math.cos(angle) * flip_y,
        0,
        0,
    )
    matrix = multiply(translated(x + w / 2, y + h / 2), rotation)
    matrix = multiply(matrix, translated(-w / 2, -h / 2))
    if group:
        off, ext = xfrm.find("a:chOff", NS), xfrm.find("a:chExt", NS)
        if off is not None and ext is not None:
            ew, eh = int(ext.get("cx", "0")), int(ext.get("cy", "0"))
            if not ew or not eh:
                raise ValueError("Group has zero child coordinate extent")
            matrix = multiply(matrix, (w / ew, 0, 0, h / eh, 0, 0))
            matrix = multiply(matrix, translated(-int(off.get("x")), -int(off.get("y"))))
    return matrix


def renderer_name(part: str, shape_id: int) -> str:
    return "ps_" + hashlib.sha256(f"{part}:{shape_id}".encode()).hexdigest()[:24]


def checked_archive(path: Path) -> None:
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Presentation exceeds 64 MiB proof limit")
    with ZipFile(path) as archive:
        files = archive.infolist()
        if len(files) > 10000 or sum(i.file_size for i in files) > 256 * 1024 * 1024:
            raise ValueError("Presentation expanded size or member count exceeds limit")
        names = set()
        for item in files:
            name = PurePosixPath(item.filename)
            if (
                name.is_absolute()
                or ".." in name.parts
                or "\\" in item.filename
                or ":" in item.filename
                or "\x00" in item.filename
                or item.filename in names
                or item.flag_bits & 1
            ):
                raise ValueError("Unsafe, duplicate or encrypted archive member")
            names.add(item.filename)
            if item.file_size > 32 * 1024 * 1024:
                raise ValueError("Archive member exceeds 32 MiB proof limit")
            if item.file_size > max(1, item.compress_size) * 1000:
                raise ValueError("Archive compression ratio exceeds proof limit")
            if item.filename.endswith((".xml", ".rels")):
                data = archive.read(item)
                if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
                    raise ValueError("XML entities and DTDs are forbidden")
                root = etree.fromstring(
                    data, etree.XMLParser(resolve_entities=False, no_network=True)
                )
                if root.getroottree().docinfo.doctype or any(
                    isinstance(node, etree._Entity) for node in root.iter()
                ):
                    raise ValueError("XML entities and DTDs are forbidden in all encodings")
                if item.filename.endswith(".rels"):
                    for rel in root:
                        if rel.get("TargetMode") == "External":
                            raise ValueError(
                                "External relationships are forbidden in renderer proof"
                            )
            if "vbaProject" in item.filename or "activeX" in item.filename:
                raise ValueError("Active content is forbidden in renderer proof")
        if "[Content_Types].xml" not in names or "ppt/presentation.xml" not in names:
            raise ValueError("Archive is not a PowerPoint OOXML package")
        content_types = etree.fromstring(archive.read("[Content_Types].xml"), etree.XMLParser(resolve_entities=False, no_network=True))
        main_types = [item.get("ContentType") for item in content_types if item.get("PartName") == "/ppt/presentation.xml"]
        if main_types != ["application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"]:
            raise ValueError("Only macro-free .pptx presentations are supported")


def validate_upload(path: Path) -> None:
    try:
        checked_archive(path)
        presentation = Presentation(str(path))
        if not 1 <= len(presentation.slides) <= 100:
            raise ValueError("Presentation must contain 1 to 100 slides")
    except (BadZipFile, etree.XMLSyntaxError, KeyError) as error:
        raise ValueError("Invalid presentation package") from error


def inspect(path: Path, asset_directory: Path | None = None) -> Inventory:
    checked_archive(path)
    presentation = Presentation(str(path))
    if len(presentation.slides) > 100:
        raise ValueError("Presentation exceeds 100-slide proof limit")
    assets: dict[str, Asset] = {}
    slides: list[Slide] = []
    if asset_directory:
        asset_directory.mkdir(parents=True, exist_ok=True)
    for number, slide in enumerate(presentation.slides, 1):
        occurrences: list[Occurrence] = []
        inherited_fields = {"dt": False, "ftr": False, "sldNum": False}
        for owner in (slide.slide_layout.slide_master, slide.slide_layout, slide):
            footer_settings = owner._element.find("p:hf", NS)
            if footer_settings is not None:
                for field in inherited_fields:
                    inherited_fields[field] = footer_settings.get(field, "1") not in ("0", "false")

        def visit(
            shapes: Any,
            part: str,
            inherited: bool,
            parent_id: str | None,
            parent_matrix: Matrix,
            depth: int = 0,
            group_path: list[str] | None = None,
        ) -> None:
            if depth > 16:
                raise ValueError("Shape nesting exceeds proof limit")
            for stacking_order, shape in enumerate(shapes):
                if len(occurrences) > 10000:
                    raise ValueError("Slide shape count exceeds proof limit")
                placeholder_warning = None
                if inherited and shape.is_placeholder:
                    placeholders = shape.element.xpath("./*/p:nvPr/p:ph")
                    placeholder_type = placeholders[0].get("type", "obj") if placeholders else "obj"
                    if not inherited_fields.get(placeholder_type, False):
                        continue
                    placeholder_warning = (
                        "Unsupported effective inherited placeholder field resolution; "
                        "required rendition must fail rather than omit visible content"
                    )
                is_group = shape.shape_type == 6
                identity = f"slide-{slide.slide_id}:{part}:{shape.shape_id}"
                matrix = multiply(parent_matrix, shape_matrix(shape))
                text = shape.text_frame.text if shape.has_text_frame else ""
                text_blocks: list[TextBlock] = []
                kind = "group" if is_group else "shape"
                asset_id, crop = None, None
                if shape.has_table:
                    kind = "table"
                    text = "\n".join(cell.text for row in shape.table.rows for cell in row.cells)
                    text_blocks = [
                        TextBlock(
                            text=cell.text,
                            paragraphs=cell.text.split("\n"),
                            source_kind="table_cell",
                            cell_row=row_index,
                            cell_column=column_index,
                        )
                        for row_index, row in enumerate(shape.table.rows)
                        for column_index, cell in enumerate(row.cells)
                    ]
                elif shape.has_chart:
                    kind = "chart"
                    text = "\n".join(shape.element.xpath(".//a:t/text()"))
                    chart_xml = shape.chart.part._element
                    text = "\n".join(
                        chart_xml.xpath(
                            ".//a:t/text() | .//c:tx//c:v/text() | .//c:cat//c:v/text()"
                        )
                    )
                elif shape.shape_type == 13:
                    kind = "picture"
                    blob = shape.image.blob
                    with Image.open(BytesIO(blob)) as image:
                        if image.width * image.height > 16_000_000:
                            raise ValueError("Original image exceeds 16-megapixel proof limit")
                    asset_id = hashlib.sha256(blob).hexdigest()
                    assets[asset_id] = Asset(
                        id=asset_id, sha256=asset_id, extension=shape.image.ext, size=len(blob)
                    )
                    if asset_directory:
                        (asset_directory / f"{asset_id}.{shape.image.ext}").write_bytes(blob)
                    crop = (shape.crop_left, shape.crop_top, shape.crop_right, shape.crop_bottom)
                elif shape.element.xpath(".//a:graphicData"):
                    data = shape.element.xpath(".//a:graphicData")[0]
                    kind = "smartart" if "diagram" in data.get("uri", "") else "graphic"
                elif shape.shape_type == 9:
                    kind = "connector"
                if kind == "smartart":
                    # Diagram text lives in a related data part rather than the graphic frame.
                    rel_ids = shape.element.xpath(".//*[local-name()='relIds']")
                    if rel_ids:
                        relationship_id = rel_ids[0].get(
                            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}dm"
                        )
                        root = etree.fromstring(shape.part.rels[relationship_id].target_part.blob)
                        text = "\n".join(root.xpath(".//a:t/text()", namespaces=NS))
                properties = shape.element.xpath("./*/p:cNvPr")
                alt_text = properties[0].get("descr", "") if properties else ""
                occurrences.append(
                    Occurrence(
                        id=identity,
                        slide=number,
                        source_part=part,
                        source_shape_id=shape.shape_id,
                        source_name=shape.name,
                        renderer_name=renderer_name(part, shape.shape_id),
                        parent_id=parent_id,
                        kind=kind,
                        inherited=inherited,
                        text=text,
                        source_scope="master"
                        if "/slideMasters/" in part
                        else "layout"
                        if "/slideLayouts/" in part
                        else "slide",
                        transform=matrix,
                        bounds=bounds(matrix, int(shape.width or 0), int(shape.height or 0)),
                        source_size=(int(shape.width or 0), int(shape.height or 0)),
                        local_geometry=(
                            int(shape.left or 0),
                            int(shape.top or 0),
                            int(shape.width or 0),
                            int(shape.height or 0),
                        ),
                        group_path=group_path or [],
                        stacking_order=stacking_order,
                        alt_text=alt_text,
                        paragraphs=text.split("\n") if text else [],
                        text_blocks=text_blocks
                        or (
                            [TextBlock(text=text, paragraphs=text.split("\n"), source_kind=kind)]
                            if text
                            else []
                        ),
                        crop=crop,
                        asset_id=asset_id,
                        warning=placeholder_warning,
                    )
                )
                if is_group:
                    visit(
                        shape.shapes,
                        part,
                        inherited,
                        identity,
                        multiply(parent_matrix, shape_matrix(shape, group=True)),
                        depth + 1,
                        [*(group_path or []), identity],
                    )

        if slide._element.get("showMasterSp", "1") != "0":
            visit(
                slide.slide_layout.slide_master.shapes,
                str(slide.slide_layout.slide_master.part.partname),
                True,
                None,
                IDENTITY,
            )
            visit(
                slide.slide_layout.shapes,
                str(slide.slide_layout.part.partname),
                True,
                None,
                IDENTITY,
            )
        visit(slide.shapes, str(slide.part.partname), False, None, IDENTITY)
        slides.append(
            Slide(
                number=number,
                source_slide_id=slide.slide_id,
                hidden=slide._element.get("show") == "0",
                width=int(presentation.slide_width or 0),
                height=int(presentation.slide_height or 0),
                occurrences=occurrences,
            )
        )
    return Inventory(
        original_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        slides=slides,
        assets=list(assets.values()),
    )


def stamp_copy(source: Path, destination: Path) -> None:
    """Stamp source identity into private copy; never reserialize the uploaded deck."""
    checked_archive(source)
    with ZipFile(source) as archive, ZipFile(destination, "w") as output:
        for item in archive.infolist():
            data = archive.read(item)
            if item.filename.startswith(
                ("ppt/slides/slide", "ppt/slideLayouts/slideLayout", "ppt/slideMasters/slideMaster")
            ) and item.filename.endswith(".xml"):
                root = etree.fromstring(
                    data, etree.XMLParser(resolve_entities=False, no_network=True)
                )
                for node in root.findall(".//p:cNvPr", NS):
                    if node.get("id") != "1":
                        node.set("name", renderer_name("/" + item.filename, int(node.get("id"))))
                data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            output.writestr(item, data)
