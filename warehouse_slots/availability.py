"""Backend availability check: image/QR counts vs SQLite on-hand stock.

Fully offline. Statuses per SKU:
  OK                 — SKU known and on_hand >= in_image_count (and on_hand > 0 when count=0)
  LOW                — SKU known, on_hand > 0 but on_hand < in_image_count
  MISSING_IN_BACKEND — SKU known in catalog but on_hand <= 0
  UNKNOWN_SKU        — QR/SKU not in catalog
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import cv2

from .config import load_config
from .slot_pipeline import analyze_image
from .store import WarehouseStore


STATUSES = ("OK", "LOW", "MISSING_IN_BACKEND", "UNKNOWN_SKU")


def classify_status(
    *,
    in_catalog: bool,
    in_image_count: int,
    backend_available: int,
) -> str:
    if not in_catalog:
        return "UNKNOWN_SKU"
    if backend_available <= 0:
        return "MISSING_IN_BACKEND"
    if in_image_count > 0 and backend_available < in_image_count:
        return "LOW"
    return "OK"


def counts_from_scan(scan: Dict[str, Any]) -> Counter:
    c: Counter = Counter()
    for slot in scan.get("slots") or []:
        for payload, qty in (slot.get("counts") or {}).items():
            c[str(payload)] += int(qty)
    return c


def build_rows(
    store: WarehouseStore,
    image_counts: Dict[str, int],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for sku_id in sorted(image_counts.keys()):
        in_image_count = int(image_counts[sku_id])
        sku = store.get_sku(sku_id)
        on_hand = store.backend_available(sku_id) if sku is not None else 0
        # If SKU was auto-registered from events, get_sku is non-None.
        # UNKNOWN only when never in catalog.
        in_catalog = sku is not None
        status = classify_status(
            in_catalog=in_catalog,
            in_image_count=in_image_count,
            backend_available=on_hand if in_catalog else 0,
        )
        rows.append(
            {
                "sku_id": sku_id,
                "name": sku.name if sku else "",
                "in_image_count": in_image_count,
                "backend_available": on_hand if in_catalog else 0,
                "status": status,
            }
        )
    return rows


def check_availability(
    store: WarehouseStore,
    *,
    image_path: Optional[Union[str, Path]] = None,
    config_path: Optional[Union[str, Path]] = None,
    qr_payloads: Optional[Sequence[str]] = None,
    option_overrides: Optional[Dict[str, Any]] = None,
    library_ids: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    """Cross-check image and/or QR payloads against SQLite stock.

    Provide at least one of: image_path (+ config), qr_payloads, library_ids.
    """
    counts: Counter = Counter()
    scan: Dict[str, Any] = {}
    sources: List[str] = []

    if library_ids:
        for lid in library_ids:
            entry = store.get_library(int(lid))
            if entry is None:
                continue
            counts[entry.qr_payload] += 1
            sources.append(f"library:{entry.id}")

    if qr_payloads:
        for q in qr_payloads:
            q = (q or "").strip()
            if not q:
                continue
            counts[q] += 1
            sources.append(f"qr:{q}")

    if image_path is not None:
        path = Path(image_path)
        if not path.is_file():
            raise FileNotFoundError(f"image not found: {path}")
        if not config_path:
            raise ValueError("--config is required when checking an image")
        overrides = dict(option_overrides or {})
        # Prefer library refs for ImageHash when available and not overridden
        if overrides.get("reference_images_dir") is None:
            refs = store.refs_dir()
            if refs.is_dir() and any(refs.iterdir()):
                overrides.setdefault("reference_images_dir", str(refs))
        slots, options = load_config(config_path, overrides=overrides)
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"failed to read image: {path}")
        scan = analyze_image(image, slots, options=options)
        img_counts = counts_from_scan(scan)
        counts.update(img_counts)
        sources.append(f"image:{path}")

    if not counts:
        return {
            "skus": [],
            "scan": scan,
            "sources": sources,
            "summary": {"OK": 0, "LOW": 0, "MISSING_IN_BACKEND": 0, "UNKNOWN_SKU": 0},
        }

    rows = build_rows(store, dict(counts))
    summary = Counter(r["status"] for r in rows)
    return {
        "skus": rows,
        "scan": scan,
        "sources": sources,
        "summary": {s: int(summary.get(s, 0)) for s in STATUSES},
    }
