"""Tests for live rack config tiling and verify PASS/FAIL."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from warehouse_slots.generate_fixtures import (
    CELL_H,
    CELL_W,
    _fit_qr_in_cell,
    _make_qr_image,
)
from warehouse_slots.rack_config import (
    build_irregular_rack,
    build_uniform_rack,
    iter_cell_rois,
    load_rack,
    save_rack,
    apply_preset,
)
from warehouse_slots.rack_pipeline import analyze_and_verify, analyze_rack, verify_rack


def test_uniform_roi_tiling_non_overlap():
    rack = build_uniform_rack(
        rack_id="U",
        store_type="uniform_2x3",
        outer_roi=(10, 20, 300, 200),
        rows=2,
        cols=3,
    )
    cells = list(iter_cell_rois(rack))
    assert len(cells) == 6
    areas = []
    for r, c, (x, y, w, h) in cells:
        assert w >= 1 and h >= 1
        areas.append((r, c, x, y, x + w, y + h))
    # Cover outer bounds roughly
    xs0 = min(a[2] for a in areas)
    ys0 = min(a[3] for a in areas)
    xs1 = max(a[4] for a in areas)
    ys1 = max(a[5] for a in areas)
    assert xs0 == 10 and ys0 == 20
    assert xs1 == 310 and ys1 == 220


def test_irregular_roi_row_counts():
    rack = build_irregular_rack(
        rack_id="I",
        store_type="irregular_5_2",
        outer_roi=(0, 0, 500, 200),
        row_cell_counts=[5, 2],
    )
    cells = list(iter_cell_rois(rack))
    assert len(cells) == 7
    assert sum(1 for r, c, _ in cells if r == 0) == 5
    assert sum(1 for r, c, _ in cells if r == 1) == 2


def test_save_load_roundtrip(tmp_path: Path):
    rack = apply_preset("irregular_5_2", outer_roi=(0, 0, 400, 200))
    rack.cells[0].expected_product_id = "SKU-X"
    path = tmp_path / "rack.json"
    save_rack(rack, path)
    loaded = load_rack(path)
    assert loaded.rack_id == rack.rack_id
    assert loaded.layout.mode == "irregular"
    assert loaded.expected_map()[(0, 0)] == "SKU-X"


def _board_with_qr_grid(rows: int, cols: int) -> tuple[np.ndarray, list]:
    """Build a white board with known QR payloads SKU-r{r}c{c}."""
    board = np.full((rows * CELL_H, cols * CELL_W, 3), 255, dtype=np.uint8)
    expected = []
    for r in range(rows):
        for c in range(cols):
            pid = f"SKU-R{r}C{c}"
            expected.append((r, c, pid))
            cell = _fit_qr_in_cell(_make_qr_image(pid), CELL_W, CELL_H)
            arr = np.array(cell.convert("RGB"))[:, :, ::-1]  # RGB→BGR
            y0, x0 = r * CELL_H, c * CELL_W
            board[y0 : y0 + CELL_H, x0 : x0 + CELL_W] = arr
    return board, expected


def test_analyze_and_verify_pass():
    board, expected = _board_with_qr_grid(2, 2)
    h, w = board.shape[:2]
    exp_map = {(r, c): pid for r, c, pid in expected}
    rack = build_uniform_rack(
        rack_id="T",
        store_type="uniform_2x2",
        outer_roi=(0, 0, w, h),
        rows=2,
        cols=2,
        expected=exp_map,
    )
    out = analyze_and_verify(board, rack)
    assert out["verification"]["summary"]["all_pass"] is True
    assert out["verification"]["summary"]["FAIL"] == 0


def test_verify_fail_wrong_sku():
    board, expected = _board_with_qr_grid(1, 2)
    h, w = board.shape[:2]
    rack = build_uniform_rack(
        rack_id="T",
        store_type="uniform_1x2",
        outer_roi=(0, 0, w, h),
        rows=1,
        cols=2,
        expected={(0, 0): "SKU-WRONG", (0, 1): expected[1][2]},
    )
    actual = analyze_rack(board, rack)
    ver = verify_rack(rack, actual)
    assert ver["summary"]["FAIL"] >= 1
    assert ver["summary"]["all_pass"] is False
    bad = [c for c in ver["cells"] if c["row"] == 0 and c["col"] == 0][0]
    assert bad["verdict"] == "FAIL"


def test_verify_empty_cell_pass():
    # Left cell QR, right cell blank white
    board = np.full((CELL_H, CELL_W * 2, 3), 255, dtype=np.uint8)
    cell = _fit_qr_in_cell(_make_qr_image("SKU-X"), CELL_W, CELL_H)
    arr = np.array(cell.convert("RGB"))[:, :, ::-1]
    board[:, :CELL_W] = arr
    rack = build_uniform_rack(
        rack_id="E",
        store_type="uniform_1x2",
        outer_roi=(0, 0, CELL_W * 2, CELL_H),
        rows=1,
        cols=2,
        expected={(0, 0): "SKU-X", (0, 1): None},
    )
    out = analyze_and_verify(board, rack)
    assert out["verification"]["summary"]["all_pass"] is True
