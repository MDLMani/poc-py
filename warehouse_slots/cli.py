"""CLI entry points for warehouse_slots (Phases 1–3)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

from .config import load_slots
from .slot_pipeline import analyze_image
from .store import WarehouseStore, default_db_path


def _store_from_args(args: argparse.Namespace) -> WarehouseStore:
    db = getattr(args, "db", None)
    return WarehouseStore(Path(db) if db else default_db_path())


def cmd_analyze(args: argparse.Namespace) -> int:
    image_path = Path(args.image)
    if not image_path.is_file():
        print(f"error: image not found: {image_path}", file=sys.stderr)
        return 1

    slots = load_slots(args.config)
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        print(f"error: failed to read image: {image_path}", file=sys.stderr)
        return 1

    report = analyze_image(image, slots)
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

    scan = {}
    if args.config:
        slots = load_slots(args.config)
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            print(f"error: failed to read image: {image_path}", file=sys.stderr)
            return 1
        scan = analyze_image(image, slots)
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
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="warehouse_slots",
        description="Offline warehouse slot QR scanner (Phases 1–3)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_an = sub.add_parser("analyze", help="Analyze an image against a slots config")
    p_an.add_argument("image", help="Path to input image (PNG/JPEG)")
    p_an.add_argument("--config", required=True, help="Path to slots JSON config")
    p_an.set_defaults(func=cmd_analyze)

    # Phase 2 — catalog
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

    # Phase 2 — confirm IN/OUT
    for direction, help_txt in (("in", "Confirm inbound"), ("out", "Confirm outbound")):
        p = sub.add_parser(direction, help=help_txt)
        p.add_argument("image", help="Capture/scan image path to store")
        p.add_argument("--config", default=None, help="Slots config; analyze before store")
        p.add_argument("--scan-json", default=None, help="Precomputed scan JSON file")
        p.add_argument("--note", default="")
        p.add_argument("--db", default=None)
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

    # Phase 3 — UI
    p_ui = sub.add_parser("ui", help="Launch CustomTkinter desktop UI")
    p_ui.add_argument(
        "--config",
        default="config/slots.example.json",
        help="Slots JSON for overlay + analysis",
    )
    p_ui.add_argument("--db", default=None)
    p_ui.add_argument("--image", default=None, help="Still image mode (skip camera)")
    p_ui.add_argument("--camera", type=int, default=0, help="Camera index for live mode")
    p_ui.set_defaults(func=cmd_ui)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
