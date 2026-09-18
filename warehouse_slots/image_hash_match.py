"""Offline ImageHash visual fallback for UNREADABLE cells (Phase 4).

Only used when a cell is occupied but QR decode failed. Never replaces a
successful QR decode. Requires local reference images named by SKU id
(e.g. fixtures/refs/SKU-ALPHA.png).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

import cv2
import numpy as np

# ImageHash is pip-installable and fully offline at runtime.
import imagehash
from PIL import Image


SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


@dataclass(frozen=True)
class HashMatch:
    sku_id: str
    distance: int
    ref_path: str


class SkuHashIndex:
    """In-memory perceptual-hash index of local SKU reference images."""

    def __init__(self, refs: Dict[str, imagehash.ImageHash], paths: Dict[str, Path]):
        self._refs = refs
        self._paths = paths

    @classmethod
    def load(cls, directory: Union[str, Path]) -> "SkuHashIndex":
        d = Path(directory)
        refs: Dict[str, imagehash.ImageHash] = {}
        paths: Dict[str, Path] = {}
        if not d.is_dir():
            return cls(refs, paths)
        for path in sorted(d.iterdir()):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            sku_id = path.stem  # SKU-ALPHA.png → SKU-ALPHA
            try:
                img = Image.open(path).convert("RGB")
            except OSError:
                continue
            refs[sku_id] = imagehash.phash(img)
            paths[sku_id] = path
        return cls(refs, paths)

    def __len__(self) -> int:
        return len(self._refs)

    @property
    def sku_ids(self) -> List[str]:
        return sorted(self._refs.keys())

    def match(
        self,
        crop_bgr: np.ndarray,
        threshold: int = 12,
    ) -> Optional[HashMatch]:
        """Return best SKU under Hamming threshold, or None."""
        if not self._refs or crop_bgr is None or crop_bgr.size == 0:
            return None
        pil = _bgr_to_pil(crop_bgr)
        probe = imagehash.phash(pil)
        best_id: Optional[str] = None
        best_dist = 10**9
        for sku_id, ref_hash in self._refs.items():
            dist = int(probe - ref_hash)
            if dist < best_dist:
                best_dist = dist
                best_id = sku_id
        if best_id is None or best_dist > threshold:
            return None
        return HashMatch(
            sku_id=best_id,
            distance=best_dist,
            ref_path=str(self._paths[best_id]),
        )


def _bgr_to_pil(crop_bgr: np.ndarray) -> Image.Image:
    if crop_bgr.ndim == 2:
        return Image.fromarray(crop_bgr).convert("RGB")
    rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def match_cell(
    crop_bgr: np.ndarray,
    index: Optional[SkuHashIndex],
    threshold: int = 12,
) -> Optional[HashMatch]:
    """Convenience wrapper — no-op if index missing/empty."""
    if index is None or len(index) == 0:
        return None
    return index.match(crop_bgr, threshold=threshold)


def build_index(directory: Optional[Union[str, Path]]) -> Optional[SkuHashIndex]:
    if directory is None:
        return None
    path = Path(directory)
    if not path.is_dir():
        return None
    idx = SkuHashIndex.load(path)
    return idx if len(idx) else None
