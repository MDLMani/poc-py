"""Tests for enhanced offline local REST API endpoints."""

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
import urllib.request
import urllib.error
import pytest

from warehouse_slots.local_api import make_handler
from warehouse_slots.store import WarehouseStore


@pytest.fixture
def test_server(tmp_path):
    db_path = tmp_path / "test_api.db"
    store = WarehouseStore(db_path)
    store.seed_common_skus()
    cfg_path = tmp_path / "slots.json"
    cfg_path.write_text(
        json.dumps({
            "slots": [
                {"id": "F1", "roi": [40, 40, 240, 2400], "capacity": 10},
                {"id": "F2", "roi": [320, 40, 240, 1440], "capacity": 6},
            ]
        }),
        encoding="utf-8",
    )

    handler = make_handler(store, cfg_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_port
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base_url = f"http://127.0.0.1:{port}"
    yield base_url, cfg_path, store
    httpd.shutdown()


def _request_json(url, data=None, method="GET"):
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    body = json.dumps(data).encode("utf-8") if data is not None else None
    with urllib.request.urlopen(req, data=body, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def test_health(test_server):
    base_url, _, _ = test_server
    status, body = _request_json(f"{base_url}/health")
    assert status == 200
    assert body["ok"] is True
    assert body["offline"] is True


def test_get_and_save_config(test_server):
    base_url, _, _ = test_server
    status, body = _request_json(f"{base_url}/api/config")
    assert status == 200
    assert len(body["slots"]) == 2
    assert body["slots"][0]["id"] == "F1"

    # Add a column via API
    add_payload = {
        "id": "COL-X",
        "roi": [600, 40, 240, 1000],
        "capacity": 4,
        "expected_products": ["SKU-ALPHA", "SKU-B", None, None],
    }
    status, body = _request_json(f"{base_url}/api/columns/add", data=add_payload, method="POST")
    assert status == 201
    assert any(s["id"] == "COL-X" for s in body["slots"])

    # Update slot product
    assign_payload = {
        "column_id": "COL-X",
        "slot_index": 2,
        "product_id": "SKU-C",
    }
    status, body = _request_json(f"{base_url}/api/slots/product", data=assign_payload, method="POST")
    assert status == 200
    col_x = next(s for s in body["slots"] if s["id"] == "COL-X")
    assert col_x["expected_products"][2] == "SKU-C"


def test_analyze_endpoint(test_server):
    base_url, cfg_path, _ = test_server
    root = Path(__file__).resolve().parents[1]
    board_img = root / "fixtures" / "board.png"
    if not board_img.is_file():
        pytest.skip("fixtures/board.png not generated")

    payload = {
        "image_path": str(board_img),
    }
    status, body = _request_json(f"{base_url}/api/analyze", data=payload, method="POST")
    assert status == 200
    assert body["ok"] is True
    assert len(body["slots"]) >= 2
    assert "overlay_base64" in body
    assert body["summary"]["total_cells"] > 0
    # Check first slot cells
    slot1 = body["slots"][0]
    assert len(slot1["cells"]) == slot1["capacity"]
    assert "crop_thumbnail" in slot1["cells"][0]
