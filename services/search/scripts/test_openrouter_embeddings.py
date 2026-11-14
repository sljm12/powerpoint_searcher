from __future__ import annotations

import asyncio

from search_service.embeddings import Embeddings
from search_service.settings import Settings


async def main() -> None:
    settings = Settings.from_environment()
    embeddings = Embeddings(settings)
    try:
        vectors = await embeddings.embed(["PowerPoint embedding dimension check"])
    finally:
        await embeddings.close()

    if len(vectors) != 1:
        raise SystemExit(f"Expected one embedding, received {len(vectors)}")
    actual = len(vectors[0])
    expected = settings.embedding_dimensions
    print(f"Model: {settings.embedding_expected_model or settings.embedding_model}")
    print(f"Returned dimensions: {actual}")
    if expected and actual != expected:
        raise SystemExit(f"Configured dimension is {expected}, but endpoint returned {actual}")
    print("Dimension check passed.")


if __name__ == "__main__":
    asyncio.run(main())
