import os

import uvicorn

from .api import Settings, create_app
from .renderer import Renderer
from .source import validate_upload


def main() -> None:
    renderer = Renderer()
    app = create_app(
        Settings.from_environment(),
        extractor=renderer,
        ready=lambda: renderer.ready,
        validate_upload=validate_upload,
        initialize_renderer=renderer.initialize,
    )
    uvicorn.run(
        app,
        host=os.environ.get("EX1_HOST", "127.0.0.1"),
        port=int(os.environ.get("EX1_PORT", "8001")),
    )


if __name__ == "__main__":
    main()
