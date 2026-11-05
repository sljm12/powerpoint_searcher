"""Operational disk guardrail checked while a renderer is running."""

import os
from pathlib import Path


def check_output_limits(directory: Path) -> None:
    total = count = 0
    for root, directories, files in os.walk(directory, followlinks=False):
        count += len(directories) + len(files)
        if count > 10_000:
            raise RuntimeError("Renderer exceeded the 10,000 output entry safety limit")
        for name in files:
            try:
                total += (Path(root) / name).lstat().st_size
            except FileNotFoundError:
                continue
            if total > 512 * 1024**2:
                raise RuntimeError("Renderer exceeded the 512 MiB output disk safety limit")
