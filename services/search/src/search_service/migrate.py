from __future__ import annotations

import asyncio

from .embeddings import EmbeddingError, Embeddings
from .settings import Settings
from .store import SearchStore


def main() -> int:
    settings = Settings.from_environment()
    if not settings.embedding_dimensions:
        raise SystemExit("Configure the embedding dimensions before running search migrations.")
    try:
        actual_dimensions = asyncio.run(_probe_dimensions(settings))
    except EmbeddingError as error:
        raise SystemExit(f"Embedding dimension validation failed ({error.code}); database was not changed.") from None
    if actual_dimensions != settings.embedding_dimensions:
        raise SystemExit(
            f"Configured embedding dimension is {settings.embedding_dimensions}, but endpoint returned "
            f"{actual_dimensions}; database was not changed."
        )
    store = SearchStore(settings)
    try:
        store.open(migrate=True)
    finally:
        store.close()
    print("Search schema is ready.")
    return 0


async def _probe_dimensions(settings: Settings) -> int:
    embeddings = Embeddings(settings)
    try:
        vectors = await embeddings.embed(["search database dimension validation"])
        return len(vectors[0])
    finally:
        await embeddings.close()


if __name__ == "__main__":
    raise SystemExit(main())
