"""Local offline HTTP API for warehouse slots management and analysis.

Serves REST endpoints for:
  - Rack / Column / Slot configuration (add/remove column, assign products in slots)
  - Product (SKU) catalog and on-hand stock availability
  - Offline image analysis with OpenCV QR detection and cell crop previews
  - Inventory confirmation (Confirm IN / OUT) and audit events
  - Fixtures list and static image preview
"""

from __future__ import annotations

import base64
import json
import mimetypes
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type
from urllib.parse import parse_qs, unquote, urlparse

import cv2
import numpy as np

from .availability import check_availability
from .config import PipelineOptions, SlotConfig, load_config, load_slots
from .slot_pipeline import CellStatus, _cell_bands, analyze_image
from .store import WarehouseStore, default_db_path
from .ui_app import draw_overlays


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT_DIR / "config" / "slots.json"
EXAMPLE_CONFIG_PATH = ROOT_DIR / "config" / "slots.example.json"


def _get_active_config_path(explicit_path: Optional[Path] = None) -> Path:
    if explicit_path and explicit_path.is_file():
        return explicit_path
    if DEFAULT_CONFIG_PATH.is_file():
        return DEFAULT_CONFIG_PATH
    if EXAMPLE_CONFIG_PATH.is_file():
        return EXAMPLE_CONFIG_PATH
    return DEFAULT_CONFIG_PATH


def _load_current_config(config_path: Optional[Path] = None) -> Tuple[List[SlotConfig], PipelineOptions, Path]:
    target = _get_active_config_path(config_path)
    if not target.is_file() and EXAMPLE_CONFIG_PATH.is_file():
        target = EXAMPLE_CONFIG_PATH
    slots, options = load_config(target)
    return slots, options, target


def _save_current_config(slots: List[SlotConfig], options: Optional[PipelineOptions] = None, path: Optional[Path] = None) -> Path:
    target = path or DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    opts = options or PipelineOptions()
    data = {
        "hash_threshold": opts.hash_threshold,
        "fill_direction": opts.fill_direction,
        "enable_hash_fallback": opts.enable_hash_fallback,
        "reference_images_dir": str(opts.reference_images_dir) if opts.reference_images_dir else "refs",
        "slots": [s.to_dict() for s in slots],
    }
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return target


