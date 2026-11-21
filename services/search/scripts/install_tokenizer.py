"""Provision and verify the pinned Qwen3 Embedding 8B tokenizer."""
from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
from urllib.request import urlopen

REVISION = "4e423935c619ae4df87b646a3ce949610c66241c"
SHA256 = "83cdf8c3a34f68862319cb1810ee7b1e2c0a44e0864ae930194ddb76bb7feb8d"
SIZE = 11_422_947
URL = f"https://huggingface.co/Qwen/Qwen3-Embedding-8B/resolve/{REVISION}/tokenizer.json"
DESTINATION = Path(__file__).resolve().parents[1] / "tokenizer/qwen3-embedding-8b/tokenizer.json"


def verify(payload: bytes) -> None:
    if len(payload) != SIZE or sha256(payload).hexdigest() != SHA256:
        raise SystemExit("Tokenizer failed revision and SHA-256 verification.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="Verify the existing artifact without downloading.")
    options = parser.parse_args()
    if options.verify or DESTINATION.exists():
        if not DESTINATION.exists():
            raise SystemExit("Tokenizer is absent. Run the installer before verification.")
        verify(DESTINATION.read_bytes())
    else:
        with urlopen(URL, timeout=30) as response:
            payload = response.read(SIZE + 1)
        verify(payload)
        DESTINATION.parent.mkdir(parents=True, exist_ok=True)
        temporary = DESTINATION.with_suffix(".tmp")
        temporary.write_bytes(payload)
        temporary.replace(DESTINATION)
    print(f"Verified Qwen3 Embedding 8B tokenizer SHA-256 {SHA256}")


if __name__ == "__main__":
    main()
