"""Generate high-contrast fixture images F1–F5 for Phase 0+ acceptance.

Offline-only after dependencies are installed. Uses qrcode[pil] + Pillow.
OpenCV QRCodeDetector needs large modules and quiet zones — we render
oversized QRs on a pure-white canvas.

Layout (each fixture image is independent; same local ROI origin):
  - F1.png / F2.png / F3.png / F4.png / F5.png — one slot each
  - board.png — F1 | F2 | F3 | F4 | F5 side-by-side (slots.example.json)
  - F4 — companion products (SKU-COMP next to main SKU-ALPHA)
  - F5 — empty column (no product in any cell)
  - F_unreadable.png — QR + noise blotch + empties
  - F_hash_fallback.png — visual label (no QR) matched via ImageHash refs
  - refs/SKU-VISUAL.png — local SKU reference for hash fallback demos
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import qrcode
from PIL import Image, ImageDraw


# Geometry tuned for reliable OpenCV decode
CELL_W = 240
CELL_H = 240
QR_BOX_SIZE = 8  # pixels per QR module
QR_BORDER = 2  # quiet-zone modules
SLOT_ORIGIN_X = 40
SLOT_ORIGIN_Y = 40
GAP = 40  # horizontal gap between slots on the board
CANVAS_PAD_RIGHT = 40
CANVAS_PAD_BOTTOM = 40


def _make_qr_image(payload: str) -> Image.Image:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=QR_BOX_SIZE,
        border=QR_BORDER,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def _fit_qr_in_cell(qr_img: Image.Image, cell_w: int, cell_h: int) -> Image.Image:
    """Center QR in a white cell; scale down only if larger than cell."""
    cell = Image.new("RGB", (cell_w, cell_h), (255, 255, 255))
    qw, qh = qr_img.size
    max_w, max_h = cell_w - 8, cell_h - 8
    if qw > max_w or qh > max_h:
        scale = min(max_w / qw, max_h / qh)
        new_size = (max(1, int(qw * scale)), max(1, int(qh * scale)))
        qr_img = qr_img.resize(new_size, Image.Resampling.NEAREST)
        qw, qh = qr_img.size
    x = (cell_w - qw) // 2
    y = (cell_h - qh) // 2
    cell.paste(qr_img, (x, y))
    return cell


def _make_noise_cell(cell_w: int, cell_h: int) -> Image.Image:
    """Dark blotch that is occupied but not a valid QR → UNREADABLE."""
    import random

    cell = Image.new("RGB", (cell_w, cell_h), (255, 255, 255))
    pixels = cell.load()
    rng = random.Random(42)
    for _ in range(40):
        x0 = rng.randint(10, cell_w - 40)
        y0 = rng.randint(10, cell_h - 40)
        bw = rng.randint(8, 30)
        bh = rng.randint(8, 30)
        for y in range(y0, min(cell_h - 1, y0 + bh)):
            for x in range(x0, min(cell_w - 1, x0 + bw)):
                pixels[x, y] = (20, 20, 20)
    for x in range(20, cell_w - 20):
        for t in range(4):
            pixels[x, 20 + t] = (0, 0, 0)
            pixels[x, cell_h - 24 + t] = (0, 0, 0)
    for y in range(20, cell_h - 20):
        for t in range(4):
            pixels[20 + t, y] = (0, 0, 0)
            pixels[cell_w - 24 + t, y] = (0, 0, 0)
    return cell


def _make_visual_sku_label(cell_w: int = CELL_W, cell_h: int = CELL_H) -> Image.Image:
    """Distinctive non-QR product label for ImageHash fallback demos.

    High-contrast colored geometry that is clearly occupied but not a QR.
    """
    cell = Image.new("RGB", (cell_w, cell_h), (245, 245, 250))
    draw = ImageDraw.Draw(cell)
    # Outer frame
    draw.rectangle([12, 12, cell_w - 13, cell_h - 13], outline=(20, 40, 120), width=6)
    # Diagonal stripe band
    for i in range(-cell_h, cell_w, 18):
        draw.line([(i, 0), (i + cell_h, cell_h)], fill=(200, 40, 40), width=8)
    # Center badge
    cx, cy = cell_w // 2, cell_h // 2
    draw.ellipse([cx - 55, cy - 55, cx + 55, cy + 55], fill=(30, 140, 70), outline=(0, 0, 0), width=3)
    draw.rectangle([cx - 40, cy - 18, cx + 40, cy + 18], fill=(255, 220, 40))
    # Corner chevrons (unique fingerprint for phash)
    draw.polygon([(20, 20), (70, 20), (20, 70)], fill=(0, 90, 180))
    draw.polygon(
        [(cell_w - 20, cell_h - 20), (cell_w - 70, cell_h - 20), (cell_w - 20, cell_h - 70)],
        fill=(180, 0, 120),
    )
    return cell


def _make_empty_cell(cell_w: int = CELL_W, cell_h: int = CELL_H) -> Image.Image:
    """Visually marked empty cell that still counts as EMPTY (near-white).

    Light gray frame only — stays above the occupied threshold (no dark ink).
    """
    cell = Image.new("RGB", (cell_w, cell_h), (255, 255, 255))
    draw = ImageDraw.Draw(cell)
    # Soft frame (all channels >= 210 so QR occupancy heuristic stays EMPTY)
    draw.rectangle([4, 4, cell_w - 5, cell_h - 5], outline=(220, 220, 220), width=2)
    draw.rectangle([10, 10, cell_w - 11, cell_h - 11], outline=(235, 235, 235), width=1)
    return cell


def render_slot_stack(
    payloads: Sequence[Optional[str]],
    cell_w: int = CELL_W,
    cell_h: int = CELL_H,
) -> Image.Image:
    """Return just the slot rectangle (capacity × cell), no outer padding."""
    capacity = len(payloads)
    slot = Image.new("RGB", (cell_w, cell_h * capacity), (255, 255, 255))
    for i, payload in enumerate(payloads):
        if payload is None:
            cell = _make_empty_cell(cell_w, cell_h)
        elif payload == "__NOISE__":
            cell = _make_noise_cell(cell_w, cell_h)
        elif payload == "__VISUAL__":
            cell = _make_visual_sku_label(cell_w, cell_h)
        else:
            cell = _fit_qr_in_cell(_make_qr_image(payload), cell_w, cell_h)
        slot.paste(cell, (0, i * cell_h))
    return slot


def pad_slot(slot: Image.Image) -> Tuple[Image.Image, List[int]]:
    """Place slot at (SLOT_ORIGIN_X, SLOT_ORIGIN_Y) on a white canvas."""
    canvas_w = SLOT_ORIGIN_X + slot.size[0] + CANVAS_PAD_RIGHT
    canvas_h = SLOT_ORIGIN_Y + slot.size[1] + CANVAS_PAD_BOTTOM
    canvas = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))
    canvas.paste(slot, (SLOT_ORIGIN_X, SLOT_ORIGIN_Y))
    roi = [SLOT_ORIGIN_X, SLOT_ORIGIN_Y, slot.size[0], slot.size[1]]
    return canvas, roi


def slot_roi_local(capacity: int) -> List[int]:
    return [SLOT_ORIGIN_X, SLOT_ORIGIN_Y, CELL_W, CELL_H * capacity]


def generate_all(out_dir: Path) -> dict:
    """Write F1–F5.png, board.png, hash fixtures, and return configs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    refs_dir = out_dir / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)

    f1_payloads: List[Optional[str]] = ["SKU-ALPHA"] * 10
    f2_payloads: List[Optional[str]] = [
        "SKU-A",
        "SKU-A",
        "SKU-B",
        "SKU-B",
        "SKU-C",
        "SKU-C",
    ]
    f3_payloads: List[Optional[str]] = [
        "SKU-X",
        "SKU-Y",
        "SKU-Z",
        None,
        None,
        None,
        None,
        None,
    ]
    # Companion products paired with a main SKU (ALPHA + COMP alternating).
    f4_payloads: List[Optional[str]] = [
        "SKU-ALPHA",
        "SKU-COMP",
        "SKU-ALPHA",
        "SKU-COMP",
        "SKU-COMP",
        "SKU-COMP",
    ]
    # Empty column — capacity reserved, no products.
    f5_payloads: List[Optional[str]] = [None, None, None, None, None, None]

    stacks = {
        "F1": render_slot_stack(f1_payloads),
        "F2": render_slot_stack(f2_payloads),
        "F3": render_slot_stack(f3_payloads),
        "F4": render_slot_stack(f4_payloads),
        "F5": render_slot_stack(f5_payloads),
    }

    # Individual fixture images (single slot each)
    single_configs = {}
    for name, stack in stacks.items():
        img, roi = pad_slot(stack)
        img.save(out_dir / f"{name}.png")
        single_configs[name] = {
            "id": name,
            "roi": roi,
            "capacity": stack.size[1] // CELL_H,
        }

    # Combined board: F1 | F2 | F3 | F4 | F5 left-to-right
    max_h = max(s.size[1] for s in stacks.values())
    board_names = ("F1", "F2", "F3", "F4", "F5")
    board_w = (
        SLOT_ORIGIN_X
        + sum(stacks[n].size[0] for n in board_names)
        + GAP * (len(board_names) - 1)
        + CANVAS_PAD_RIGHT
    )
    board_h = SLOT_ORIGIN_Y + max_h + CANVAS_PAD_BOTTOM
    board = Image.new("RGB", (board_w, board_h), (255, 255, 255))
    draw = ImageDraw.Draw(board)
    # Short labels so the 5 columns are obvious in the PNG preview.
    board_labels = {
        "F1": "F1",
        "F2": "F2",
        "F3": "F3",
        "F4": "F4 companion",
        "F5": "F5 empty",
    }

    x = SLOT_ORIGIN_X
    board_slots = []
    for name in board_names:
        stack = stacks[name]
        board.paste(stack, (x, SLOT_ORIGIN_Y))
        draw.text((x, 8), board_labels[name], fill=(120, 120, 120))
        board_slots.append(
            {
                "id": name,
                "roi": [x, SLOT_ORIGIN_Y, stack.size[0], stack.size[1]],
                "capacity": stack.size[1] // CELL_H,
            }
        )
        x += stack.size[0] + GAP

    board.save(out_dir / "board.png")

    # UNREADABLE fixture: 1 valid QR + 1 noise blotch + 2 empty (capacity 4)
    ur_payloads: List[Optional[str]] = ["SKU-UR", "__NOISE__", None, None]
    ur_stack = render_slot_stack(ur_payloads)
    ur_img, ur_roi = pad_slot(ur_stack)
    ur_img.save(out_dir / "F_unreadable.png")
    single_configs["F_unreadable"] = {
        "id": "F_unreadable",
        "roi": ur_roi,
        "capacity": 4,
    }

    # Phase 4 ImageHash fallback: visual label cell (no QR) + empty
    visual = _make_visual_sku_label()
    visual.save(refs_dir / "SKU-VISUAL.png")
    # Also keep a QR-based ref for negative tests (should not match noise)
    _fit_qr_in_cell(_make_qr_image("SKU-ALPHA"), CELL_W, CELL_H).save(
        refs_dir / "SKU-ALPHA.png"
    )

    hash_payloads: List[Optional[str]] = ["__VISUAL__", None, None, None]
    hash_stack = render_slot_stack(hash_payloads)
    hash_img, hash_roi = pad_slot(hash_stack)
    hash_img.save(out_dir / "F_hash_fallback.png")
    single_configs["F_hash_fallback"] = {
        "id": "F_hash_fallback",
        "roi": hash_roi,
        "capacity": 4,
    }

    return {
        "board_slots": board_slots,
        "single_slots": single_configs,
        "refs_dir": str(refs_dir),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Phase 0/4 fixtures")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("fixtures"),
        help="Output directory for PNGs (default: fixtures/)",
    )
    parser.add_argument(
        "--write-config",
        type=Path,
        default=None,
        help="Write board slots JSON here (default: also writes singles next to it)",
    )
    args = parser.parse_args(argv)

    result = generate_all(args.out)
    print(f"Wrote fixtures to {args.out.resolve()}")
    for s in result["board_slots"]:
        print(f"  board {s['id']}: roi={s['roi']} capacity={s['capacity']}")
    print(f"  refs → {result['refs_dir']}")

    config_path = args.write_config
    if config_path is None:
        guess = Path("config/slots.example.json")
        config_path = guess if guess.parent.is_dir() else None

    if config_path is not None:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        runtime = {
            "hash_threshold": 12,
            "fill_direction": "top_to_bottom",
            "reference_images_dir": "../fixtures/refs",
            "enable_hash_fallback": True,
            "slots": result["board_slots"],
        }

        with config_path.open("w", encoding="utf-8") as f:
            json.dump(runtime, f, indent=2)
            f.write("\n")
        print(f"Wrote board config to {config_path.resolve()}")

        for name, slot in result["single_slots"].items():
            single_path = config_path.parent / f"{name}.json"
            single_cfg: dict = {"slots": [slot]}
            if name in ("F_hash_fallback", "F_unreadable"):
                single_cfg.update(
                    {
                        "hash_threshold": 12,
                        "fill_direction": "top_to_bottom",
                        "reference_images_dir": "../fixtures/refs",
                        "enable_hash_fallback": True,
                    }
                )
            with single_path.open("w", encoding="utf-8") as f:
                json.dump(single_cfg, f, indent=2)
                f.write("\n")
            print(f"Wrote single-slot config to {single_path.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
