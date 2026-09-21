"""Warehouse slots UI launcher and headless smoke helpers.

The Python GUI (CustomTkinter) has been replaced by the Electron frontend.
This module retains headless drawing and smoke test utilities, and provides
the CLI launcher for the Electron frontend.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from .config import load_config
from .slot_pipeline import analyze_image
from .store import WarehouseStore, default_db_path


def draw_overlays(
    image_bgr: np.ndarray,
    slots: List,
    report: Optional[Dict[str, Any]] = None,
    *,
    scale: float = 1.0,
) -> np.ndarray:
    """Draw ROI rectangles and compact status labels onto a copy.

    Labels are drawn inside each slot (with a dark backing) so adjacent
    columns do not overlap when the preview is scaled down.
    ``scale`` maps original ROI coordinates onto a pre-resized preview.
    """
    out = image_bgr.copy()
    by_id = {}
    if report:
        by_id = {s["slot_id"]: s for s in report.get("slots", [])}
    colors = {
        "ok": (40, 180, 40),
        "warn": (0, 165, 255),
        "alert": (0, 0, 220),
        "empty": (180, 180, 40),
        "hash": (200, 100, 0),
    }
    font_scale = max(0.35, min(0.55, 0.45 * max(scale, 0.35)))
    thickness = 1

    for slot in slots:
        x, y, w, h = slot.roi
        if scale != 1.0:
            x = int(round(x * scale))
            y = int(round(y * scale))
            w = int(round(w * scale))
            h = int(round(h * scale))
        info = by_id.get(slot.id)
        color = colors["ok"]
        if info:
            unread = info.get("unreadable", 0)
            hash_filled = info.get("hash_filled", 0)
            if unread > 0:
                color = colors["alert"]
            elif hash_filled > 0:
                color = colors["hash"]
            elif info.get("filled", 0) == 0:
                color = colors["empty"]

        cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)
        for i in range(1, slot.capacity):
            cy = y + int(round(i * h / slot.capacity))
            cv2.line(out, (x, cy), (x + w, cy), color, 1)

        if info:
            line1 = str(slot.id)
            line2 = (
                f"F{info.get('filled', 0)} "
                f"E{info.get('empty', 0)} "
                f"U{info.get('unreadable', 0)}"
            )
            if info.get("hash_filled"):
                line2 += f" H{info['hash_filled']}"
        else:
            line1 = str(slot.id)
            line2 = f"cap {slot.capacity}"

        pad = max(2, int(round(4 * scale)))
        ty1 = y + pad + int(14 * font_scale / 0.45)
        ty2 = ty1 + int(16 * font_scale / 0.45)
        for text, ty in ((line1, ty1), (line2, ty2)):
            (tw, th), _ = cv2.getTextSize(
                text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
            )
            cv2.rectangle(
                out,
                (x + pad, ty - th - 2),
                (x + pad + tw + 4, ty + 3),
                (20, 20, 20),
                -1,
            )
            cv2.putText(
                out,
                text,
                (x + pad + 2, ty),
                cv2.FONT_HERSHEY_SIMPLEX,
                font_scale,
                color,
                thickness,
                cv2.LINE_AA,
            )

        if info:
            for cell in info.get("cells") or []:
                if cell.get("status") != "UNREADABLE":
                    continue
                idx = int(cell["index"])
                cy0 = y + int(round(idx * h / slot.capacity))
                cy1 = y + int(round((idx + 1) * h / slot.capacity))
                cv2.putText(
                    out,
                    "UR",
                    (x + 4, (cy0 + cy1) // 2 + 4),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    max(0.35, font_scale),
                    colors["alert"],
                    1,
                    cv2.LINE_AA,
                )
    return out


def smoke_check(config_path: Path, image_path: Path, db_path: Path) -> Dict[str, Any]:
    """Headless smoke: load image, overlay, analyze, record IN+OUT. No GUI."""
    slots, options = load_config(config_path)
    img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(image_path)
    report = analyze_image(img, slots, options=options)
    overlay = draw_overlays(img, slots, report)
    store = WarehouseStore(db_path)
    store.seed_common_skus()
    snap_dir = db_path.parent / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap = snap_dir / "smoke_snap.png"
    cv2.imwrite(str(snap), overlay)
    ev_in = store.record_event("IN", snap, scan=report, note="smoke IN")
    ev_out = store.record_event("OUT", snap, scan=report, note="smoke OUT")
    return {
        "slots": len(report["slots"]),
        "overlay_shape": list(overlay.shape),
        "event_in": ev_in.id,
        "event_out": ev_out.id,
        "snap": str(snap),
        "alerts": report.get("alerts") or [],
    }


def run_ui(
    config_path: Optional[Path] = None,
    image_path: Optional[Path] = None,
    camera_index: Optional[int] = None,
    db_path: Optional[Path] = None,
    **kwargs: Any,
) -> int:
    """Launch the Electron frontend desktop app."""
    root_dir = Path(__file__).resolve().parents[1]
    npm_bin = shutil.which("npm")
    if not npm_bin:
        print("[ui] 'npm' was not found in PATH. Please install Node.js and npm to run the Electron frontend.")
        print("[ui] You can also start the local API: python -m warehouse_slots serve")
        return 1

    print(f"[ui] Launching Electron frontend from {root_dir}...")
    env = dict(os.environ)
    if config_path:
        env["SLOTS_CONFIG"] = str(config_path)
    if image_path:
        env["INITIAL_IMAGE"] = str(image_path)
    if db_path:
        env["WAREHOUSE_DB"] = str(db_path)

    try:
        proc = subprocess.run([npm_bin, "start"], cwd=str(root_dir), env=env)
        return proc.returncode
    except Exception as exc:
        print(f"[ui] Failed to launch Electron frontend: {exc}", file=sys.stderr)
        return 1
