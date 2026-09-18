"""Phase 4: hardened QR decode still reads clean fixtures; survives mild warp."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from warehouse_slots.generate_fixtures import generate_all
from warehouse_slots.qr_detect import decode_qr

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


@pytest.fixture(scope="module", autouse=True)
def ensure_fixtures():
    generate_all(FIXTURES)


def test_decode_clean_qr_cell():
    img = cv2.imread(str(FIXTURES / "F1.png"), cv2.IMREAD_COLOR)
    assert img is not None
    # First cell band ≈ y 40..280, x 40..280
    cell = img[40:280, 40:280]
    assert decode_qr(cell) == "SKU-ALPHA"


def test_decode_small_downscale():
    img = cv2.imread(str(FIXTURES / "F1.png"), cv2.IMREAD_COLOR)
    cell = img[40:280, 40:280]
    small = cv2.resize(cell, (80, 80), interpolation=cv2.INTER_AREA)
    assert decode_qr(small) == "SKU-ALPHA"


def test_decode_mild_rotation():
    img = cv2.imread(str(FIXTURES / "F1.png"), cv2.IMREAD_COLOR)
    cell = img[40:280, 40:280]
    h, w = cell.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), 10, 1.0)
    rotated = cv2.warpAffine(cell, m, (w, h), borderValue=(255, 255, 255))
    assert decode_qr(rotated) == "SKU-ALPHA"


def test_decode_glare_boost():
    """Simulate washed-out glare by lifting midtones; CLAHE path should recover."""
    img = cv2.imread(str(FIXTURES / "F1.png"), cv2.IMREAD_COLOR)
    cell = img[40:280, 40:280].astype(np.float32)
    cell = np.clip(cell * 0.55 + 110, 0, 255).astype(np.uint8)
    assert decode_qr(cell) == "SKU-ALPHA"
