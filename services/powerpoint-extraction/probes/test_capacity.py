import io
import tempfile
import unittest
import zipfile
from pathlib import Path

import capacity
from powerpoint_extraction.source import inspect
from pptx import Presentation


class FixtureTest(unittest.TestCase):
    def test_fixture_has_20_slides_and_original_picture(self):
        with tempfile.TemporaryDirectory() as directory:
            data, picture_sha = capacity.fixture(Path(directory) / "fixture.pptx")
            presentation = Presentation(io.BytesIO(data))
            self.assertEqual(len(presentation.slides), 20)
            for index, slide in enumerate(presentation.slides):
                names = {shape.name for shape in slide.shapes}
                self.assertIn(f"capacity-text-{index}", names)
                self.assertIn(f"capacity-picture-{index}", names)
            inventory = inspect(Path(directory) / "fixture.pptx")
            first_slide = {item.source_name: item for item in inventory.slides[0].occurrences}
            self.assertEqual(first_slide["capacity-table"].kind, "table")
            self.assertIn("North", first_slide["capacity-table"].text)
            self.assertEqual(first_slide["capacity-chart"].kind, "chart")
            self.assertIn("Measure", first_slide["capacity-chart"].text)
            self.assertEqual(
                first_slide["capacity-group-child"].parent_id,
                first_slide["capacity-group"].id,
            )
            pictures = [
                occurrence
                for slide in inventory.slides
                for occurrence in slide.occurrences
                if occurrence.source_name.startswith("capacity-picture-")
            ]
            self.assertEqual(len(pictures), 20)
            self.assertEqual(len({item.id for item in pictures}), 20)
            self.assertEqual(len({item.asset_id for item in pictures}), 1)
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                images = [archive.read(name) for name in archive.namelist() if name.startswith("ppt/media/")]
            self.assertIn(picture_sha, {capacity.sha(image) for image in images})


if __name__ == "__main__":
    unittest.main()
