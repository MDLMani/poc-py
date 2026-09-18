# warehouse_slots — Offline Warehouse MVP (Phases 1–3)

Offline warehouse slot QR scanner with SQLite inventory events and a desktop UI.

**Product constraints (locked)**

- Offline / no internet at runtime (warehouse floor)
- Fixed camera + jig
- One QR per product unit
- Cell-band ROI → capacity cells → decode one QR per cell
- Report: counts by QR payload, `empty = capacity − filled − unreadable`, `UNREADABLE` if occupied but QR fails

**Out of scope (Phase 4+):** WeChat QR, ImageHash matching, PyInstaller packaging.

## Layout

```
poc-py/
├── warehouse_slots/
│   ├── config.py             # load slots JSON
│   ├── qr_detect.py          # OpenCV QRCodeDetector on a crop
│   ├── slot_pipeline.py      # cell-band + FILLED/EMPTY/UNREADABLE
│   ├── store.py              # SQLite SKU catalog + IN/OUT events (Phase 2)
│   ├── local_api.py          # optional 127.0.0.1 HTTP API (Phase 2)
│   ├── ui_app.py             # CustomTkinter desktop UI (Phase 3)
│   ├── cli.py / __main__.py
│   └── generate_fixtures.py  # F1/F2/F3/board + F_unreadable
├── config/                   # slots JSON (board + singles)
├── fixtures/                 # generated PNGs (gitignored; regenerate)
├── data/                     # local DB + snapshots (gitignored)
├── tests/
├── pyproject.toml
└── README.md
```

## Offline install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
# UI also needs system tkinter: sudo apt install python3-tk   # Debian/Ubuntu
```

Runtime analysis / inventory paths use OpenCV + NumPy + stdlib sqlite3 only — **no network imports**.

---

## Phase 1 — Cell-banded slot analysis

### Fixtures (offline)

```bash
python -m warehouse_slots.generate_fixtures --out fixtures --write-config config/slots.example.json
```

| File | Meaning |
|------|---------|
| `fixtures/F1.png` | capacity 10, `SKU-ALPHA` ×10 |
| `fixtures/F2.png` | capacity 6, `SKU-A/B/C` ×2 each |
| `fixtures/F3.png` | capacity 8, 3 filled + 5 empty |
| `fixtures/F_unreadable.png` | 1 filled + 1 UNREADABLE (noise) + 2 empty |
| `fixtures/board.png` | F1 \| F2 \| F3 side-by-side |
| `config/slots.example.json` | ROIs for `board.png` |

### Analyze (JSON on stdout)

```bash
python -m warehouse_slots analyze fixtures/board.png --config config/slots.example.json
python -m warehouse_slots analyze fixtures/F1.png --config config/F1.json
python -m warehouse_slots analyze fixtures/F_unreadable.png --config config/F_unreadable.json
```

### Phase 1 acceptance

- [x] Cell banding solid; EMPTY vs UNREADABLE clear
- [x] Multi-slot board fixture + CLI JSON
- [x] Tests for F1/F2/F3, board, and UNREADABLE
- [x] No network in analysis path

---

## Phase 2 — Offline SQLite catalog + IN/OUT events

Default DB: `data/warehouse.db` (created on first use).

### SKU catalog

```bash
python -m warehouse_slots sku add SKU-ALPHA "Alpha unit" --description "demo"
python -m warehouse_slots sku list --seed          # seed fixture SKUs if missing
```

### Confirm IN / OUT (analyzes image, stores image path + scan JSON)

```bash
python -m warehouse_slots in fixtures/board.png --config config/slots.example.json --note "truck-1"
python -m warehouse_slots out fixtures/F3.png --config config/F3.json
python -m warehouse_slots events --limit 20
```

### Optional local API (loopback only)

```bash
python -m warehouse_slots serve --host 127.0.0.1 --port 8765
# GET  /health  /skus  /events
# POST /skus  /skus/seed  /confirm/in  /confirm/out
```

### Phase 2 acceptance

- [x] Offline SQLite SKU catalog
- [x] Events IN/OUT linked to scan/capture; store image path
- [x] CLI + minimal local API to confirm IN/OUT
- [x] Tests for catalog + events

---

## Phase 3 — Desktop UI (CustomTkinter)

```bash
# Still-image mode (recommended for demos / no camera)
python -m warehouse_slots ui --config config/slots.example.json --image fixtures/board.png

# Live camera (falls back with status if camera missing)
python -m warehouse_slots ui --config config/slots.example.json --camera 0
```

UI features:

- Live camera and/or still image load
- Slot ROI overlay + cell-band guides
- Per-slot results table (filled / empty / unreadable / counts)
- Snapshot + **Confirm IN** / **Confirm OUT** → Phase 2 SQLite store under `data/`
- Works network-unplugged

### Headless smoke (CI / no display)

```bash
pytest tests/test_ui_smoke.py -q
```

### Manual check (with display)

1. Launch UI with `--image fixtures/board.png`
2. Click **Analyze** — table shows F1/F2/F3 counts
3. Click **Confirm IN** then **Confirm OUT**
4. `python -m warehouse_slots events` shows both events with image paths under `data/snapshots/`

### Phase 3 acceptance

- [x] CustomTkinter UI with overlay + results table
- [x] Confirm IN/OUT wired to Phase 2 store
- [x] Offline; README launch docs; smoke test

---

## Tests

```bash
pytest -q
```

Fixtures regenerate in test setup (offline). Expect Phase 1+2+3 smoke green.

## Slot JSON schema

```json
{
  "slots": [
    {"id": "F1", "roi": [x, y, w, h], "capacity": 10}
  ]
}
```

`roi` is axis-aligned in image pixel coordinates. Cells are horizontal bands stacked top → bottom inside the ROI.
