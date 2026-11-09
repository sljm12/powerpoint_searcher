from __future__ import annotations

from .settings import Settings
from .store import SearchStore


def main() -> int:
    settings = Settings.from_environment()
    if not settings.embedding_dimensions:
        raise SystemExit("Configure the embedding dimensions before running search migrations.")
    store = SearchStore(settings)
    try:
        store.open(migrate=True)
    finally:
        store.close()
    print("Search schema is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
