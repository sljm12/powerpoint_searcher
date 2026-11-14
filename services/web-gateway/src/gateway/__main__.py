import os

import uvicorn


uvicorn.run("gateway.main:app", host=os.environ.get("GATEWAY_HOST", "127.0.0.1"),
            port=int(os.environ.get("GATEWAY_PORT", "8080")), workers=1)
