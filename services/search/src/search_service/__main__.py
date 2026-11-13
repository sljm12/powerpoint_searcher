from __future__ import annotations

import uvicorn

from .api import create_app
from .settings import Settings


settings = Settings.from_environment()
app = create_app(settings)


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
