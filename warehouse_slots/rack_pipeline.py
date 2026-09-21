"""Analyze live rack cells and verify expected product IDs."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .qr_detect import decode_qr
from .rack_config import RackConfig, iter_cell_rois
from .slot_pipeline import _is_occupied


def _crop(image_bgr: np.ndarray, roi: Tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = roi
    img_h, img_w = image_bgr.shape[:2]
    x0 = max(0, min(img_w, x))
    y0 = max(0, min(img_h, y))
    x1 = max(0, min(img_w, x + w))
    y1 = max(0, min(img_h, y + h))
    return image_bgr[y0:y1, x0:x1]


def analyze_rack_cell(
    image_bgr: np.ndarray,
    roi: Tuple[int, int, int, int],
) -> Dict[str, Any]:
    """Classify one cell: EMPTY / FILLED(+payload) / UNREADABLE."""
    crop = _crop(image_bgr, roi)
    if crop.size == 0:
        return {
            "status": "EMPTY",
            "product_id": None,
            "match_source": None,
        }
    if crop.ndim == 3:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    else:
        gray = crop

    payload = decode_qr(crop)
    if payload:
        return {
            "status": "FILLED",
            "product_id": str(payload).strip(),
            "match_source": "qr",
        }
    if not _is_occupied(gray):
        return {
            "status": "EMPTY",
            "product_id": None,
            "match_source": None,
        }
    return {
        "status": "UNREADABLE",
        "product_id": None,
        "match_source": None,
    }


def analyze_rack(image_bgr: np.ndarray, rack: RackConfig) -> Dict[str, Any]:
    """Decode every cell ROI; return structured report."""
    cells: List[Dict[str, Any]] = []
    for row, col, roi in iter_cell_rois(rack, image_shape=image_bgr.shape):
        result = analyze_rack_cell(image_bgr, roi)
        cells.append(
            {
                "row": row,
                "col": col,
                "roi": list(roi),
                "status": result["status"],
                "product_id": result["product_id"],
                "match_source": result["match_source"],
            }
        )
    return {
        "rack_id": rack.rack_id,
        "store_type": rack.store_type,
        "outer_roi": list(rack.outer_roi),
        "cells": cells,
    }


def _norm_pid(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def verify_rack(
    rack: RackConfig,
    actual: Dict[str, Any],
) -> Dict[str, Any]:
    """Compare expected product ids vs analyze_rack output.

    PASS when:
      - expected empty  <=> actual EMPTY
      - expected SKU    <=> actual FILLED with same product_id
    FAIL otherwise (wrong SKU, unexpected fill, miss, unreadable when expected SKU).
    """
    expected = rack.expected_map()
    by_pos = {
        (int(c["row"]), int(c["col"])): c for c in (actual.get("cells") or [])
    }
    rows: List[Dict[str, Any]] = []
    pass_n = fail_n = 0

    positions = sorted(set(expected.keys()) | set(by_pos.keys()))
    for row, col in positions:
        exp = _norm_pid(expected.get((row, col)))
        cell = by_pos.get((row, col)) or {}
        got_status = cell.get("status", "MISSING")
        got_pid = _norm_pid(cell.get("product_id"))

        if exp is None:
            ok = got_status == "EMPTY"
            reason = "ok_empty" if ok else f"expected_empty_got_{got_status}"
            if not ok and got_pid:
                reason = f"expected_empty_got_{got_pid}"
        else:
            ok = got_status == "FILLED" and got_pid == exp
            if ok:
                reason = "ok_match"
            elif got_status == "EMPTY":
                reason = "missing_product"
            elif got_status == "UNREADABLE":
                reason = "unreadable"
            elif got_pid and got_pid != exp:
                reason = f"wrong_product_got_{got_pid}"
            else:
                reason = f"mismatch_status_{got_status}"

        if ok:
            pass_n += 1
            verdict = "PASS"
        else:
            fail_n += 1
            verdict = "FAIL"

        rows.append(
            {
                "row": row,
                "col": col,
                "expected": exp,
                "actual_status": got_status,
                "actual_product_id": got_pid,
                "verdict": verdict,
                "reason": reason,
            }
        )

    return {
        "rack_id": rack.rack_id,
        "store_type": rack.store_type,
        "summary": {
            "PASS": pass_n,
            "FAIL": fail_n,
            "total": pass_n + fail_n,
            "all_pass": fail_n == 0 and pass_n > 0,
        },
        "cells": rows,
    }


def analyze_and_verify(
    image_bgr: np.ndarray,
    rack: RackConfig,
) -> Dict[str, Any]:
    actual = analyze_rack(image_bgr, rack)
    verification = verify_rack(rack, actual)
    return {"scan": actual, "verification": verification}
