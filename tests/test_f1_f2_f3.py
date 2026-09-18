"""Acceptance tests for fixtures F1 / F2 / F3."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import pytest

from warehouse_slots.config import load_slots
from warehouse_slots.generate_fixtures import generate_all
from warehouse_slots.slot_pipeline import analyze_image, analyze_slot


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
CONFIG_DIR = ROOT / "config"


@pytest.fixture(scope="session", autouse=True)
def fixtures_and_configs():
    """Generate PNGs + rewrite configs so rois stay aligned (offline)."""
    result = generate_all(FIXTURES)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    board_cfg = {"slots": result["board_slots"]}
    (CONFIG_DIR / "slots.example.json").write_text(
        json.dumps(board_cfg, indent=2) + "\n", encoding="utf-8"
    )
    for name, slot in result["single_slots"].items():
        (CONFIG_DIR / f"{name}.json").write_text(
            json.dumps({"slots": [slot]}, indent=2) + "\n", encoding="utf-8"
        )
    return result


def _analyze_single(name: str):
    img = cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture {name}.png"
    slots = load_slots(CONFIG_DIR / f"{name}.json")
    assert len(slots) == 1
    return analyze_slot(img, slots[0])


def test_f1_same_payload_x10():
    result = _analyze_single("F1")
    assert result.capacity == 10
    assert result.empty == 0
    assert result.unreadable == 0
    assert result.filled == 10
    assert dict(result.counts) == {"SKU-ALPHA": 10}


def test_f2_three_payloads_x2():
    result = _analyze_single("F2")
    assert result.capacity == 6
    assert result.empty == 0
    assert result.unreadable == 0
    assert result.filled == 6
    assert dict(result.counts) == {"SKU-A": 2, "SKU-B": 2, "SKU-C": 2}


def test_f3_three_filled_five_empty():
    result = _analyze_single("F3")
    assert result.capacity == 8
    assert result.filled == 3
    assert result.empty == 5
    assert result.unreadable == 0
    assert dict(result.counts) == {"SKU-X": 1, "SKU-Y": 1, "SKU-Z": 1}


def test_board_all_slots():
    img = cv2.imread(str(FIXTURES / "board.png"), cv2.IMREAD_COLOR)
    assert img is not None
    slots = load_slots(CONFIG_DIR / "slots.example.json")
    report = analyze_image(img, slots)
    by_id = {s["slot_id"]: s for s in report["slots"]}

    assert by_id["F1"]["counts"] == {"SKU-ALPHA": 10}
    assert by_id["F1"]["empty"] == 0

    assert by_id["F2"]["counts"] == {"SKU-A": 2, "SKU-B": 2, "SKU-C": 2}
    assert by_id["F2"]["empty"] == 0

    assert by_id["F3"]["counts"] == {"SKU-X": 1, "SKU-Y": 1, "SKU-Z": 1}
    assert by_id["F3"]["empty"] == 5
    assert by_id["F3"]["unreadable"] == 0
