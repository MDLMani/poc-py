"""CLI entry points for warehouse_slots (Phases 1–4)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

import cv2

from .config import load_config
from .slot_pipeline import analyze_image, format_alerts_text
from .availability import check_availability
from .store import WarehouseStore, default_db_path


def _store_from_args(args: argparse.Namespace) -> WarehouseStore:
    db = getattr(args, "db", None)
    return WarehouseStore(Path(db) if db else default_db_path())


def _option_overrides(args: argparse.Namespace) -> Dict[str, Any]:
    enable = False if getattr(args, "no_hash_fallback", False) else None
    return {
        "hash_threshold": getattr(args, "hash_threshold", None),
        "fill_direction": getattr(args, "fill_direction", None),
        "reference_images_dir": getattr(args, "refs", None),
        "enable_hash_fallback": enable,
    }


def _add_phase4_knobs(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--hash-threshold",
        type=int,
        default=None,
        help="ImageHash Hamming distance threshold (default from config or 12)",
    )
    p.add_argument(
        "--fill-direction",
        choices=["top_to_bottom", "bottom_to_top"],
        default=None,
        help="Cell index 0 at top or bottom of ROI",
    )
    p.add_argument(
        "--refs",
        default=None,
        help="Directory of SKU reference images for ImageHash fallback",
    )
    p.add_argument(
        "--no-hash-fallback",
        action="store_true",
        help="Disable ImageHash fallback even if refs are configured",
    )


def _emit_alerts(report: dict) -> None:
    text = format_alerts_text(report)
    if text:
        print(text, file=sys.stderr)


def cmd_analyze(args: argparse.Namespace) -> int:
    image_path = Path(args.image)
    if not image_path.is_file():
        print(f"error: image not found: {image_path}", file=sys.stderr)
        return 1

    slots, options = load_config(args.config, overrides=_option_overrides(args))
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        print(f"error: failed to read image: {image_path}", file=sys.stderr)
        return 1

    report = analyze_image(image, slots, options=options)
    _emit_alerts(report)
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def cmd_sku_add(args: argparse.Namespace) -> int:
    store = _store_from_args(args)
    sku = store.upsert_sku(args.sku_id, args.name, args.description or "")
    json.dump(sku.to_dict(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def cmd_sku_list(args: argparse.Namespace) -> int:
    store = _store_from_args(args)
    if args.seed:
        store.seed_common_skus()
    rows = [s.to_dict() for s in store.list_skus()]
    json.dump({"skus": rows}, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def cmd_confirm(args: argparse.Namespace) -> int:
    """Confirm IN or OUT: analyze image (optional) and persist event."""
    store = _store_from_args(args)
    direction = args.direction.upper()
    image_path = Path(args.image)
    if not image_path.is_file():
        print(f"error: image not found: {image_path}", file=sys.stderr)
        return 1

    scan: dict = {}
    if args.config:
        slots, options = load_config(args.config, overrides=_option_overrides(args))
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            print(f"error: failed to read image: {image_path}", file=sys.stderr)
            return 1
        scan = analyze_image(image, slots, options=options)
        _emit_alerts(scan)
    elif args.scan_json:
        scan = json.loads(Path(args.scan_json).read_text(encoding="utf-8"))

    event = store.record_event(
        direction=direction,
        image_path=image_path.resolve(),
        scan=scan,
        note=args.note or "",
    )
    payload = event.to_dict()
    payload["sku_lines"] = store.event_sku_lines(event.id)
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    store = _store_from_args(args)
    events = []
    for e in store.list_events(limit=args.limit):
        d = e.to_dict()
        d["sku_lines"] = store.event_sku_lines(e.id)
        events.append(d)
    json.dump({"events": events}, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .local_api import serve

    serve(host=args.host, port=args.port, db_path=Path(args.db) if args.db else None)
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    from .ui_app import run_ui

    return run_ui(
        config_path=args.config,
        db_path=Path(args.db) if args.db else default_db_path(),
        image_path=Path(args.image) if args.image else None,
        camera_index=args.camera,
        hash_threshold=args.hash_threshold,
        fill_direction=args.fill_direction,
        refs=args.refs,
        enable_hash_fallback=(False if args.no_hash_fallback else None),
    )



def cmd_library_add(args: argparse.Namespace) -> int:
    store = _store_from_args(args)
    try:
        entry = store.add_library_image(
            Path(args.image),
            qr_payload=args.qr,
            name=args.name or "",
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    json.dump(entry.to_dict(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def cmd_library_list(args: argparse.Namespace) -> int:
    store = _store_from_args(args)
    rows = [e.to_dict() for e in store.list_library()]
    json.dump({"library": rows}, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def cmd_library_remove(args: argparse.Namespace) -> int:
    store = _store_from_args(args)
    ok = store.remove_library(int(args.entry_id))
    if not ok:
        print(f"error: library id not found: {args.entry_id}", file=sys.stderr)
        return 1
    json.dump({"removed": int(args.entry_id)}, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    store = _store_from_args(args)
    qr_list: list[str] = []
    if getattr(args, "qr", None):
        qr_list.extend(args.qr)
    library_ids = list(getattr(args, "library_id", None) or [])
    if not args.image and not qr_list and not library_ids:
        print(
            "error: provide --image (with --config), --qr, and/or --library-id",
            file=sys.stderr,
        )
        return 1
    try:
        report = check_availability(
            store,
            image_path=Path(args.image) if args.image else None,
            config_path=args.config,
            qr_payloads=qr_list or None,
            library_ids=library_ids or None,
            option_overrides=_option_overrides(args) if args.image else None,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if report.get("scan"):
        _emit_alerts(report["scan"])
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def cmd_rack_verify(args: argparse.Namespace) -> int:
    """Analyze a photo against a live rack JSON and print PASS/FAIL."""
    from .rack_config import load_rack
    from .rack_pipeline import analyze_and_verify

    image_path = Path(args.image)
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        print(f"error: cannot read image: {image_path}", file=sys.stderr)
        return 1
    try:
        rack = load_rack(args.rack)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: rack config: {exc}", file=sys.stderr)
        return 1
    report = analyze_and_verify(image, rack)
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    summary = report.get("verification", {}).get("summary") or {}
    return 0 if summary.get("all_pass") else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="warehouse_slots",
        description="Offline warehouse slot QR scanner (Phases 1–4 + image library)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_an = sub.add_parser("analyze", help="Analyze an image against a slots config")
    p_an.add_argument("image", help="Path to input image (PNG/JPEG)")
    p_an.add_argument("--config", required=True, help="Path to slots JSON config")
    _add_phase4_knobs(p_an)
    p_an.set_defaults(func=cmd_analyze)

    p_sku = sub.add_parser("sku", help="SKU catalog commands")
    sku_sub = p_sku.add_subparsers(dest="sku_cmd", required=True)

    p_sku_add = sku_sub.add_parser("add", help="Add or update a SKU")
    p_sku_add.add_argument("sku_id")
    p_sku_add.add_argument("name")
    p_sku_add.add_argument("--description", default="")
    p_sku_add.add_argument("--db", default=None)
    p_sku_add.set_defaults(func=cmd_sku_add)

    p_sku_list = sku_sub.add_parser("list", help="List SKUs")
    p_sku_list.add_argument("--db", default=None)
    p_sku_list.add_argument("--seed", action="store_true", help="Seed demo SKUs first")
    p_sku_list.set_defaults(func=cmd_sku_list)

    for direction, help_txt in (("in", "Confirm inbound"), ("out", "Confirm outbound")):
        p = sub.add_parser(direction, help=help_txt)
        p.add_argument("image", help="Capture/scan image path to store")
        p.add_argument("--config", default=None, help="Slots config; analyze before store")
        p.add_argument("--scan-json", default=None, help="Precomputed scan JSON file")
        p.add_argument("--note", default="")
        p.add_argument("--db", default=None)
        _add_phase4_knobs(p)
        p.set_defaults(func=cmd_confirm, direction=direction.upper())

    p_ev = sub.add_parser("events", help="List recent IN/OUT events")
    p_ev.add_argument("--db", default=None)
    p_ev.add_argument("--limit", type=int, default=50)
    p_ev.set_defaults(func=cmd_events)

    p_serve = sub.add_parser("serve", help="Start offline local API (127.0.0.1)")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--db", default=None)
    p_serve.set_defaults(func=cmd_serve)

    p_ui = sub.add_parser("ui", help="Launch Electron desktop UI")
    p_ui.add_argument(
        "--config",
        default="config/slots.example.json",
        help="Slots JSON for overlay + analysis",
    )
    p_ui.add_argument("--db", default=None)
    p_ui.add_argument("--image", default=None, help="Still image to open on launch")
    p_ui.add_argument(
        "--camera",
        type=int,
        default=0,
        help=argparse.SUPPRESS,  # removed from UI; kept so old scripts don't break
    )
    _add_phase4_knobs(p_ui)
    p_ui.set_defaults(func=cmd_ui)

    p_lib = sub.add_parser("library", help="Offline image library (image + QR payload)")
    lib_sub = p_lib.add_subparsers(dest="library_cmd", required=True)

    p_lib_add = lib_sub.add_parser("add", help="Register image file + QR payload (SKU id)")
    p_lib_add.add_argument("--image", required=True, help="Path to image file")
    p_lib_add.add_argument("--qr", required=True, help="QR payload / SKU id")
    p_lib_add.add_argument("--name", default="", help="Optional display label")
    p_lib_add.add_argument("--db", default=None)
    p_lib_add.set_defaults(func=cmd_library_add)

    p_lib_list = lib_sub.add_parser("list", help="List library images")
    p_lib_list.add_argument("--db", default=None)
    p_lib_list.set_defaults(func=cmd_library_list)

    p_lib_rm = lib_sub.add_parser("remove", help="Remove a library entry by id")
    p_lib_rm.add_argument("entry_id", type=int, help="Library entry id")
    p_lib_rm.add_argument("--db", default=None)
    p_lib_rm.set_defaults(func=cmd_library_remove)

    p_check = sub.add_parser(
        "check",
        help="Check availability: image/QR vs SQLite on-hand stock",
    )
    p_check.add_argument("--image", default=None, help="Board/still image to analyze")
    p_check.add_argument("--config", default=None, help="Slots config (required with --image)")
    p_check.add_argument(
        "--qr",
        action="append",
        default=None,
        help="QR payload / SKU id to look up (repeatable)",
    )
    p_check.add_argument(
        "--library-id",
        action="append",
        type=int,
        default=None,
        help="Library entry id whose QR to include (repeatable)",
    )
    p_check.add_argument("--db", default=None)
    _add_phase4_knobs(p_check)
    p_check.set_defaults(func=cmd_check)

    p_rack = sub.add_parser(
        "rack-verify",
        help="Live rack: verify expected product IDs vs photo cell scan",
    )
    p_rack.add_argument("--image", required=True, help="Rack / crate photo")
    p_rack.add_argument("--rack", required=True, help="Rack JSON (config/racks/…)")
    p_rack.set_defaults(func=cmd_rack_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
