"""Load slot configuration from JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Sequence, Tuple, Union


@dataclass(frozen=True)
class SlotConfig:
    """One physical slot with a known ROI and capacity."""

    id: str
    roi: Tuple[int, int, int, int]  # x, y, w, h
    capacity: int

    def __post_init__(self) -> None:
        if self.capacity < 1:
            raise ValueError(f"slot {self.id!r}: capacity must be >= 1")
        x, y, w, h = self.roi
        if w < 1 or h < 1:
            raise ValueError(f"slot {self.id!r}: roi width/height must be >= 1")
        if x < 0 or y < 0:
            raise ValueError(f"slot {self.id!r}: roi x/y must be >= 0")


def _parse_slot(raw: dict[str, Any]) -> SlotConfig:
    if "id" not in raw or "roi" not in raw or "capacity" not in raw:
        raise ValueError(f"slot entry missing required keys: {raw!r}")
    roi = raw["roi"]
    if not isinstance(roi, Sequence) or len(roi) != 4:
        raise ValueError(f"roi must be [x, y, w, h], got {roi!r}")
    return SlotConfig(
        id=str(raw["id"]),
        roi=(int(roi[0]), int(roi[1]), int(roi[2]), int(roi[3])),
        capacity=int(raw["capacity"]),
    )


def load_slots(path: Union[str, Path]) -> List[SlotConfig]:
    """Load a list of SlotConfig from a JSON file.

    Expected shape::

        {
          "slots": [
            {"id": "F1", "roi": [x, y, w, h], "capacity": 10},
            ...
          ]
        }

    or a bare list of slot objects.
    """
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        if "slots" not in data:
            raise ValueError("config JSON must contain a 'slots' key or be a list")
        entries = data["slots"]
    elif isinstance(data, list):
        entries = data
    else:
        raise ValueError("config JSON must be an object or a list")

    if not isinstance(entries, list) or not entries:
        raise ValueError("slots must be a non-empty list")

    return [_parse_slot(e) for e in entries]
