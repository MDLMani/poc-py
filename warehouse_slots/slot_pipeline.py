"""Cell-band a slot ROI and classify each cell as FILLED / EMPTY / UNREADABLE.

Phase 4: fill_direction knobs + ImageHash fallback only when QR fails on an
occupied cell. Successful QR decode is never replaced by hash.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from .config import DEFAULT_OPTIONS, PipelineOptions, SlotConfig
from .image_hash_match import SkuHashIndex, build_index, match_cell
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
    match_source: Optional[str] = None  # "qr" | "imagehash"
    hash_distance: Optional[int] = None


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

    @property
    def hash_filled(self) -> int:
        return sum(
            1
            for c in self.cells
            if c.status == CellStatus.FILLED and c.match_source == "imagehash"
        )

    def alerts(self) -> List[str]:
        """Human-readable UNREADABLE / hash-fallback notices."""
        msgs: List[str] = []
        for c in self.cells:
            if c.status == CellStatus.UNREADABLE:
                msgs.append(
                    f"ALERT UNREADABLE: slot={self.slot_id} cell={c.index} "
                    f"(occupied, QR failed, no hash match)"
                )
            elif c.match_source == "imagehash" and c.payload:
                msgs.append(
                    f"NOTICE HASH_FALLBACK: slot={self.slot_id} cell={c.index} "
                    f"→ {c.payload} (dist={c.hash_distance})"
                )
        return msgs

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "capacity": self.capacity,
            "counts": dict(self.counts),
            "empty": self.empty,
            "unreadable": self.unreadable,
            "filled": self.filled,
            "hash_filled": self.hash_filled,
            "alerts": self.alerts(),
            "cells": [
                {
                    "index": c.index,
                    "status": c.status.value,
                    "payload": c.payload,
                    "match_source": c.match_source,
                    "hash_distance": c.hash_distance,
                }
                for c in self.cells
            ],
        }


def _cell_bands(
    roi_h: int, capacity: int, fill_direction: str = "top_to_bottom"
) -> List[tuple]:
    """Split [0, roi_h) into `capacity` vertical bands (y0, y1).

    Cell index 0 is at the top for top_to_bottom, or at the bottom for
    bottom_to_top (fill_direction).
    """
    bands: List[tuple] = []
    for i in range(capacity):
        y0 = int(round(i * roi_h / capacity))
        y1 = int(round((i + 1) * roi_h / capacity))
        if y1 <= y0:
            y1 = y0 + 1
        bands.append((y0, y1))
    if fill_direction == "bottom_to_top":
        bands = list(reversed(bands))
    return bands


def _is_occupied(cell_gray: np.ndarray) -> bool:
    """Heuristic: non-white content implies something is in the cell.

    Fixtures use white background; QR modules / labels are dark or colorful.
    Empty cells are near-uniform bright.
    """
    if cell_gray.size == 0:
        return False
    dark = np.count_nonzero(cell_gray < 200)
    frac = dark / cell_gray.size
    std = float(np.std(cell_gray))
    return frac > 0.02 or std > 15.0


def analyze_slot(
    image_bgr: np.ndarray,
    slot: SlotConfig,
    options: Optional[PipelineOptions] = None,
    hash_index: Optional[SkuHashIndex] = None,
) -> SlotResult:
    """Crop slot ROI, band into capacity cells, decode one QR per cell.

    If QR fails on an occupied cell and hash fallback is enabled, try
    ImageHash against local SKU reference images.
    """
    opts = options or DEFAULT_OPTIONS
    if (
        hash_index is None
        and opts.enable_hash_fallback
        and opts.reference_images_dir is not None
    ):
        hash_index = build_index(opts.reference_images_dir)

    x, y, w, h = slot.roi
    img_h, img_w = image_bgr.shape[:2]
    x1 = max(0, min(img_w, x + w))
    y1 = max(0, min(img_h, y + h))
    x0 = max(0, min(img_w, x))
    y0 = max(0, min(img_h, y))
    roi = image_bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return SlotResult(
            slot_id=slot.id,
            capacity=slot.capacity,
            cells=[
                CellResult(index=i, status=CellStatus.EMPTY)
                for i in range(slot.capacity)
            ],
        )

    roi_h, _roi_w = roi.shape[:2]
    bands = _cell_bands(roi_h, slot.capacity, opts.fill_direction)
    cells: List[CellResult] = []

    for i, (cy0, cy1) in enumerate(bands):
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
                CellResult(
                    index=i,
                    status=CellStatus.FILLED,
                    payload=payload,
                    match_source="qr",
                )
            )
            continue

        if not _is_occupied(gray):
            cells.append(CellResult(index=i, status=CellStatus.EMPTY))
            continue

        # Occupied + QR failed → UNREADABLE, optionally ImageHash fallback
        if opts.enable_hash_fallback and hash_index is not None:
            hit = match_cell(cell, hash_index, threshold=opts.hash_threshold)
            if hit is not None:
                cells.append(
                    CellResult(
                        index=i,
                        status=CellStatus.FILLED,
                        payload=hit.sku_id,
                        match_source="imagehash",
                        hash_distance=hit.distance,
                    )
                )
                continue

        cells.append(CellResult(index=i, status=CellStatus.UNREADABLE))

    return SlotResult(slot_id=slot.id, capacity=slot.capacity, cells=cells)


def analyze_image(
    image_bgr: np.ndarray,
    slots: List[SlotConfig],
    options: Optional[PipelineOptions] = None,
    hash_index: Optional[SkuHashIndex] = None,
) -> Dict[str, Any]:
    """Run the pipeline on every configured slot."""
    opts = options or DEFAULT_OPTIONS
    if hash_index is None and opts.enable_hash_fallback:
        hash_index = build_index(opts.reference_images_dir)

    results = [
        analyze_slot(image_bgr, s, options=opts, hash_index=hash_index) for s in slots
    ]
    alerts: List[str] = []
    for r in results:
        alerts.extend(r.alerts())
    return {
        "slots": [r.to_dict() for r in results],
        "alerts": alerts,
        "options": {
            "hash_threshold": opts.hash_threshold,
            "fill_direction": opts.fill_direction,
            "enable_hash_fallback": opts.enable_hash_fallback,
            "reference_images_dir": (
                str(opts.reference_images_dir) if opts.reference_images_dir else None
            ),
            "hash_refs_loaded": len(hash_index) if hash_index is not None else 0,
        },
    }


def format_alerts_text(report: Dict[str, Any]) -> str:
    """CLI/UI helper: multi-line alert block (empty string if none)."""
    alerts = report.get("alerts") or []
    if not alerts:
        return ""
    lines = ["=== UNREADABLE / HASH ALERTS ==="]
    lines.extend(alerts)
    lines.append("=== end alerts ===")
    return "\n".join(lines)
