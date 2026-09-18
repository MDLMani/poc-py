"""CustomTkinter desktop UI for offline warehouse slot scanning (Phases 3–4).

Features:
  - Still image mode (load PNG) and optional live camera
  - Slot ROI overlays on the preview
  - Per-slot results table (counts / empty / unreadable)
  - Clear UNREADABLE / hash-fallback alerts (Phase 4)
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

from .config import load_config
from .slot_pipeline import analyze_image, format_alerts_text
from .store import WarehouseStore, default_db_path


def _bgr_to_rgb(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def draw_overlays(
    image_bgr: np.ndarray,
    slots: List,
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
        "alert": (0, 0, 220),
        "empty": (180, 180, 40),
        "hash": (200, 100, 0),
    }
    for slot in slots:
        x, y, w, h = slot.roi
        info = by_id.get(slot.id)
        color = colors["ok"]
        label = slot.id
        if info:
            unread = info.get("unreadable", 0)
            hash_filled = info.get("hash_filled", 0)
            if unread > 0:
                color = colors["alert"]
            elif hash_filled > 0:
                color = colors["hash"]
            elif info.get("filled", 0) == 0:
                color = colors["empty"]
            label = (
                f"{slot.id} F={info.get('filled', 0)} "
                f"E={info.get('empty', 0)} U={info.get('unreadable', 0)}"
            )
            if hash_filled:
                label += f" H={hash_filled}"
        cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)
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
        # Per-cell UNREADABLE markers
        if info:
            for cell in info.get("cells") or []:
                if cell.get("status") != "UNREADABLE":
                    continue
                idx = int(cell["index"])
                cy0 = y + int(round(idx * h / slot.capacity))
                cy1 = y + int(round((idx + 1) * h / slot.capacity))
                cv2.putText(
                    out,
                    "UNREADABLE",
                    (x + 4, (cy0 + cy1) // 2),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    colors["alert"],
                    1,
                    cv2.LINE_AA,
                )
    return out


def run_ui(
    config_path: str | Path = "config/slots.example.json",
    db_path: Optional[Path] = None,
    image_path: Optional[Path] = None,
    camera_index: int = 0,
    hash_threshold: Optional[int] = None,
    fill_direction: Optional[str] = None,
    refs: Optional[str] = None,
    enable_hash_fallback: Optional[bool] = None,
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

    overrides = {
        "hash_threshold": hash_threshold,
        "fill_direction": fill_direction,
        "reference_images_dir": refs,
        "enable_hash_fallback": enable_hash_fallback,
    }
    slots, options = load_config(config_path, overrides=overrides)
    store = WarehouseStore(db_path or default_db_path())
    store.seed_common_skus()

    snapshots_dir = Path("data/snapshots")
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")

    app = ctk.CTk()
    app.title("Warehouse Slots — Offline UI (Phase 4)")
    app.geometry("1100x760")

    state: Dict[str, Any] = {
        "frame": None,
        "report": None,
        "cap": None,
        "live": False,
        "photo": None,
        "still_path": Path(image_path) if image_path else None,
        "options": options,
    }

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

    alert_box = ctk.CTkTextbox(right, width=340, height=100, text_color="#cc2222")
    alert_box.pack(padx=8, pady=4, fill="x")
    alert_box.insert("1.0", "No UNREADABLE alerts.\n")
    alert_box.configure(state="disabled")

    table = ctk.CTkTextbox(right, width=340, height=240)
    table.pack(padx=8, pady=4, fill="x")
    table.insert("1.0", "Per-slot results will appear here.\n")
    table.configure(state="disabled")

    def set_status(msg: str) -> None:
        status_var.set(msg)

    def refresh_alerts(report: Optional[Dict[str, Any]]) -> None:
        alert_box.configure(state="normal")
        alert_box.delete("1.0", "end")
        text = format_alerts_text(report or {})
        if not text:
            alert_box.insert("1.0", "No UNREADABLE / hash alerts.\n")
        else:
            alert_box.insert("1.0", text + "\n")
        alert_box.configure(state="disabled")

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
            if s.get("hash_filled"):
                lines.append(f"     hash_fallback_cells={s['hash_filled']}")
        table.insert("1.0", "\n".join(lines) + "\n")
        table.configure(state="disabled")

    def show_frame(frame_bgr: np.ndarray) -> None:
        overlay = draw_overlays(frame_bgr, slots, state["report"])
        rgb = _bgr_to_rgb(overlay)
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
        state["photo"] = photo
        preview_label.configure(image=photo, text="")

    def analyze_current() -> None:
        frame = state["frame"]
        if frame is None:
            set_status("No frame to analyze")
            return
        report = analyze_image(frame, slots, options=state["options"])
        state["report"] = report
        refresh_table(report)
        refresh_alerts(report)
        show_frame(frame)
        n_alert = len(report.get("alerts") or [])
        if n_alert:
            set_status(f"Scan complete — {n_alert} UNREADABLE/hash alert(s)")
        else:
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
        refresh_alerts(None)
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

    if state["still_path"] and state["still_path"].is_file():
        load_still(state["still_path"])
        analyze_current()
    else:
        board = Path("fixtures/board.png")
        if board.is_file():
            load_still(board)

    app.mainloop()
    return 0


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
