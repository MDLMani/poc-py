"""Live rack configuration: uniform / irregular grids + expected product IDs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, Union


Roi = Tuple[int, int, int, int]  # x, y, w, h


@dataclass
class RackCellExpect:
    row: int
    col: int
    expected_product_id: Optional[str] = None

    def normalized_product_id(self) -> Optional[str]:
        if self.expected_product_id is None:
            return None
        s = str(self.expected_product_id).strip()
        return s or None


@dataclass
class IrregularRow:
    cells: int

    def __post_init__(self) -> None:
        if self.cells < 1:
            raise ValueError("irregular row cells must be >= 1")


@dataclass
class RackLayout:
    mode: str  # "uniform" | "irregular"
    rows: int = 1
    cols: int = 1
    irregular_rows: List[IrregularRow] = field(default_factory=list)

    def __post_init__(self) -> None:
        mode = self.mode.lower().strip()
        if mode not in ("uniform", "irregular"):
            raise ValueError("layout.mode must be 'uniform' or 'irregular'")
        object.__setattr__(self, "mode", mode)
        if mode == "uniform":
            if self.rows < 1 or self.cols < 1:
                raise ValueError("uniform layout requires rows >= 1 and cols >= 1")
        else:
            if not self.irregular_rows:
                raise ValueError("irregular layout requires at least one row")


@dataclass
class RackConfig:
    rack_id: str
    store_type: str
    outer_roi: Roi
    layout: RackLayout
    cells: List[RackCellExpect] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not str(self.rack_id).strip():
            raise ValueError("rack_id is required")
        x, y, w, h = self.outer_roi
        if w < 1 or h < 1:
            raise ValueError("outer_roi width/height must be >= 1")
        if x < 0 or y < 0:
            raise ValueError("outer_roi x/y must be >= 0")

    def expected_map(self) -> Dict[Tuple[int, int], Optional[str]]:
        return {
            (c.row, c.col): c.normalized_product_id() for c in self.cells
        }

    def ensure_cell_slots(self) -> None:
        """Fill missing cells with empty expected product ids."""
        have = {(c.row, c.col) for c in self.cells}
        for row, col, _roi in iter_cell_rois(self):
            if (row, col) not in have:
                self.cells.append(
                    RackCellExpect(row=row, col=col, expected_product_id=None)
                )
        self.cells.sort(key=lambda c: (c.row, c.col))


def _parse_roi(raw: Any) -> Roi:
    if not isinstance(raw, Sequence) or len(raw) != 4:
        raise ValueError(f"outer_roi must be [x, y, w, h], got {raw!r}")
    return (int(raw[0]), int(raw[1]), int(raw[2]), int(raw[3]))


def _parse_layout(raw: Dict[str, Any]) -> RackLayout:
    mode = str(raw.get("mode", "uniform")).lower().strip()
    if mode == "uniform":
        return RackLayout(
            mode="uniform",
            rows=int(raw.get("rows", 1)),
            cols=int(raw.get("cols", 1)),
        )
    if mode == "irregular":
        rows_raw = raw.get("rows")
        if not isinstance(rows_raw, list) or not rows_raw:
            raise ValueError("irregular layout.rows must be a non-empty list")
        irregular: List[IrregularRow] = []
        for item in rows_raw:
            if isinstance(item, dict):
                irregular.append(IrregularRow(cells=int(item["cells"])))
            else:
                irregular.append(IrregularRow(cells=int(item)))
        return RackLayout(mode="irregular", irregular_rows=irregular)
    raise ValueError(f"unknown layout.mode: {mode!r}")


def rack_from_dict(data: Dict[str, Any]) -> RackConfig:
    layout = _parse_layout(data.get("layout") or {"mode": "uniform", "rows": 1, "cols": 1})
    cells: List[RackCellExpect] = []
    for c in data.get("cells") or []:
        pid = c.get("expected_product_id")
        if pid is not None:
            pid = str(pid).strip() or None
        cells.append(
            RackCellExpect(
                row=int(c["row"]),
                col=int(c["col"]),
                expected_product_id=pid,
            )
        )
    rack = RackConfig(
        rack_id=str(data.get("rack_id", "RACK-1")),
        store_type=str(data.get("store_type", "custom")),
        outer_roi=_parse_roi(data.get("outer_roi", [0, 0, 1, 1])),
        layout=layout,
        cells=cells,
    )
    rack.ensure_cell_slots()
    return rack


def rack_to_dict(rack: RackConfig) -> Dict[str, Any]:
    layout: Dict[str, Any]
    if rack.layout.mode == "uniform":
        layout = {
            "mode": "uniform",
            "rows": rack.layout.rows,
            "cols": rack.layout.cols,
        }
    else:
        layout = {
            "mode": "irregular",
            "rows": [{"cells": r.cells} for r in rack.layout.irregular_rows],
        }
    return {
        "rack_id": rack.rack_id,
        "store_type": rack.store_type,
        "outer_roi": list(rack.outer_roi),
        "layout": layout,
        "cells": [
            {
                "row": c.row,
                "col": c.col,
                "expected_product_id": c.normalized_product_id(),
            }
            for c in sorted(rack.cells, key=lambda x: (x.row, x.col))
        ],
    }


def load_rack(path: Union[str, Path]) -> RackConfig:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("rack JSON must be an object")
    return rack_from_dict(data)


def save_rack(rack: RackConfig, path: Union[str, Path]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    rack.ensure_cell_slots()
    p.write_text(json.dumps(rack_to_dict(rack), indent=2) + "\n", encoding="utf-8")
    return p


def build_uniform_rack(
    *,
    rack_id: str,
    store_type: str,
    outer_roi: Roi,
    rows: int,
    cols: int,
    expected: Optional[Dict[Tuple[int, int], Optional[str]]] = None,
) -> RackConfig:
    expected = expected or {}
    cells = [
        RackCellExpect(
            row=r,
            col=c,
            expected_product_id=expected.get((r, c)),
        )
        for r in range(rows)
        for c in range(cols)
    ]
    return RackConfig(
        rack_id=rack_id,
        store_type=store_type,
        outer_roi=outer_roi,
        layout=RackLayout(mode="uniform", rows=rows, cols=cols),
        cells=cells,
    )


def build_irregular_rack(
    *,
    rack_id: str,
    store_type: str,
    outer_roi: Roi,
    row_cell_counts: Sequence[int],
    expected: Optional[Dict[Tuple[int, int], Optional[str]]] = None,
) -> RackConfig:
    expected = expected or {}
    irregular = [IrregularRow(cells=int(n)) for n in row_cell_counts]
    cells: List[RackCellExpect] = []
    for r, row in enumerate(irregular):
        for c in range(row.cells):
            cells.append(
                RackCellExpect(
                    row=r,
                    col=c,
                    expected_product_id=expected.get((r, c)),
                )
            )
    return RackConfig(
        rack_id=rack_id,
        store_type=store_type,
        outer_roi=outer_roi,
        layout=RackLayout(mode="irregular", irregular_rows=irregular),
        cells=cells,
    )


def iter_cell_rois(
    rack: RackConfig,
    image_shape: Optional[Tuple[int, ...]] = None,
) -> Iterator[Tuple[int, int, Roi]]:
    """Yield (row, col, roi) for every cell. ROIs are clipped if image_shape given."""
    ox, oy, ow, oh = rack.outer_roi
    layout = rack.layout

    def clip(x: int, y: int, w: int, h: int) -> Roi:
        if image_shape is None:
            return (x, y, max(1, w), max(1, h))
        img_h, img_w = int(image_shape[0]), int(image_shape[1])
        x0 = max(0, min(img_w, x))
        y0 = max(0, min(img_h, y))
        x1 = max(0, min(img_w, x + w))
        y1 = max(0, min(img_h, y + h))
        return (x0, y0, max(1, x1 - x0), max(1, y1 - y0))

    if layout.mode == "uniform":
        rows, cols = layout.rows, layout.cols
        for r in range(rows):
            y0 = oy + int(round(r * oh / rows))
            y1 = oy + int(round((r + 1) * oh / rows))
            for c in range(cols):
                x0 = ox + int(round(c * ow / cols))
                x1 = ox + int(round((c + 1) * ow / cols))
                yield r, c, clip(x0, y0, x1 - x0, y1 - y0)
        return

    n_rows = len(layout.irregular_rows)
    for r, row in enumerate(layout.irregular_rows):
        y0 = oy + int(round(r * oh / n_rows))
        y1 = oy + int(round((r + 1) * oh / n_rows))
        n_cols = row.cells
        for c in range(n_cols):
            x0 = ox + int(round(c * ow / n_cols))
            x1 = ox + int(round((c + 1) * ow / n_cols))
            yield r, c, clip(x0, y0, x1 - x0, y1 - y0)


PRESETS: Dict[str, Dict[str, Any]] = {
    "uniform_4x5": {
        "store_type": "uniform_4x5",
        "layout": {"mode": "uniform", "rows": 4, "cols": 5},
    },
    "uniform_7x2": {
        "store_type": "uniform_7x2",
        "layout": {"mode": "uniform", "rows": 7, "cols": 2},
    },
    "uniform_5x1": {
        "store_type": "uniform_5x1",
        "layout": {"mode": "uniform", "rows": 5, "cols": 1},
    },
    "irregular_5_2": {
        "store_type": "irregular_5_2",
        "layout": {"mode": "irregular", "rows": [{"cells": 5}, {"cells": 2}]},
    },
}


def apply_preset(
    name: str,
    *,
    rack_id: str = "RACK-1",
    outer_roi: Roi = (0, 0, 800, 600),
) -> RackConfig:
    if name not in PRESETS:
        raise KeyError(f"unknown preset: {name}")
    data = {
        "rack_id": rack_id,
        "store_type": PRESETS[name]["store_type"],
        "outer_roi": list(outer_roi),
        "layout": PRESETS[name]["layout"],
        "cells": [],
    }
    return rack_from_dict(data)
