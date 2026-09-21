"""Phase 2: SQLite catalog + IN/OUT events."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import pytest

from warehouse_slots.config import load_slots
from warehouse_slots.generate_fixtures import generate_all
from warehouse_slots.slot_pipeline import analyze_image
from warehouse_slots.store import WarehouseStore

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
CONFIG_DIR = ROOT / "config"


@pytest.fixture
def store(tmp_path: Path) -> WarehouseStore:
    return WarehouseStore(tmp_path / "test.db")


@pytest.fixture(scope="module")
def board_scan():
    generate_all(FIXTURES)
    img = cv2.imread(str(FIXTURES / "board.png"), cv2.IMREAD_COLOR)
    assert img is not None
    # Ensure board config exists
    result = generate_all(FIXTURES)
    (CONFIG_DIR / "slots.example.json").write_text(
        json.dumps({"slots": result["board_slots"]}, indent=2) + "\n",
        encoding="utf-8",
    )
    slots = load_slots(CONFIG_DIR / "slots.example.json")
    return analyze_image(img, slots)


def test_upsert_and_list_skus(store: WarehouseStore):
    store.upsert_sku("SKU-ALPHA", "Alpha", "desc")
    store.upsert_sku("SKU-ALPHA", "Alpha v2", "updated")
    skus = store.list_skus()
    assert len(skus) == 1
    assert skus[0].name == "Alpha v2"
    assert store.get_sku("SKU-ALPHA") is not None
    assert store.get_sku("MISSING") is None


def test_seed_common_skus(store: WarehouseStore):
    n = store.seed_common_skus()
    assert n >= 7
    assert store.seed_common_skus() == 0  # idempotent


def test_record_in_out_with_scan(store: WarehouseStore, board_scan, tmp_path: Path):
    img_path = tmp_path / "capture.png"
    img_path.write_bytes(b"fake")  # path only; scan provided
    ev_in = store.record_event(
        "IN", img_path, scan=board_scan, note="inbound board"
    )
    assert ev_in.direction == "IN"
    assert ev_in.image_path == str(img_path)
    lines = store.event_sku_lines(ev_in.id)
    sku_map = {x["sku_id"]: x["qty"] for x in lines}
    assert sku_map["SKU-ALPHA"] == 12  # F1×10 + F4×2
    assert sku_map["SKU-COMP"] == 4
    assert sku_map["SKU-A"] == 2
    assert sku_map["SKU-X"] == 1

    ev_out = store.record_event("OUT", img_path, scan=board_scan)
    assert ev_out.direction == "OUT"
    events = store.list_events()
    assert len(events) == 2
    assert {e.direction for e in events} == {"IN", "OUT"}

    hist = store.sku_history("SKU-ALPHA")
    assert len(hist) >= 2
    assert hist[0]["direction"] == "OUT"
    assert hist[0]["qty"] == 12
    assert hist[1]["direction"] == "IN"
    assert hist[1]["qty"] == 12


def test_invalid_direction(store: WarehouseStore, tmp_path: Path):
    with pytest.raises(ValueError):
        store.record_event("SIDEWAYS", tmp_path / "x.png", scan={})
