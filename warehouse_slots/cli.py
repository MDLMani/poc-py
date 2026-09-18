"""CLI entry points for warehouse_slots."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

from .config import load_slots
from .slot_pipeline import analyze_image


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="warehouse_slots",
        description="Offline warehouse slot QR scanner (Phase 0)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_an = sub.add_parser("analyze", help="Analyze an image against a slots config")
    p_an.add_argument("image", help="Path to input image (PNG/JPEG)")
    p_an.add_argument(
        "--config",
        required=True,
        help="Path to slots JSON config",
    )
    p_an.set_defaults(func=cmd_analyze)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
