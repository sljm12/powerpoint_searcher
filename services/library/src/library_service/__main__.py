import os

import uvicorn

from .api import Settings, create_app


def main() -> None:
    settings = Settings.from_environment()
    uvicorn.run(create_app(settings), host=os.environ.get("LIBRARY_HOST", "127.0.0.1"),
                port=int(os.environ.get("LIBRARY_PORT", "8000")), workers=1)


if __name__ == "__main__":
    main()
