"""CustomTkinter desktop UI for offline warehouse slot scanning.

Features:
  - Primary: Check availability (still/library image → SKU vs on-hand table)
  - Image library: register image + QR payload; gallery list
  - Still image mode (load PNG/JPG from disk)
  - Slot ROI overlays + analyze / Confirm IN/OUT
  - Fully offline (no network imports in analysis path)
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import cv2
import numpy as np

from .config import load_config
from .availability import check_availability
from .slot_pipeline import analyze_image
from .store import WarehouseStore, default_db_path


def _bgr_to_rgb(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def _format_columns(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    aligns: Optional[Sequence[str]] = None,
    min_widths: Optional[Sequence[int]] = None,
) -> List[str]:
    """Fixed-width monospace table; long lines scroll horizontally in the UI."""
    ncols = len(headers)
    aligns_l = list(aligns) if aligns is not None else ["l"] * ncols
    widths = list(min_widths) if min_widths is not None else [0] * ncols
    while len(aligns_l) < ncols:
        aligns_l.append("l")
    while len(widths) < ncols:
        widths.append(0)

    str_rows: List[List[str]] = []
    for row in rows:
        cells = [("" if c is None else str(c)) for c in row]
        if len(cells) < ncols:
            cells.extend([""] * (ncols - len(cells)))
        str_rows.append(cells[:ncols])

    for i in range(ncols):
        widths[i] = max(
            widths[i],
            len(str(headers[i])),
            *(len(r[i]) for r in str_rows),
        )

    def fmt_row(cells: Sequence[str]) -> str:
        parts: List[str] = []
        for i, cell in enumerate(cells):
            w = widths[i]
            if aligns_l[i] == "r":
                parts.append(f"{cell:>{w}}")
            else:
                parts.append(f"{cell:<{w}}")
        return "  ".join(parts)

    hdr = fmt_row([str(h) for h in headers])
    sep = "-" * len(hdr)
    out = [hdr, sep]
    out.extend(fmt_row(r) for r in str_rows)
    return out


def draw_overlays(
    image_bgr: np.ndarray,
    slots: List,
    report: Optional[Dict[str, Any]] = None,
    *,
    scale: float = 1.0,
) -> np.ndarray:
    """Draw ROI rectangles and compact status labels onto a copy.

    Labels are drawn *inside* each slot (with a dark backing) so adjacent
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
    # Keep text readable on small previews without spilling into neighbors.
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


