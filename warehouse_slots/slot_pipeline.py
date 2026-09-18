"""Cell-band a slot ROI and classify each cell as FILLED / EMPTY / UNREADABLE."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from .config import SlotConfig
from .qr_detect import decode_qr


class CellStatus(str, Enum):
    FILLED = "FILLED"
    EMPTY = "EMPTY"
    UNREADABLE = "UNREADABLE"


@dataclass
class CellResult:
    index: int
    status: CellStatus
    payload: Optional[str] = None


@dataclass
class SlotResult:
    slot_id: str
    capacity: int
    cells: List[CellResult] = field(default_factory=list)

    @property
    def counts(self) -> Counter:
        c: Counter = Counter()
        for cell in self.cells:
            if cell.status == CellStatus.FILLED and cell.payload is not None:
                c[cell.payload] += 1
        return c

    @property
    def filled(self) -> int:
        return sum(1 for c in self.cells if c.status == CellStatus.FILLED)

    @property
    def empty(self) -> int:
        return sum(1 for c in self.cells if c.status == CellStatus.EMPTY)

    @property
    def unreadable(self) -> int:
        return sum(1 for c in self.cells if c.status == CellStatus.UNREADABLE)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "capacity": self.capacity,
            "counts": dict(self.counts),
            "empty": self.empty,
            "unreadable": self.unreadable,
            "filled": self.filled,
            "cells": [
                {
                    "index": c.index,
                    "status": c.status.value,
                    "payload": c.payload,
                }
                for c in self.cells
            ],
        }


def _cell_bands(roi_h: int, capacity: int) -> List[tuple]:
    """Split [0, roi_h) into `capacity` vertical bands (y0, y1).

    Prefer cell-banding: one expected QR per horizontal row-band
    within the slot ROI (top-to-bottom stacking).
    """
    bands: List[tuple] = []
    for i in range(capacity):
        y0 = int(round(i * roi_h / capacity))
        y1 = int(round((i + 1) * roi_h / capacity))
        if y1 <= y0:
            y1 = y0 + 1
        bands.append((y0, y1))
    return bands


def _is_occupied(cell_gray: np.ndarray) -> bool:
    """Heuristic: non-white content implies something is in the cell.

    Fixtures use white background; QR modules are dark. Empty cells are
    near-uniform bright.
    """
    if cell_gray.size == 0:
        return False
    # Fraction of dark-ish pixels
    dark = np.count_nonzero(cell_gray < 200)
    frac = dark / cell_gray.size
    # Also check std-dev — empty white is low variance
    std = float(np.std(cell_gray))
    return frac > 0.02 or std > 15.0


def analyze_slot(image_bgr: np.ndarray, slot: SlotConfig) -> SlotResult:
    """Crop slot ROI, band into capacity cells, decode one QR per cell."""
    x, y, w, h = slot.roi
    img_h, img_w = image_bgr.shape[:2]
    x1 = max(0, min(img_w, x + w))
    y1 = max(0, min(img_h, y + h))
    x0 = max(0, min(img_w, x))
    y0 = max(0, min(img_h, y))
    roi = image_bgr[y0:y1, x0:x1]
    if roi.size == 0:
        # Entire ROI off-image — treat all as empty
        return SlotResult(
            slot_id=slot.id,
            capacity=slot.capacity,
            cells=[
                CellResult(index=i, status=CellStatus.EMPTY)
                for i in range(slot.capacity)
            ],
        )

    roi_h, roi_w = roi.shape[:2]
    bands = _cell_bands(roi_h, slot.capacity)
    cells: List[CellResult] = []

    for i, (cy0, cy1) in enumerate(bands):
        # Small inset to avoid shared borders between adjacent QRs
        inset = max(1, (cy1 - cy0) // 20)
        cell = roi[cy0 + inset : max(cy0 + inset + 1, cy1 - inset), :]
        if cell.size == 0:
            cell = roi[cy0:cy1, :]

        if cell.ndim == 3:
            gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
        else:
            gray = cell

        payload = decode_qr(cell)
        if payload:
            cells.append(
                CellResult(index=i, status=CellStatus.FILLED, payload=payload)
            )
        elif _is_occupied(gray):
            cells.append(CellResult(index=i, status=CellStatus.UNREADABLE))
        else:
            cells.append(CellResult(index=i, status=CellStatus.EMPTY))

    return SlotResult(slot_id=slot.id, capacity=slot.capacity, cells=cells)


def analyze_image(
    image_bgr: np.ndarray, slots: List[SlotConfig]
) -> Dict[str, Any]:
    """Run the pipeline on every configured slot."""
    results = [analyze_slot(image_bgr, s) for s in slots]
    return {
        "slots": [r.to_dict() for r in results],
    }
