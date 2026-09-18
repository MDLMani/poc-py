"""Phase 1: EMPTY vs UNREADABLE classification."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import pytest

from warehouse_slots.config import load_slots
from warehouse_slots.generate_fixtures import generate_all
from warehouse_slots.slot_pipeline import CellStatus, analyze_slot

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
CONFIG_DIR = ROOT / "config"


@pytest.fixture(scope="module", autouse=True)
def ensure_fixtures():
    result = generate_all(FIXTURES)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    refs = FIXTURES / "refs"
    for name, slot in result["single_slots"].items():
        cfg = {"slots": [slot]}
        if name in ("F_hash_fallback", "F_unreadable"):
            cfg.update(
                {
                    "hash_threshold": 12,
                    "reference_images_dir": str(refs.resolve()),
                    "enable_hash_fallback": True,
                }
            )
        (CONFIG_DIR / f"{name}.json").write_text(
            json.dumps(cfg, indent=2) + "\n", encoding="utf-8"
        )
    return result


def test_unreadable_vs_empty():
    img = cv2.imread(str(FIXTURES / "F_unreadable.png"), cv2.IMREAD_COLOR)
    assert img is not None
    slots = load_slots(CONFIG_DIR / "F_unreadable.json")
    result = analyze_slot(img, slots[0])
    assert result.capacity == 4
    assert result.filled == 1
    assert result.unreadable == 1
    assert result.empty == 2
    assert dict(result.counts) == {"SKU-UR": 1}
    assert result.cells[0].status == CellStatus.FILLED
    assert result.cells[1].status == CellStatus.UNREADABLE
    assert result.cells[2].status == CellStatus.EMPTY
    assert result.cells[3].status == CellStatus.EMPTY