def _encode_bgr_to_base64_jpeg(img_bgr: np.ndarray, quality: int = 80) -> str:
    success, encoded = cv2.imencode(".jpg", img_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not success:
        return ""
    b64 = base64.b64encode(encoded).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _read_json(handler: BaseHTTPRequestHandler) -> Any:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b"{}"
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def make_handler(store: WarehouseStore, config_path: Optional[Path] = None) -> Type[BaseHTTPRequestHandler]:
    active_cfg_ref = [config_path]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            # Quiet log
            print(f"[local-api] {self.address_string()} - {fmt % args}")

        def _send_cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._send_cors_headers()
            self.end_headers()

        def _send(self, code: int, body: Any) -> None:
            data = json.dumps(body, indent=2, sort_keys=True).encode("utf-8")
            self.send_response(code)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_binary(self, code: int, content_type: str, data: bytes) -> None:
            self.send_response(code)
            self._send_cors_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            try:
                parsed = urlparse(self.path)
                path = parsed.path.rstrip("/") or "/"
                qs = parse_qs(parsed.query)

                if path == "/health":
                    self._send(200, {"ok": True, "offline": True, "version": "1.0.0"})
                    return

                # --- Image file serving ---
                if path == "/api/image":
                    raw_img_path = (qs.get("path") or [""])[0]
                    if not raw_img_path:
                        self._send(400, {"error": "path parameter is required"})
                        return
                    img_file = Path(raw_img_path)
                    if not img_file.is_absolute():
                        img_file = (ROOT_DIR / img_file).resolve()
                    if not img_file.is_file():
                        self._send(404, {"error": f"image not found: {img_file}"})
                        return
                    mime, _ = mimetypes.guess_type(str(img_file))
                    mime = mime or "image/png"
                    self._send_binary(200, mime, img_file.read_bytes())
                    return

                # --- Fixtures list ---
                if path in ("/api/fixtures", "/fixtures"):
                    fixtures_dir = ROOT_DIR / "fixtures"
                    fixtures = []
                    if fixtures_dir.is_dir():
                        for item in sorted(fixtures_dir.glob("*.png")):
                            fixtures.append({
                                "name": item.name,
                                "path": str(item.relative_to(ROOT_DIR)),
                                "absolute_path": str(item.resolve()),
                                "size_bytes": item.stat().st_size,
                            })
                    self._send(200, {"fixtures": fixtures})
                    return

                # --- Column & Slot Configuration ---
                if path in ("/api/config", "/config"):
                    slots, options, cfg_path = _load_current_config(active_cfg_ref[0])
                    self._send(200, {
                        "config_path": str(cfg_path),
                        "slots": [s.to_dict() for s in slots],
                        "options": {
                            "hash_threshold": options.hash_threshold,
                            "fill_direction": options.fill_direction,
                            "enable_hash_fallback": options.enable_hash_fallback,
                            "reference_images_dir": str(options.reference_images_dir) if options.reference_images_dir else None,
                        }
                    })
                    return

                # --- SKUs ---
                if path in ("/api/skus", "/skus"):
                    self._send(200, {"skus": [s.to_dict() for s in store.list_skus()]})
                    return

                if path.startswith("/api/skus/") or path.startswith("/skus/"):
                    sku_id = path.split("/", 3)[-1]
                    sku = store.get_sku(sku_id)
                    if sku is None:
                        self._send(404, {"error": "sku not found"})
                        return
                    self._send(200, sku.to_dict())
                    return

                # --- Stock & Availability ---
                if path in ("/api/stock", "/stock"):
                    on_hand = store.on_hand()
                    skus = store.list_skus()
                    items = []
                    sku_map = {s.sku_id: s for s in skus}
                    all_ids = sorted(set(sku_map.keys()) | set(on_hand.keys()))
                    for sid in all_ids:
                        sku = sku_map.get(sid)
                        qty = on_hand.get(sid, 0)
                        items.append({
                            "sku_id": sid,
                            "name": sku.name if sku else sid,
                            "description": sku.description if sku else "",
                            "on_hand": qty,
                            "status": "OK" if qty > 0 else "OUT_OF_STOCK"
                        })
                    self._send(200, {"stock": items})
                    return

                # --- Events ---
                if path in ("/api/events", "/events"):
                    limit = int((qs.get("limit") or ["100"])[0])
                    events = [e.to_dict() for e in store.list_events(limit=limit)]
                    for e in events:
                        e["sku_lines"] = store.event_sku_lines(e["id"])
                    self._send(200, {"events": events})
                    return

                self._send(404, {"error": f"Endpoint not found: {path}"})
            except Exception as exc:
                self._send(500, {"error": str(exc), "trace": traceback.format_exc()})

        def do_POST(self) -> None:  # noqa: N802
            try:
                parsed = urlparse(self.path)
                path = parsed.path.rstrip("/") or "/"
                body = _read_json(self)

                # --- Save entire config ---
                if path in ("/api/config", "/config"):
                    raw_slots = body.get("slots") or []
                    if not raw_slots:
                        self._send(400, {"error": "slots list cannot be empty"})
                        return
                    slots = []
                    for r in raw_slots:
                        roi = r.get("roi")
                        exp = r.get("expected_products")
                        exp_list = [str(x) if x is not None else None for x in exp] if isinstance(exp, list) else None
                        slots.append(SlotConfig(
                            id=str(r["id"]),
                            roi=(int(roi[0]), int(roi[1]), int(roi[2]), int(roi[3])),
                            capacity=int(r["capacity"]),
                            expected_products=exp_list,
                        ))
                    opts_raw = body.get("options") or {}
                    opts = PipelineOptions(
                        hash_threshold=int(opts_raw.get("hash_threshold", 12)),
                        fill_direction=str(opts_raw.get("fill_direction", "top_to_bottom")),
                        enable_hash_fallback=bool(opts_raw.get("enable_hash_fallback", True)),
                    )
                    saved_path = _save_current_config(slots, opts, active_cfg_ref[0] or DEFAULT_CONFIG_PATH)
                    active_cfg_ref[0] = saved_path
                    self._send(200, {
                        "ok": True,
                        "message": f"Saved {len(slots)} slots to {saved_path.name}",
                        "config_path": str(saved_path),
                        "slots": [s.to_dict() for s in slots]
                    })
                    return

                # --- Add a column ---
                if path in ("/api/columns/add", "/columns/add"):
                    col_id = str(body.get("id") or "").strip()
                    if not col_id:
                        self._send(400, {"error": "Column id is required"})
                        return
                    roi = body.get("roi") or [40, 40, 240, 1440]
                    capacity = int(body.get("capacity") or 6)
                    expected = body.get("expected_products")
                    exp_list = [str(x) if x is not None else None for x in expected] if isinstance(expected, list) else None

                    slots, options, _ = _load_current_config(active_cfg_ref[0])
                    # If column id already exists, update it, otherwise append
                    new_slots = [s for s in slots if s.id != col_id]
                    new_slots.append(SlotConfig(
                        id=col_id,
                        roi=(int(roi[0]), int(roi[1]), int(roi[2]), int(roi[3])),
                        capacity=capacity,
                        expected_products=exp_list,
                    ))
                    saved_path = _save_current_config(new_slots, options, active_cfg_ref[0] or DEFAULT_CONFIG_PATH)
                    active_cfg_ref[0] = saved_path
                    self._send(201, {
                        "ok": True,
                        "message": f"Column {col_id} added/updated",
                        "slots": [s.to_dict() for s in new_slots]
                    })
                    return

                # --- Delete a column ---
                if path in ("/api/columns/delete", "/columns/delete") or path.startswith("/api/columns/"):
                    col_id = body.get("id") or path.split("/")[-1]
                    col_id = str(col_id).strip()
                    slots, options, _ = _load_current_config(active_cfg_ref[0])
                    updated_slots = [s for s in slots if s.id != col_id]
                    if len(updated_slots) == len(slots):
                        self._send(404, {"error": f"Column {col_id} not found"})
                        return
                    saved_path = _save_current_config(updated_slots, options, active_cfg_ref[0] or DEFAULT_CONFIG_PATH)
                    active_cfg_ref[0] = saved_path
                    self._send(200, {
                        "ok": True,
                        "message": f"Column {col_id} removed",
                        "slots": [s.to_dict() for s in updated_slots]
                    })
                    return

                # --- Assign product in slot ---
                if path in ("/api/slots/product", "/slots/product"):
                    col_id = str(body.get("column_id") or "").strip()
                    slot_index = int(body.get("slot_index") if body.get("slot_index") is not None else -1)
                    product_id = body.get("product_id")
                    if product_id is not None:
                        product_id = str(product_id).strip() or None

                    if not col_id or slot_index < 0:
                        self._send(400, {"error": "column_id and valid slot_index are required"})
                        return

                    slots, options, _ = _load_current_config(active_cfg_ref[0])
                    found = False
                    updated_slots = []
                    for s in slots:
                        if s.id == col_id:
                            found = True
                            if slot_index >= s.capacity:
                                self._send(400, {"error": f"slot_index {slot_index} exceeds capacity {s.capacity}"})
                                return
                            curr_exp = list(s.expected_products) if s.expected_products else [None] * s.capacity
                            while len(curr_exp) < s.capacity:
                                curr_exp.append(None)
                            curr_exp[slot_index] = product_id
                            updated_slots.append(SlotConfig(
                                id=s.id,
                                roi=s.roi,
                                capacity=s.capacity,
                                expected_products=curr_exp,
                            ))
                        else:
                            updated_slots.append(s)

                    if not found:
                        self._send(404, {"error": f"Column {col_id} not found"})
                        return

                    saved_path = _save_current_config(updated_slots, options, active_cfg_ref[0] or DEFAULT_CONFIG_PATH)
                    active_cfg_ref[0] = saved_path
                    self._send(200, {
                        "ok": True,
                        "message": f"Assigned product {product_id or '(empty)'} to {col_id}[{slot_index}]",
                        "slots": [s.to_dict() for s in updated_slots]
                    })
                    return

                # --- Run Slot Analysis with Cropped Previews & Verification ---
                if path in ("/api/analyze", "/analyze"):
                    img_path_str = str(body.get("image_path") or "").strip()
                    if not img_path_str:
                        self._send(400, {"error": "image_path is required"})
                        return

                    img_file = Path(img_path_str)
                    if not img_file.is_absolute():
                        img_file = (ROOT_DIR / img_file).resolve()
                    if not img_file.is_file():
                        self._send(404, {"error": f"Image file not found: {img_file}"})
                        return

                    slots, options, _ = _load_current_config(active_cfg_ref[0])
                    image_bgr = cv2.imread(str(img_file), cv2.IMREAD_COLOR)
                    if image_bgr is None:
                        self._send(400, {"error": f"Could not decode image at {img_file}"})
                        return

                    img_h, img_w = image_bgr.shape[:2]
                    raw_report = analyze_image(image_bgr, slots, options=options)
                    overlay_bgr = draw_overlays(image_bgr, slots, raw_report)
                    overlay_base64 = _encode_bgr_to_base64_jpeg(overlay_bgr, quality=85)

                    # Build detailed flow information for each slot & cell
                    detailed_slots = []
                    total_cells = 0
                    total_filled = 0
                    total_empty = 0
                    total_unreadable = 0
                    total_matches = 0
                    total_mismatches = 0

                    for s in slots:
                        matching_raw = next((r for r in raw_report["slots"] if r["slot_id"] == s.id), None)
                        x, y, w, h = s.roi
                        x0 = max(0, min(img_w, x))
                        y0 = max(0, min(img_h, y))
                        x1 = max(0, min(img_w, x + w))
                        y1 = max(0, min(img_h, y + h))
                        col_crop = image_bgr[y0:y1, x0:x1]
                        bands = _cell_bands(max(1, y1 - y0), s.capacity, options.fill_direction)

                        cells_data = []
                        raw_cells = (matching_raw.get("cells") if matching_raw else []) or []
                        expected_list = s.expected_products or [None] * s.capacity

                        for idx in range(s.capacity):
                            total_cells += 1
                            raw_c = next((c for c in raw_cells if c["index"] == idx), None)
                            status = raw_c["status"] if raw_c else "EMPTY"
                            payload = raw_c.get("payload") if raw_c else None
                            source = raw_c.get("match_source") if raw_c else None
                            dist = raw_c.get("hash_distance") if raw_c else None
                            expected_sku = expected_list[idx] if idx < len(expected_list) else None

                            # Cell crop
                            cy0, cy1 = bands[idx]
                            cell_img = col_crop[cy0:cy1, :] if col_crop.size > 0 else np.zeros((10, 10, 3), dtype=np.uint8)
                            cell_base64 = _encode_bgr_to_base64_jpeg(cell_img, quality=80)

                            # Determine flow verdict
                            if status == "FILLED":
                                total_filled += 1
                                if expected_sku is None:
                                    verdict = "MATCH"
                                    verdict_label = "Occupied (No expected SKU set)"
                                elif payload == expected_sku:
                                    verdict = "MATCH"
                                    verdict_label = f"Match ({payload})"
                                    total_matches += 1
                                else:
                                    verdict = "MISMATCH"
                                    verdict_label = f"Mismatch (Expected {expected_sku}, got {payload})"
                                    total_mismatches += 1
                            elif status == "EMPTY":
                                total_empty += 1
                                if expected_sku is None:
                                    verdict = "EMPTY"
                                    verdict_label = "Empty as expected"
                                    total_matches += 1
                                else:
                                    verdict = "MISSING"
                                    verdict_label = f"Empty (Expected {expected_sku})"
                                    total_mismatches += 1
                            else:  # UNREADABLE
                                total_unreadable += 1
                                verdict = "UNREADABLE"
                                verdict_label = "Occupied, QR unreadable"

                            cells_data.append({
                                "index": idx,
                                "status": status,
                                "payload": payload,
                                "match_source": source,
                                "hash_distance": dist,
                                "expected_product": expected_sku,
                                "verdict": verdict,
                                "verdict_label": verdict_label,
                                "crop_thumbnail": cell_base64,
                            })

                        detailed_slots.append({
                            "slot_id": s.id,
                            "capacity": s.capacity,
                            "roi": list(s.roi),
                            "filled": matching_raw.get("filled", 0) if matching_raw else 0,
                            "empty": matching_raw.get("empty", 0) if matching_raw else 0,
                            "unreadable": matching_raw.get("unreadable", 0) if matching_raw else 0,
                            "counts": matching_raw.get("counts", {}) if matching_raw else {},
                            "cells": cells_data,
                        })

                        detected_counts: Dict[str, int] = {}
                        for c in cells_data:
                            if c["payload"]:
                                detected_counts[c["payload"]] = detected_counts.get(c["payload"], 0) + 1

                    self._send(200, {
                        "ok": True,
                        "image_path": str(img_file),
                        "image_name": img_file.name,
                        "image_width": img_w,
                        "image_height": img_h,
                        "overlay_base64": overlay_base64,
                        "slots": detailed_slots,
                        "alerts": raw_report.get("alerts") or [],
                        "summary": {
                            "total_cells": total_cells,
                            "filled": total_filled,
                            "empty": total_empty,
                            "unreadable": total_unreadable,
                            "matches": total_matches,
                            "mismatches": total_mismatches,
                        }
                    })
                    return

                # --- SKU catalog endpoints ---
                if path in ("/api/skus", "/skus"):
                    sku = store.upsert_sku(
                        body.get("sku_id", ""),
                        body.get("name", ""),
                        body.get("description", ""),
                    )
                    self._send(201, sku.to_dict())
                    return

                if path in ("/api/skus/seed", "/skus/seed"):
                    added = store.seed_common_skus()
                    self._send(200, {"added": added, "skus": [s.to_dict() for s in store.list_skus()]})
                    return

                # --- Inventory confirm IN/OUT ---
                if path in ("/api/confirm/in", "/api/confirm/out", "/confirm/in", "/confirm/out"):
                    direction = "IN" if path.endswith("in") else "OUT"
                    image_path = body.get("image_path") or "manual"
                    scan_payload = body.get("scan") or {}
                    note = body.get("note") or ""
                    sku_qtys = body.get("sku_qtys")
                    event = store.record_event(
                        direction=direction,
                        image_path=image_path,
                        scan=scan_payload,
                        note=note,
                        sku_qtys=sku_qtys,
                    )
                    payload = event.to_dict()
                    payload["sku_lines"] = store.event_sku_lines(event.id)
                    self._send(201, payload)
                    return

                self._send(404, {"error": f"Endpoint not found: {path}"})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
            except Exception as exc:
                self._send(500, {"error": str(exc), "trace": traceback.format_exc()})

    return Handler


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    db_path: Optional[Path] = None,
    config_path: Optional[Path] = None,
) -> None:
    store = WarehouseStore(db_path or default_db_path())
    store.seed_common_skus()
    handler = make_handler(store, config_path)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"[local-api] listening on http://{host}:{port} db={store.db_path}")
    httpd.serve_forever()


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Offline local warehouse API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    serve(host=args.host, port=args.port, db_path=args.db, config_path=args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
