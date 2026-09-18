"""Minimal offline local HTTP API for catalog + IN/OUT confirm (Phase 2).

Uses only the stdlib http.server. Bind to 127.0.0.1 by default.
Analysis/inventory code paths do not import this module.
"""

from __future__ import annotations

import json
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Type
from urllib.parse import parse_qs, urlparse

from .store import WarehouseStore, default_db_path


def _read_json(handler: BaseHTTPRequestHandler) -> Any:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b"{}"
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def make_handler(store: WarehouseStore) -> Type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            # Quiet by default; prefix with local marker
            print(f"[local-api] {self.address_string()} - {fmt % args}")

        def _send(self, code: int, body: Any) -> None:
            data = json.dumps(body, indent=2, sort_keys=True).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            try:
                parsed = urlparse(self.path)
                path = parsed.path.rstrip("/") or "/"
                qs = parse_qs(parsed.query)
                if path == "/health":
                    self._send(200, {"ok": True, "offline": True})
                    return
                if path == "/skus":
                    self._send(200, {"skus": [s.to_dict() for s in store.list_skus()]})
                    return
                if path.startswith("/skus/"):
                    sku_id = path.split("/", 2)[2]
                    sku = store.get_sku(sku_id)
                    if sku is None:
                        self._send(404, {"error": "sku not found"})
                        return
                    self._send(200, sku.to_dict())
                    return
                if path == "/events":
                    limit = int((qs.get("limit") or ["100"])[0])
                    events = [e.to_dict() for e in store.list_events(limit=limit)]
                    for e in events:
                        e["sku_lines"] = store.event_sku_lines(e["id"])
                    self._send(200, {"events": events})
                    return
                self._send(404, {"error": "not found"})
            except Exception as exc:  # pragma: no cover
                self._send(500, {"error": str(exc), "trace": traceback.format_exc()})

        def do_POST(self) -> None:  # noqa: N802
            try:
                parsed = urlparse(self.path)
                path = parsed.path.rstrip("/") or "/"
                body = _read_json(self)
                if path == "/skus":
                    sku = store.upsert_sku(
                        body.get("sku_id", ""),
                        body.get("name", ""),
                        body.get("description", ""),
                    )
                    self._send(201, sku.to_dict())
                    return
                if path == "/skus/seed":
                    added = store.seed_common_skus()
                    self._send(200, {"added": added, "skus": [s.to_dict() for s in store.list_skus()]})
                    return
                if path in ("/events/in", "/events/out", "/confirm/in", "/confirm/out"):
                    direction = "IN" if path.endswith("in") else "OUT"
                    image_path = body.get("image_path") or ""
                    if not image_path:
                        self._send(400, {"error": "image_path required"})
                        return
                    event = store.record_event(
                        direction=direction,
                        image_path=image_path,
                        scan=body.get("scan") or {},
                        note=body.get("note") or "",
                    )
                    payload = event.to_dict()
                    payload["sku_lines"] = store.event_sku_lines(event.id)
                    self._send(201, payload)
                    return
                self._send(404, {"error": "not found"})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
            except Exception as exc:  # pragma: no cover
                self._send(500, {"error": str(exc), "trace": traceback.format_exc()})

    return Handler


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    db_path: Optional[Path] = None,
) -> None:
    store = WarehouseStore(db_path or default_db_path())
    store.seed_common_skus()
    handler = make_handler(store)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"[local-api] listening on http://{host}:{port} db={store.db_path}")
    httpd.serve_forever()


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Offline local warehouse API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args(argv)
    serve(host=args.host, port=args.port, db_path=args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