def run_ui(
    config_path: str | Path = "config/slots.example.json",
    db_path: Optional[Path] = None,
    image_path: Optional[Path] = None,
    camera_index: int = 0,  # kept for CLI compatibility; unused
    hash_threshold: Optional[int] = None,
    fill_direction: Optional[str] = None,
    refs: Optional[str] = None,
    enable_hash_fallback: Optional[bool] = None,
) -> int:
    try:
        import customtkinter as ctk
        from PIL import Image, ImageTk
        import tkinter as tk
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
    app.title("Warehouse Slots — Offline UI (library + availability)")
    app.geometry("1280x820")
    app.minsize(960, 640)

    state: Dict[str, Any] = {
        "frame": None,
        "report": None,
        "photo": None,
        "photo_item": None,
        "still_path": Path(image_path) if image_path else None,
        "options": options,
        "busy": False,
        "disp_size": (0, 0),
        "avail_skus": [],
    }

    left = ctk.CTkFrame(app)
    left.pack(side="left", fill="both", expand=True, padx=8, pady=8)
    right = ctk.CTkScrollableFrame(app, width=400)
    right.pack(side="right", fill="y", padx=8, pady=8)

    preview_canvas = tk.Canvas(
        left,
        highlightthickness=0,
        bg="#1a1a1a",
        width=560,
        height=640,
    )
    preview_canvas.pack(fill="both", expand=True)
    preview_canvas.create_text(
        280,
        320,
        text="No image loaded",
        fill="#888888",
        font=("Helvetica", 14),
        tags=("hint",),
    )
    # Persistent loading badge — toggled, never clears the preview image.
    preview_canvas.create_rectangle(
        8, 8, 168, 36, fill="#111111", outline="#444444", tags=("busy_bg",), state="hidden"
    )
    preview_canvas.create_text(
        88,
        22,
        text="Loading…",
        fill="#f0c040",
        font=("Helvetica", 12, "bold"),
        tags=("busy_txt",),
        state="hidden",
    )

    status_var = ctk.StringVar(value="Ready (offline)")
    ctk.CTkLabel(right, textvariable=status_var, wraplength=320, justify="left").pack(
        anchor="w", padx=8, pady=(8, 4)
    )

    import tkinter.font as tkfont

    _mono_family = "Courier"
    _available = set(tkfont.families())
    for _candidate in ("Consolas", "DejaVu Sans Mono", "Liberation Mono", "Courier"):
        if _candidate in _available:
            _mono_family = _candidate
            break
    mono = ctk.CTkFont(family=_mono_family, size=12)

    def _make_panel(*, height: int, text_color: Optional[str] = None) -> Any:
        # wrap=none → horizontal scrollbar when rows are wider than the panel
        kw: Dict[str, Any] = {
            "width": 360,
            "height": height,
            "font": mono,
            "wrap": "none",
            "activate_scrollbars": True,
        }
        if text_color is not None:
            kw["text_color"] = text_color
        box = ctk.CTkTextbox(right, **kw)
        box.pack(padx=8, pady=4, fill="x")
        return box

    alert_box = _make_panel(height=100, text_color="#cc2222")
    alert_box.insert("1.0", "No UNREADABLE alerts.\n")
    alert_box.configure(state="disabled")

    table = _make_panel(height=280)
    table.insert("1.0", "Availability / per-slot results will appear here.\n")
    table.configure(state="disabled")

    def set_status(msg: str) -> None:
        status_var.set(msg)

    def _set_busy(busy: bool, label: str = "Loading…") -> None:
        state["busy"] = busy
        st = "normal" if busy else "hidden"
        preview_canvas.itemconfigure("busy_bg", state=st)
        preview_canvas.itemconfigure("busy_txt", state=st, text=label)
        if busy:
            preview_canvas.tag_raise("busy_bg")
            preview_canvas.tag_raise("busy_txt")

    def _set_panel_text(box: Any, lines: List[str]) -> None:
        body = "\n".join(lines) + "\n"
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", body)
        box.configure(state="disabled")
        # Jump to top-left so alignment is visible; user can scroll L↔R / up↔down.
        try:
            box.see("1.0")
            box._textbox.xview_moveto(0)  # noqa: SLF001 — CTk internal text widget
        except Exception:
            pass

    def _set_table_text(lines: List[str]) -> None:
        _set_panel_text(table, lines)

    def refresh_alerts(report: Optional[Dict[str, Any]]) -> None:
        alerts = (report or {}).get("alerts") or []
        if not alerts:
            _set_panel_text(alert_box, ["(no UNREADABLE / hash alerts)"])
            return
        rows = []
        for a in alerts:
            s = str(a)
            kind = "ALERT"
            if "HASH_FALLBACK" in s:
                kind = "HASH"
            elif "UNREADABLE" in s:
                kind = "UNREAD"
            rows.append((kind, s))
        lines = _format_columns(
            ["kind", "message"],
            rows,
            aligns=("l", "l"),
            min_widths=(6, 24),
        )
        _set_panel_text(alert_box, lines)

    def refresh_table(report: Optional[Dict[str, Any]]) -> None:
        if not report:
            _set_table_text(["(no scan yet)"])
            return
        rows = []
        for s in report.get("slots", []):
            counts = json.dumps(s.get("counts") or {}, sort_keys=True)
            rows.append(
                (
                    s.get("slot_id", ""),
                    s.get("filled", 0),
                    s.get("empty", 0),
                    s.get("unreadable", 0),
                    counts,
                )
            )
            if s.get("hash_filled"):
                rows.append(("", "", "", "", f"hash_fallback_cells={s['hash_filled']}"))
        lines = _format_columns(
            ["slot", "filled", "empty", "unread", "counts"],
            rows,
            aligns=("l", "r", "r", "r", "l"),
            min_widths=(4, 6, 5, 6, 8),
        )
        _set_table_text(lines)

    def show_frame(frame_bgr: np.ndarray) -> None:
        # Downscale first; swap PhotoImage in-place (no delete→blank→create shutter).
        max_w, max_h = 560, 640
        h, w = frame_bgr.shape[:2]
        scale = min(max_w / w, max_h / h, 1.0)
        if scale < 1.0:
            disp = cv2.resize(
                frame_bgr,
                (max(1, int(w * scale)), max(1, int(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
        else:
            disp = frame_bgr
            scale = 1.0
        overlay = draw_overlays(disp, slots, state["report"], scale=scale)
        rgb = _bgr_to_rgb(overlay)
        pil = Image.fromarray(rgb)
        photo = ImageTk.PhotoImage(pil)
        cw = max(preview_canvas.winfo_width(), 1)
        ch = max(preview_canvas.winfo_height(), 1)
        item = state.get("photo_item")
        if item is None:
            preview_canvas.delete("hint")
            item = preview_canvas.create_image(
                cw // 2, ch // 2, image=photo, anchor="center", tags=("frame",)
            )
            state["photo_item"] = item
        else:
            preview_canvas.itemconfig(item, image=photo)
            preview_canvas.coords(item, cw // 2, ch // 2)
        state["photo"] = photo
        state["disp_size"] = (pil.size[0], pil.size[1])
        preview_canvas.tag_raise("busy_bg")
        preview_canvas.tag_raise("busy_txt")

    def _run_job(label: str, work, on_done) -> None:
        """Run heavy work off the UI thread; apply results on the main thread."""
        if state["busy"]:
            return

        def worker() -> None:
            err: Optional[BaseException] = None
            result: Any = None
            try:
                result = work()
            except BaseException as exc:  # noqa: BLE001 — surface to UI
                err = exc

            def finish() -> None:
                _set_busy(False)
                if err is not None:
                    set_status(f"{label} failed: {err}")
                    return
                on_done(result)

            app.after(0, finish)

        _set_busy(True, label)
        set_status(label)
        threading.Thread(target=worker, daemon=True).start()

    def analyze_current() -> None:
        frame = state["frame"]
        if frame is None:
            set_status("No frame to analyze")
            return

        def work():
            return analyze_image(frame, slots, options=state["options"])

        def done(report: Dict[str, Any]) -> None:
            state["report"] = report
            refresh_table(report)
            refresh_alerts(report)
            show_frame(frame)
            n_alert = len(report.get("alerts") or [])
            if n_alert:
                set_status(f"Scan complete — {n_alert} UNREADABLE/hash alert(s)")
            else:
                set_status("Scan complete (offline)")

        _run_job("Analyzing…", work, done)

    def load_still(path: Path, *, analyze: bool = False) -> None:
        def work():
            img = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if img is None:
                raise FileNotFoundError(f"Failed to load {path}")
            report = None
            if analyze:
                report = analyze_image(img, slots, options=state["options"])
            return img, report

        def done(payload) -> None:
            img, report = payload
            state["frame"] = img
            state["still_path"] = path
            state["report"] = report
            if report is not None:
                refresh_table(report)
                refresh_alerts(report)
                show_frame(img)
                n_alert = len(report.get("alerts") or [])
                if n_alert:
                    set_status(f"Loaded {path.name} — {n_alert} alert(s)")
                else:
                    set_status(f"Loaded {path.name} — scan complete")
            else:
                show_frame(img)
                set_status(
                    f"Loaded image: {path.name}"
                )

        _run_job("Loading image…", work, done)

    def browse_still() -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Open still image (PNG/JPG from disk)",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All", "*.*")],
        )
        if path:
            load_still(Path(path))

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
            set_status("Nothing to confirm — load an image first")
            return
        if state["busy"]:
            set_status("Busy — wait for load/analyze to finish")
            return
        if state["report"] is None:
            analyze_current()
            set_status("Analyze first, then Confirm again")
            return
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

    def refresh_availability(report: Optional[Dict[str, Any]]) -> None:
        if not report:
            _set_table_text(["(no availability check yet)"])
            return
        rows = [
            (
                row.get("sku_id", ""),
                row.get("in_image_count", 0),
                row.get("backend_available", 0),
                row.get("status", ""),
            )
            for row in (report.get("skus") or [])
        ]
        lines = _format_columns(
            ["sku_id", "in_image", "on_hand", "status"],
            rows,
            aligns=("l", "r", "r", "l"),
            min_widths=(12, 8, 7, 18),
        )
        summary = report.get("summary") or {}
        lines.append("-" * len(lines[0]))
        lines.append(
            "summary  "
            + "  ".join(
                f"{k}={summary.get(k, 0)}"
                for k in ("OK", "LOW", "MISSING_IN_BACKEND", "UNKNOWN_SKU")
            )
        )
        lines.append("")
        lines.append("Tip: Show SKU history…  (scroll left/right if columns are wide)")
        state["avail_skus"] = [str(r.get("sku_id", "")) for r in (report.get("skus") or [])]
        _set_table_text(lines)

    def show_sku_history() -> None:
        from tkinter import simpledialog

        skus = state.get("avail_skus") or []
        hint = f" (e.g. {skus[0]})" if skus else " (e.g. SKU-ALPHA)"
        raw = simpledialog.askstring(
            "SKU history",
            f"Enter sku_id to show history{hint}:",
            initialvalue=skus[0] if skus else "",
        )
        if not raw:
            return
        sku_id = raw.strip()
        on_hand = store.on_hand(sku_id).get(sku_id, 0)
        hist = store.sku_history(sku_id, limit=40)
        sku = store.get_sku(sku_id)
        name = sku.name if sku else "(not in catalog)"

        meta = _format_columns(
            ["field", "value"],
            [
                ("sku_id", sku_id),
                ("name", name),
                ("on_hand", on_hand),
            ],
            aligns=("l", "l"),
            min_widths=(8, 12),
        )
        if not hist:
            lines = meta + ["", "(no IN/OUT history for this sku_id)"]
        else:
            rows = [
                (
                    str(h["created_at"])[:19].replace("T", " "),
                    h["direction"],
                    h["qty"],
                    h["event_id"],
                    h.get("note") or "",
                )
                for h in hist
            ]
            hist_lines = _format_columns(
                ["when", "dir", "qty", "ev", "note"],
                rows,
                aligns=("l", "l", "r", "r", "l"),
                min_widths=(19, 3, 4, 4, 8),
            )
            lines = meta + [""] + hist_lines
        _set_table_text(lines)
        set_status(f"History for {sku_id}: {len(hist)} event line(s), on_hand={on_hand}")

    def do_check_availability() -> None:
        frame = state["frame"]
        path = state.get("still_path")
        if frame is None and path is None:
            set_status("Load a still or pick a library image first")
            return

        def work():
            check_path = path
            if check_path is None or not Path(check_path).is_file():
                check_path = snapshot_path()
            return check_availability(
                store,
                image_path=check_path,
                config_path=config_path,
                option_overrides={
                    "hash_threshold": hash_threshold,
                    "fill_direction": fill_direction,
                    "reference_images_dir": refs,
                    "enable_hash_fallback": enable_hash_fallback,
                },
            )

        def done(report: Dict[str, Any]) -> None:
            state["report"] = report.get("scan") or state["report"]
            refresh_availability(report)
            refresh_alerts(report.get("scan") or {})
            if state["frame"] is not None:
                show_frame(state["frame"])
            n = len(report.get("skus") or [])
            set_status(f"Availability: {n} SKU(s) — {report.get('summary')}")

        _run_job("Checking availability…", work, done)

    def refresh_gallery() -> None:
        entries = store.list_library()
        if not entries:
            _set_panel_text(gallery, ["(library empty — add image + QR below)"])
            return
        rows = [
            (e.id, e.qr_payload, e.name[:24], Path(e.image_path).name)
            for e in entries
        ]
        lines = _format_columns(
            ["id", "qr_payload", "name", "path"],
            rows,
            aligns=("r", "l", "l", "l"),
            min_widths=(3, 12, 8, 8),
        )
        _set_panel_text(gallery, lines)

    def library_add() -> None:
        from tkinter import filedialog, simpledialog

        path = filedialog.askopenfilename(
            title="Library image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All", "*.*")],
        )
        if not path:
            return
        qr = simpledialog.askstring("QR payload", "QR payload / SKU id:")
        if not qr:
            set_status("Library add cancelled — QR required")
            return
        name = simpledialog.askstring("Label", "Optional label:", initialvalue=Path(path).stem) or ""
        try:
            entry = store.add_library_image(path, qr_payload=qr, name=name)
        except (FileNotFoundError, ValueError) as exc:
            set_status(f"Library add failed: {exc}")
            return
        refresh_gallery()
        set_status(f"Library #{entry.id} added: {entry.qr_payload}")

    def library_open_selected() -> None:
        from tkinter import simpledialog

        entries = store.list_library()
        if not entries:
            set_status("Library empty")
            return
        raw = simpledialog.askstring(
            "Open library image",
            f"Enter library id (1–{entries[-1].id}):",
        )
        if not raw:
            return
        try:
            lid = int(raw)
        except ValueError:
            set_status("Invalid library id")
            return
        entry = store.get_library(lid)
        if entry is None:
            set_status(f"Library id {lid} not found")
            return
        load_still(Path(entry.image_path))
        # status is set by load_still when the background job finishes

    btn_row = ctk.CTkFrame(right)
    btn_row.pack(fill="x", padx=8, pady=8)
    ctk.CTkButton(
        btn_row,
        text="Check availability",
        fg_color="#1f6aa5",
        command=do_check_availability,
    ).pack(fill="x", pady=2)
    ctk.CTkButton(
        btn_row,
        text="Show SKU history…",
        command=show_sku_history,
    ).pack(fill="x", pady=2)
    ctk.CTkButton(
        btn_row,
        text="Open image…",
        command=browse_still,
    ).pack(fill="x", pady=2)
    ctk.CTkLabel(
        btn_row,
        text="Open image = PNG/JPG from disk",
        text_color="gray",
        font=ctk.CTkFont(size=11),
        wraplength=320,
        justify="left",
    ).pack(anchor="w", pady=(0, 4))
    ctk.CTkButton(btn_row, text="Analyze slots", command=analyze_current).pack(
        fill="x", pady=2
    )
    ctk.CTkButton(
        btn_row, text="Confirm IN", fg_color="#1a7f37", command=lambda: confirm("IN")
    ).pack(fill="x", pady=2)
    ctk.CTkButton(
        btn_row, text="Confirm OUT", fg_color="#b62324", command=lambda: confirm("OUT")
    ).pack(fill="x", pady=2)

    lib_frame = ctk.CTkFrame(right)
    lib_frame.pack(fill="x", padx=8, pady=4)
    ctk.CTkLabel(lib_frame, text="Image library").pack(anchor="w", padx=4)
    gallery = ctk.CTkTextbox(
        lib_frame,
        width=340,
        height=100,
        font=mono,
        wrap="none",
        activate_scrollbars=True,
    )
    gallery.pack(padx=4, pady=2, fill="x")
    gallery.insert("1.0", "")
    gallery.configure(state="disabled")
    ctk.CTkButton(lib_frame, text="Add image + QR…", command=library_add).pack(
        fill="x", pady=2, padx=4
    )
    ctk.CTkButton(
        lib_frame, text="Open library image…", command=library_open_selected
    ).pack(fill="x", pady=2, padx=4)
    refresh_gallery()

    def on_close() -> None:
        app.destroy()

    app.protocol("WM_DELETE_WINDOW", on_close)

    def boot() -> None:
        if state["still_path"] and state["still_path"].is_file():
            load_still(state["still_path"], analyze=True)
            return
        board = Path("fixtures/board.png")
        if board.is_file():
            load_still(board)

    # Show the window first; analyze after so startup doesn't feel frozen.
    app.after(50, boot)

    def _on_preview_resize(_event=None) -> None:
        frame = state.get("frame")
        if frame is None or state.get("busy"):
            return
        # Skip if canvas size barely changed (avoids redraw loops).
        cw = max(preview_canvas.winfo_width(), 1)
        ch = max(preview_canvas.winfo_height(), 1)
        prev = state.get("canvas_size")
        if prev == (cw, ch):
            return
        state["canvas_size"] = (cw, ch)
        show_frame(frame)

    # Debounced resize redraw — avoids flicker while dragging the window.
    _resize_after: Dict[str, Any] = {"id": None}

    def on_preview_configure(event) -> None:
        if event.widget is not preview_canvas:
            return
        aid = _resize_after.get("id")
        if aid is not None:
            app.after_cancel(aid)
        _resize_after["id"] = app.after(200, _on_preview_resize)

    preview_canvas.bind("<Configure>", on_preview_configure)

    try:
        app.mainloop()
    except KeyboardInterrupt:
        on_close()
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
