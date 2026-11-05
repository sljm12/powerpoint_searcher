import asyncio
import sys
import unittest
from pathlib import Path

from powerpoint_extraction.confinement import run_confined_async


@unittest.skipUnless(sys.platform == "win32", "Native Windows fails closed")
class UnsupportedPlatformTests(unittest.TestCase):
    def test_windows_does_not_start_an_unconfined_renderer(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Native Windows.*Linux or WSL"):
            asyncio.run(run_confined_async(["must-not-start"], Path("."), 10))
