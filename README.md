# warehouse_slots — Phase 0 (offline QR slot scanner)

Offline warehouse slot QR scanner scaffold. Fixed camera + jig; each product unit has its own QR. Per-slot ROI with known capacity is **cell-banded** into capacity cells; OpenCV `QRCodeDetector` decodes **one QR per cell**.

**Not in Phase 0:** ImageHash matching, network/cloud decode, multi-QR-per-cell.

## Product constraints (locked)

- Offline / no internet at runtime (warehouse floor)
- Fixed camera + jig
- One QR per product unit
- Cell-band ROI → capacity cells → decode one QR per cell
- Report: counts by QR payload (same ×N or mixed with counts), `empty = capacity − filled`, `UNREADABLE` if occupied but QR fails

## Layout

```
poc-py/
├── warehouse_slots/          # Python package
│   ├── config.py             # load slots JSON
│   ├── qr_detect.py          # OpenCV QRCodeDetector on a crop
│   ├── slot_pipeline.py      # cell-band + FILLED/EMPTY/UNREADABLE
│   ├── cli.py / __main__.py  # python -m warehouse_slots analyze ...
│   └── generate_fixtures.py  # F1/F2/F3 (+ board) PNG generator
├── config/
│   ├── slots.example.json    # board: F1|F2|F3 ROIs
│   ├── F1.json F2.json F3.json
├── fixtures/                 # generated PNGs (offline)
├── tests/
├── requirements.txt
├── pyproject.toml
└── README.md
```

## Offline install

From the project root (`/workspace/poc-py`):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
# or: pip install -r requirements.txt && pip install -e .
```

After install, **runtime analysis needs no network** (OpenCV + NumPy only for decode path; Pillow/qrcode are for fixture generation).

## Fixture generation (offline)

```bash
python -m warehouse_slots.generate_fixtures --out fixtures --write-config config/slots.example.json
```

Creates:

| File | Meaning |
|------|---------|
| `fixtures/F1.png` | capacity 10, payload `SKU-ALPHA` ×10 |
| `fixtures/F2.png` | capacity 6, `SKU-A`/`SKU-B`/`SKU-C` ×2 each |
| `fixtures/F3.png` | capacity 8, 3 filled (`SKU-X/Y/Z`) + 5 empty |
| `fixtures/board.png` | F1 \| F2 \| F3 side-by-side |
| `config/slots.example.json` | ROIs for `board.png` |
| `config/F1.json` … | single-slot configs for individual PNGs |

QRs are large, high-contrast, with quiet zones so OpenCV can read them reliably.

## CLI usage

Analyze the combined board (all three slots):

```bash
python -m warehouse_slots analyze fixtures/board.png --config config/slots.example.json
```

Or one fixture at a time:

```bash
python -m warehouse_slots analyze fixtures/F1.png --config config/F1.json
python -m warehouse_slots analyze fixtures/F2.png --config config/F2.json
python -m warehouse_slots analyze fixtures/F3.png --config config/F3.json
```

JSON on stdout includes per-slot `counts`, `empty`, `unreadable`, `filled`, and per-cell status.

## Tests

```bash
pytest -q
```

Tests regenerate fixtures in setup, then assert F1/F2/F3 counts and empties (and the board).

## Phase 0 acceptance

- [x] `pip install -e .` works in `.venv`
- [x] Fixtures generate offline (no network beyond prior `pip`)
- [x] CLI / tests meet F1, F2, F3 expectations
- [x] No network calls in runtime analysis code (`config`, `qr_detect`, `slot_pipeline`, `cli`)
- [x] Cell-banding: one QR expected per capacity cell

## Slot JSON schema

```json
{
  "slots": [
    {"id": "F1", "roi": [x, y, w, h], "capacity": 10}
  ]
}
```

`roi` is axis-aligned in image pixel coordinates. Cells are horizontal bands stacked top → bottom inside the ROI.
