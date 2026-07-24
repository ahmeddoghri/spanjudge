from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .core import TraceStore, evaluate_policy, parse_otlp_json


def make_handler(store: TraceStore, web_root: Path):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, payload, status=HTTPStatus.OK):
            body = json.dumps(payload, indent=2).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self):
            if self.path == "/api/summary":
                return self._json(store.summary())
            if self.path == "/api/traces":
                return self._json(store.traces())
            if self.path in ("/", "/index.html"):
                body = (web_root / "index.html").read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self):
            try:
                payload = self._body()
                if self.path == "/v1/traces":
                    count = store.ingest(parse_otlp_json(payload))
                    return self._json({"accepted_spans": count}, HTTPStatus.ACCEPTED)
                if self.path == "/api/evaluate":
                    return self._json(evaluate_policy(store.summary(), payload))
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except (ValueError, json.JSONDecodeError) as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

        def log_message(self, *_):
            return

    return Handler


def serve(database: str, host: str, port: int) -> None:
    store = TraceStore(database)
    web_root = Path(__file__).resolve().parents[1] / "web"
    server = ThreadingHTTPServer((host, port), make_handler(store, web_root))
    print(f"spanjudge listening on http://{host}:{port}")
    server.serve_forever()
