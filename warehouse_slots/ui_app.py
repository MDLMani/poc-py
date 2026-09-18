"""CustomTkinter desktop UI for offline warehouse slot scanning (Phase 3).

Features:
  - Still image mode (load PNG) and optional live camera
  - Slot ROI overlays on the preview
  - Per-slot results table (counts / empty / unreadable)
  - Snapshot + Confirm IN / Confirm OUT wired to Phase 2 SQLite store
  - Fully offline (no network imports in analysis path)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from .config import SlotConfig, load_slots
from .slot_pipeline import analyze_image
from .store import WarehouseStore, default_db_path


def _bgr_to_rgb(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def draw_overlays(
    image_bgr: np.ndarray,
    slots: List[SlotConfig],
    report: Optional[Dict[str, Any]] = None,
) -> np.ndarray:
    """Draw ROI rectangles and optional status labels onto a copy."""
    out = image_bgr.copy()
    by_id = {}
    if report:
        by_id = {s["slot_id"]: s for s in report.get("slots", [])}
    colors = {
        "ok": (40, 180, 40),
        "warn": (0, 165, 255),
        "empty": (180, 180, 40),
    }
    for slot in slots:
        x, y, w, h = slot.roi
        info = by_id.get(slot.id)
        color = colors["ok"]
        label = slot.id
        if info:
            if info.get("unreadable", 0) > 0:
                color = colors["warn"]
            elif info.get("filled", 0) == 0:
                color = colors["empty"]
            label = (
                f"{slot.id} F={info.get('filled', 0)} "
                f"E={info.get('empty', 0)} U={info.get('unreadable', 0)}"
            )
        cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)
        # Cell band guides
        for i in range(1, slot.capacity):
            cy = y + int(round(i * h / slot.capacity))
            cv2.line(out, (x, cy), (x + w, cy), color, 1)
        cv2.putText(
            out,
            label,
            (x, max(16, y - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )
    return out


def run_ui(
    config_path: str | Path = "config/slots.example.json",
    db_path: Optional[Path] = None,
    image_path: Optional[Path] = None,
    camera_index: int = 0,
) -> int:
    try:
        import customtkinter as ctk
        from PIL import Image, ImageTk
    except ImportError as exc:
        print(
            "UI requires customtkinter + Pillow + tkinter. "
            f"Install: pip install customtkinter Pillow  ({exc})"
        )
        return 1

    config_path = Path(config_path)
    if not config_path.is_file():
        print(f"error: config not found: {config_path}")
        return 1

    slots = load_slots(config_path)
    store = WarehouseStore(db_path or default_db_path())
    store.seed_common_skus()

    snapshots_dir = Path("data/snapshots")
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")

    app = ctk.CTk()
    app.title("Warehouse Slots — Offline UI (Phase 3)")
    app.geometry("1100x720")

    state: Dict[str, Any] = {
        "frame": None,  # latest BGR frame
        "report": None,
        "cap": None,
        "live": False,
        "photo": None,
        "still_path": Path(image_path) if image_path else None,
    }

    # Layout
    left = ctk.CTkFrame(app)
    left.pack(side="left", fill="both", expand=True, padx=8, pady=8)
    right = ctk.CTkFrame(app, width=360)
    right.pack(side="right", fill="y", padx=8, pady=8)

    preview_label = ctk.CTkLabel(left, text="No image loaded")
    preview_label.pack(fill="both", expand=True)

    status_var = ctk.StringVar(value="Ready (offline)")
    ctk.CTkLabel(right, textvariable=status_var, wraplength=320).pack(
        anchor="w", padx=8, pady=(8, 4)
    )

    table = ctk.CTkTextbox(right, width=340, height=280)
    table.pack(padx=8, pady=4, fill="x")
    table.insert("1.0", "Per-slot results will appear here.\n")
    table.configure(state="disabled")

    def set_status(msg: str) -> None:
        status_var.set(msg)

    def refresh_table(report: Optional[Dict[str, Any]]) -> None:
        table.configure(state="normal")
        table.delete("1.0", "end")
        if not report:
            table.insert("1.0", "(no scan yet)\n")
            table.configure(state="disabled")
            return
        lines = ["slot | filled | empty | unread | counts", "-" * 42]
        for s in report.get("slots", []):
            counts = json.dumps(s.get("counts") or {}, sort_keys=True)
            lines.append(
                f"{s['slot_id']:4} | {s.get('filled', 0):6} | "
                f"{s.get('empty', 0):5} | {s.get('unreadable', 0):6} | {counts}"
            )
        table.insert("1.0", "\n".join(lines) + "\n")
        table.configure(state="disabled")

    def show_frame(frame_bgr: np.ndarray) -> None:
        overlay = draw_overlays(frame_bgr, slots, state["report"])
        rgb = _bgr_to_rgb(overlay)
        # Fit into preview
        max_w, max_h = 720, 640
        h, w = rgb.shape[:2]
        scale = min(max_w / w, max_h / h, 1.0)
        if scale < 1.0:
            rgb = cv2.resize(
                rgb,
                (int(w * scale), int(h * scale)),
                interpolation=cv2.INTER_AREA,
            )
        pil = Image.fromarray(rgb)
        photo = ImageTk.PhotoImage(pil)
        state["photo"] = photo  # keep ref
        preview_label.configure(image=photo, text="")

    def analyze_current() -> None:
        frame = state["frame"]
        if frame is None:
            set_status("No frame to analyze")
            return
        report = analyze_image(frame, slots)
        state["report"] = report
        refresh_table(report)
        show_frame(frame)
        set_status("Scan complete (offline)")

    def load_still(path: Path) -> None:
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            set_status(f"Failed to load {path}")
            return
        state["frame"] = img
        state["still_path"] = path
        state["report"] = None
        refresh_table(None)
        show_frame(img)
        set_status(f"Loaded still: {path}")

    def browse_still() -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Open still image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All", "*.*")],
        )
        if path:
            load_still(Path(path))

    def start_camera() -> None:
        stop_camera()
        cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            set_status(f"Camera {camera_index} unavailable — use still image")
            return
        state["cap"] = cap
        state["live"] = True
        set_status(f"Live camera {camera_index}")
        tick_live()

    def stop_camera() -> None:
        state["live"] = False
        cap = state.get("cap")
        if cap is not None:
            cap.release()
            state["cap"] = None

    def tick_live() -> None:
        if not state["live"]:
            return
        cap = state["cap"]
        if cap is None:
            return
        ok, frame = cap.read()
        if ok and frame is not None:
            state["frame"] = frame
            show_frame(frame)
        app.after(33, tick_live)

    def snapshot_path() -> Path:
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = snapshots_dir / f"snap_{ts}.png"
        frame = state["frame"]
        if frame is None:
            raise RuntimeError("no frame")
        cv2.imwrite(str(path), frame)
        return path

    def confirm(direction: str) -> None:
        if state["frame"] is None:
            set_status("Nothing to confirm — load image or start camera")
            return
        if state["report"] is None:
            analyze_current()
        try:
            path = snapshot_path()
        except RuntimeError:
            set_status("No frame to snapshot")
            return
        event = store.record_event(
            direction=direction,
            image_path=path.resolve(),
            scan=state["report"] or {},
            note=f"ui confirm {direction}",
        )
        lines = store.event_sku_lines(event.id)
        set_status(
            f"Confirmed {direction} event #{event.id} "
            f"→ {path.name} skus={lines}"
        )

    # Buttons
    btn_row = ctk.CTkFrame(right)
    btn_row.pack(fill="x", padx=8, pady=8)
    ctk.CTkButton(btn_row, text="Open still…", command=browse_still).pack(
        fill="x", pady=2
    )
    ctk.CTkButton(btn_row, text="Start camera", command=start_camera).pack(
        fill="x", pady=2
    )
    ctk.CTkButton(btn_row, text="Stop camera", command=stop_camera).pack(
        fill="x", pady=2
    )
    ctk.CTkButton(btn_row, text="Analyze / Snapshot view", command=analyze_current).pack(
        fill="x", pady=2
    )
    ctk.CTkButton(
        btn_row, text="Confirm IN", fg_color="#1a7f37", command=lambda: confirm("IN")
    ).pack(fill="x", pady=2)
    ctk.CTkButton(
        btn_row, text="Confirm OUT", fg_color="#b62324", command=lambda: confirm("OUT")
    ).pack(fill="x", pady=2)

    def on_close() -> None:
        stop_camera()
        app.destroy()

    app.protocol("WM_DELETE_WINDOW", on_close)

    # Initial still if provided
    if state["still_path"] and state["still_path"].is_file():
        load_still(state["still_path"])
        analyze_current()
    else:
        # Prefer board fixture if present
        board = Path("fixtures/board.png")
        if board.is_file():
            load_still(board)

    app.mainloop()
    return 0


def smoke_check(config_path: Path, image_path: Path, db_path: Path) -> Dict[str, Any]:
    """Headless smoke: load image, overlay, analyze, record IN+OUT. No GUI."""
    slots = load_slots(config_path)
    img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(image_path)
    report = analyze_image(img, slots)
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
    }
