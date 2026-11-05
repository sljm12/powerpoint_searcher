"""Generate deterministic ordinary-content fixture; real SmartArt is vendored separately."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.util import Inches, Pt


def generate(destination: Path) -> Path:
    presentation = Presentation()
    presentation.slide_width = Inches(10)
    presentation.slide_height = Inches(7.5)
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])

    def box(shapes: Any, name: str, text: str, x: float, y: float, w: float, h: float) -> Any:
        shape = shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h)
        )
        shape.name = name
        shape.text = text
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor(35, 99, 145)  # type: ignore[no-untyped-call]
        for paragraph in shape.text_frame.paragraphs:
            paragraph.font.size = Pt(16)
            paragraph.font.color.rgb = RGBColor(255, 255, 255)  # type: ignore[no-untyped-call]
        return shape

    box(slide.shapes, "ordinary-text", "Source inventory proof\nSecond paragraph", 0.3, 0.2, 5, 0.8)
    image = Image.new("RGBA", (160, 120), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((15, 10, 145, 110), fill=(230, 70, 30, 190))
    draw.rectangle((60, 25, 90, 95), fill=(20, 100, 240, 255))
    image_bytes = BytesIO()
    image.save(image_bytes, format="PNG")
    blob = image_bytes.getvalue()
    picture = slide.shapes.add_picture(
        BytesIO(blob), Inches(0.5), Inches(1.3), Inches(2), Inches(1.5)
    )
    picture.name = "cropped-alpha-picture"
    picture.crop_left, picture.crop_right = 0.12, 0.18
    picture.rotation = 24
    slide.shapes.add_picture(
        BytesIO(blob), Inches(3), Inches(1.3), Inches(2), Inches(1.5)
    ).name = "repeated-picture"
    group = slide.shapes.add_group_shape()
    group.name = "outer-group"
    box(group.shapes, "outer-child", "Group parent", 5.5, 1.4, 2, 0.7)
    nested = group.shapes.add_group_shape()
    nested.name = "nested-group"
    box(nested.shapes, "nested-child", "Nested child", 5.8, 2.3, 1.8, 0.6)
    nested.shapes.add_picture(
        BytesIO(blob), Inches(7.8), Inches(2.3), Inches(1), Inches(0.75)
    ).name = "nested-picture"
    nested.rotation = 13
    group.width = int(group.width * 1.10)
    group.height = int(group.height * 0.90)
    group.rotation = 7
    connector = slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, Inches(5.5), Inches(3.4), Inches(8.7), Inches(3.4)
    )
    connector.name = "connector"
    table = slide.shapes.add_table(2, 2, Inches(0.5), Inches(3.4), Inches(3), Inches(1)).table
    for cell, text in zip(
        (cell for row in table.rows for cell in row.cells),
        ("Region", "Value", "North", "42"),
        strict=True,
    ):
        cell.text = text
    chart_data = CategoryChartData()  # type: ignore[no-untyped-call]
    chart_data.categories = ["First", "Second"]
    chart_data.add_series("Measure", (3, 7))  # type: ignore[no-untyped-call]
    slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.5), Inches(4.8), Inches(4), Inches(2), chart_data
    )

    master = presentation.slide_masters[0]
    master_mark = box(slide.shapes, "inherited-master-mark", "Inherited master", 7.3, 6.7, 2.2, 0.4)
    master.shapes._spTree.append(master_mark.element)
    hidden = presentation.slides.add_slide(presentation.slide_layouts[6])
    hidden._element.set("show", "0")
    box(hidden.shapes, "hidden-slide-text", "Hidden slide retained", 0.8, 1, 5, 1)
    destination.parent.mkdir(parents=True, exist_ok=True)
    presentation.save(str(destination))
    return destination
