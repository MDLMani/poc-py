"""Load slot configuration and Phase 4 pipeline options from JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple, Union


FillDirection = str  # "top_to_bottom" | "bottom_to_top"


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


@dataclass(frozen=True)
class PipelineOptions:
    """Phase 4 knobs: ImageHash fallback + cell band fill direction."""

    hash_threshold: int = 12
    fill_direction: str = "top_to_bottom"
    reference_images_dir: Optional[Path] = None
    enable_hash_fallback: bool = True

    def __post_init__(self) -> None:
        if self.hash_threshold < 0:
            raise ValueError("hash_threshold must be >= 0")
        fd = self.fill_direction.lower().replace("-", "_")
        if fd not in ("top_to_bottom", "bottom_to_top"):
            raise ValueError(
                "fill_direction must be 'top_to_bottom' or 'bottom_to_top'"
            )
        object.__setattr__(self, "fill_direction", fd)


DEFAULT_OPTIONS = PipelineOptions()


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


def _parse_options(
    data: dict[str, Any],
    config_dir: Optional[Path] = None,
    overrides: Optional[dict[str, Any]] = None,
) -> PipelineOptions:
    """Parse top-level pipeline knobs from config JSON + optional CLI overrides."""
    overrides = overrides or {}

    def pick(key: str, default: Any) -> Any:
        if key in overrides and overrides[key] is not None:
            return overrides[key]
        return data.get(key, default)

    refs = pick("reference_images_dir", None)
    refs_path: Optional[Path] = None
    if refs:
        refs_path = Path(str(refs))
        if not refs_path.is_absolute() and config_dir is not None:
            # Resolve relative to config file dir, then CWD as fallback
            candidate = (config_dir / refs_path).resolve()
            if candidate.exists():
                refs_path = candidate
            else:
                refs_path = Path(refs_path)

    enable = pick("enable_hash_fallback", True)
    if isinstance(enable, str):
        enable = enable.strip().lower() in ("1", "true", "yes", "on")

    return PipelineOptions(
        hash_threshold=int(pick("hash_threshold", 12)),
        fill_direction=str(pick("fill_direction", "top_to_bottom")),
        reference_images_dir=refs_path,
        enable_hash_fallback=bool(enable),
    )


def load_slots(
    path: Union[str, Path],
    *,
    overrides: Optional[dict[str, Any]] = None,
) -> List[SlotConfig]:
    """Load a list of SlotConfig from a JSON file.

    Expected shape::

        {
          "hash_threshold": 12,
          "fill_direction": "top_to_bottom",
          "reference_images_dir": "refs",
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


def load_config(
    path: Union[str, Path],
    *,
    overrides: Optional[dict[str, Any]] = None,
) -> Tuple[List[SlotConfig], PipelineOptions]:
    """Load slots + Phase 4 pipeline options from one JSON file."""
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        slots = [_parse_slot(e) for e in data]
        options = _parse_options({}, config_dir=p.parent, overrides=overrides)
        return slots, options

    if not isinstance(data, dict):
        raise ValueError("config JSON must be an object or a list")
    if "slots" not in data:
        raise ValueError("config JSON must contain a 'slots' key or be a list")
    entries = data["slots"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("slots must be a non-empty list")
    slots = [_parse_slot(e) for e in entries]
    options = _parse_options(data, config_dir=p.parent, overrides=overrides)
    return slots, options
