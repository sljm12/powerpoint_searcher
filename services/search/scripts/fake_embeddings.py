from __future__ import annotations

import argparse
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class Handler(BaseHTTPRequestHandler):
    dimensions = 8
    model = "deterministic-test-model"

    def do_POST(self) -> None:
        if self.path != "/v1/embeddings":
            self.send_error(404)
            return
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        inputs = request.get("input")
        if request.get("model") != self.model or not isinstance(inputs, list) or not inputs:
            self.send_error(400)
            return
        items: list[dict[str, Any]] = []
        for index, text in enumerate(inputs):
            digest = hashlib.sha256(text.encode()).digest()
            vector = [float(digest[position] + 1) for position in range(self.dimensions)]
            items.append({"index": index, "embedding": vector})
        payload = json.dumps({"model": self.model, "data": items}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic local embeddings for search checks")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--dimensions", type=int, default=8)
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1") or args.dimensions < 1:
        parser.error("Use a loopback host and positive dimensions")
    Handler.dimensions = args.dimensions
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
