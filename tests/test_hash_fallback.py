"""Phase 4: ImageHash fallback only when cell is UNREADABLE (QR failed)."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import pytest

from warehouse_slots.config import PipelineOptions, load_config, load_slots
from warehouse_slots.generate_fixtures import generate_all
from warehouse_slots.image_hash_match import SkuHashIndex
from warehouse_slots.slot_pipeline import CellStatus, analyze_image, analyze_slot

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
CONFIG_DIR = ROOT / "config"
REFS = FIXTURES / "refs"


@pytest.fixture(scope="module", autouse=True)
def ensure_fixtures():
    result = generate_all(FIXTURES)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    for name, slot in result["single_slots"].items():
        cfg = {"slots": [slot]}
        if name == "F_hash_fallback":
            cfg.update(
                {
                    "hash_threshold": 12,
                    "fill_direction": "top_to_bottom",
                    "reference_images_dir": str(REFS),
                    "enable_hash_fallback": True,
                }
            )
        (CONFIG_DIR / f"{name}.json").write_text(
            json.dumps(cfg, indent=2) + "\n", encoding="utf-8"
        )
    # Unreadable config without matching refs for noise
    ur = result["single_slots"]["F_unreadable"]
    (CONFIG_DIR / "F_unreadable.json").write_text(
        json.dumps(
            {
                "slots": [ur],
                "hash_threshold": 12,
                "reference_images_dir": str(REFS),
                "enable_hash_fallback": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return result


def test_hash_fallback_fills_visual_cell():
    img = cv2.imread(str(FIXTURES / "F_hash_fallback.png"), cv2.IMREAD_COLOR)
    assert img is not None
    slots = load_slots(CONFIG_DIR / "F_hash_fallback.json")
    options = PipelineOptions(
        hash_threshold=12,
        fill_direction="top_to_bottom",
        reference_images_dir=REFS,
        enable_hash_fallback=True,
    )
    assert options.enable_hash_fallback
    assert options.reference_images_dir is not None
    result = analyze_slot(img, slots[0], options=options)
    assert result.capacity == 4
    assert result.filled == 1
    assert result.unreadable == 0
    assert result.empty == 3
    assert result.cells[0].status == CellStatus.FILLED
    assert result.cells[0].payload == "SKU-VISUAL"
    assert result.cells[0].match_source == "imagehash"
    assert result.cells[0].hash_distance is not None
    assert result.cells[0].hash_distance <= options.hash_threshold
    assert result.hash_filled == 1
    assert any("HASH_FALLBACK" in a for a in result.alerts())


def test_noise_stays_unreadable_even_with_refs():
    """Noise blotch must not falsely match SKU-VISUAL / SKU-ALPHA refs."""
    img = cv2.imread(str(FIXTURES / "F_unreadable.png"), cv2.IMREAD_COLOR)
    assert img is not None
    slots, options = load_config(CONFIG_DIR / "F_unreadable.json")
    result = analyze_slot(img, slots[0], options=options)
    assert result.filled == 1
    assert result.unreadable == 1
    assert result.empty == 2
    assert result.cells[0].match_source == "qr"
    assert result.cells[0].payload == "SKU-UR"
    assert result.cells[1].status == CellStatus.UNREADABLE
    assert result.cells[1].match_source is None
    assert any("UNREADABLE" in a for a in result.alerts())


def test_qr_success_never_replaced_by_hash():
    """When QR decodes, match_source stays qr even if refs exist."""
    img = cv2.imread(str(FIXTURES / "F1.png"), cv2.IMREAD_COLOR)
    assert img is not None
    slots = load_slots(CONFIG_DIR / "F1.json")
    options = PipelineOptions(
        hash_threshold=64,  # extremely loose — would match almost anything
        fill_direction="top_to_bottom",
        reference_images_dir=REFS,
        enable_hash_fallback=True,
    )
    index = SkuHashIndex.load(REFS)
    assert len(index) >= 1
    result = analyze_slot(img, slots[0], options=options, hash_index=index)
    assert result.filled == 10
    assert result.unreadable == 0
    assert all(c.match_source == "qr" for c in result.cells)
    assert all(c.payload == "SKU-ALPHA" for c in result.cells)


def test_hash_fallback_disabled():
    img = cv2.imread(str(FIXTURES / "F_hash_fallback.png"), cv2.IMREAD_COLOR)
    slots = load_slots(CONFIG_DIR / "F_hash_fallback.json")
    options = PipelineOptions(
        hash_threshold=12,
        reference_images_dir=REFS,
        enable_hash_fallback=False,
    )
    result = analyze_slot(img, slots[0], options=options)
    assert result.unreadable == 1
    assert result.filled == 0
    assert result.cells[0].status == CellStatus.UNREADABLE


def test_fill_direction_bottom_to_top_orders_cells():
    img = cv2.imread(str(FIXTURES / "F_hash_fallback.png"), cv2.IMREAD_COLOR)
    slots = load_slots(CONFIG_DIR / "F_hash_fallback.json")
    # Visual label is physically at the top of the ROI.
    # With bottom_to_top, top band becomes the last cell index.
    options = PipelineOptions(
        hash_threshold=12,
        fill_direction="bottom_to_top",
        reference_images_dir=REFS,
        enable_hash_fallback=True,
    )
    result = analyze_slot(img, slots[0], options=options)
    assert result.cells[-1].status == CellStatus.FILLED
    assert result.cells[-1].payload == "SKU-VISUAL"
    assert result.cells[-1].match_source == "imagehash"
    assert result.cells[0].status == CellStatus.EMPTY


def test_analyze_image_report_includes_alerts_and_options():
    img = cv2.imread(str(FIXTURES / "F_hash_fallback.png"), cv2.IMREAD_COLOR)
    slots, options = load_config(CONFIG_DIR / "F_hash_fallback.json")
    report = analyze_image(img, slots, options=options)
    assert "alerts" in report
    assert "options" in report
    assert report["options"]["hash_refs_loaded"] >= 1
    assert report["slots"][0]["counts"].get("SKU-VISUAL") == 1
