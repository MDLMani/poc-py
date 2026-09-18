# warehouse_slots — POC v1.0 ✅ completed

**Status (2026-09-18):** POC v1.0 is complete on `main` (`v1.0.0`).

Includes Phases 0–4 (offline QR slot scan, SQLite IN/OUT, CustomTkinter UI, ImageHash fallback, packaging) plus **image library** and **backend availability check**. Live camera is optional; default workflow is file/library based.

---

Offline warehouse slot QR scanner with SQLite inventory events, desktop UI,
tougher QR decode, ImageHash visual fallback, image library, and backend
availability checks (image/QR vs on-hand stock).

**Product constraints (locked)**

- Offline / no internet at runtime (warehouse floor)
- Fixed camera + jig
- One QR per product unit
- Cell-band ROI → capacity cells → decode one QR per cell
- Report: counts by QR payload, `empty = capacity − filled − unreadable`, `UNREADABLE` if occupied but QR fails
- ImageHash fallback **only** when a cell is UNREADABLE (never replaces a successful QR)

**Out of scope:** Cloud sync, multi-user auth, mobile, WeChat QR.

## Layout

```
poc-py/
├── warehouse_slots/
│   ├── config.py             # slots JSON + Phase 4 knobs
│   ├── qr_detect.py          # hardened OpenCV QRCodeDetector
│   ├── image_hash_match.py   # ImageHash SKU reference fallback
│   ├── slot_pipeline.py      # cell-band + FILLED/EMPTY/UNREADABLE
│   ├── store.py              # SQLite SKU catalog + IN/OUT + image library
│   ├── availability.py       # Check image/QR counts vs on-hand stock
│   ├── local_api.py          # optional 127.0.0.1 HTTP API
│   ├── ui_app.py             # CustomTkinter desktop UI
│   ├── cli.py / __main__.py
│   └── generate_fixtures.py  # F1/F2/F3/board + unreadable + hash fixtures
├── config/                   # slots JSON (board + singles)
├── fixtures/                 # generated PNGs + refs/ (regenerate offline)
├── packaging/                # PyInstaller specs + Windows build script
├── data/                     # local DB + snapshots (gitignored)
├── tests/
├── pyproject.toml
└── README.md
```

## Offline install

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -U pip
pip install -e ".[dev]"
# UI also needs system tkinter: sudo apt install python3-tk   # Debian/Ubuntu
python -m warehouse_slots.generate_fixtures --out fixtures --write-config config/slots.example.json
```

Runtime analysis / inventory paths use OpenCV + NumPy + Pillow + ImageHash + stdlib sqlite3 — **no network imports**.

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
| `fixtures/F_hash_fallback.png` | visual label → ImageHash → `SKU-VISUAL` |
| `fixtures/refs/SKU-VISUAL.png` | local SKU reference for hash fallback |
| `fixtures/board.png` | F1 \| F2 \| F3 side-by-side |
| `config/slots.example.json` | ROIs + Phase 4 knobs for `board.png` |

### Analyze (JSON on stdout; alerts on stderr)

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

### Phase 3 acceptance

- [x] CustomTkinter UI with overlay + results table
- [x] Confirm IN/OUT wired to Phase 2 store
- [x] Offline; README launch docs; smoke test

---

## Phase 4 — Field hardening

### 1. Tougher QR decode

`qr_detect.py` now applies offline OpenCV refinements before giving up:

- Min-size upscale + extra 2× pass for small/distant codes
- CLAHE + sharpen (glare / washed lighting)
- Otsu + adaptive thresholds + light morphology
- Mild rotation sweep (±8°, ±15°) for jig angle
- Invert pass + `detectAndDecodeMulti` fallback

No second detector unless it is fully offline and pip-installable (kept OpenCV-only).

### 2. ImageHash visual fallback (UNREADABLE only)

When a cell is **occupied** and QR decode fails:

1. Status would be `UNREADABLE`
2. If `enable_hash_fallback` and local refs exist, match crop via **perceptual hash** (`imagehash.phash`)
3. On hit under `hash_threshold`: mark `FILLED` with `match_source=imagehash`
4. Successful QR is **never** replaced by hash

Reference images live in a local folder (filename stem = SKU id):

```text
fixtures/refs/SKU-VISUAL.png
fixtures/refs/SKU-ALPHA.png
```

### Run hash fallback demo

```bash
python -m warehouse_slots.generate_fixtures --out fixtures --write-config config/slots.example.json

python -m warehouse_slots analyze fixtures/F_hash_fallback.png \
  --config config/F_hash_fallback.json

# Explicit knobs
python -m warehouse_slots analyze fixtures/F_hash_fallback.png \
  --config config/F_hash_fallback.json \
  --refs fixtures/refs \
  --hash-threshold 12 \
  --fill-direction top_to_bottom
```

Expect stderr alerts like `NOTICE HASH_FALLBACK: … → SKU-VISUAL` and JSON `match_source: "imagehash"`.

Noise blotch fixture stays UNREADABLE (does not false-match refs):

```bash
python -m warehouse_slots analyze fixtures/F_unreadable.png --config config/F_unreadable.json
# stderr: ALERT UNREADABLE: slot=F_unreadable cell=1 …
```

### Config knobs (slots JSON top-level)

```json
{
  "hash_threshold": 12,
  "fill_direction": "top_to_bottom",
  "reference_images_dir": "../fixtures/refs",
  "enable_hash_fallback": true,
  "slots": [ { "id": "F1", "roi": [x, y, w, h], "capacity": 10 } ]
}
```

| Knob | Meaning |
|------|---------|
| `hash_threshold` | Max Hamming distance for phash match (default 12) |
| `fill_direction` | `top_to_bottom` or `bottom_to_top` (cell index 0) |
| `reference_images_dir` | Local folder of SKU reference images |
| `enable_hash_fallback` | Master switch for ImageHash path |

CLI overrides: `--hash-threshold`, `--fill-direction`, `--refs`, `--no-hash-fallback`.

### 3. Packaging (PyInstaller + air-gap wheels)

See [`packaging/README.md`](packaging/README.md).

Windows one-folder (preferred for floor PCs):

```powershell
.\packaging\build_windows.ps1
# → dist\warehouse_slots\  (copy entire folder offline)
```

One-file:

```powershell
.\packaging\build_windows.ps1 -OneFile
```

Air-gapped pip path when freeze is hard:

```bash
# on a connected twin machine (same OS/Python):
mkdir offline-wheels
pip download -d offline-wheels -r requirements.txt
# USB → warehouse PC
python -m venv .venv && .venv\Scripts\activate
pip install --no-index --find-links=offline-wheels -r requirements.txt
pip install --no-index --find-links=offline-wheels -e .
```

### 4. Ops polish

- CLI prints `ALERT UNREADABLE` / `NOTICE HASH_FALLBACK` blocks on **stderr**
- Report JSON includes `alerts`, `hash_filled`, per-cell `match_source`
- UI shows a dedicated alert panel + red `UNREADABLE` overlay labels

### Phase 4 acceptance checklist

- [x] Hardened QR decode (glare/angle/small) keeps F1/F2/F3 green
- [x] ImageHash fallback only on occupied+QR-fail cells
- [x] QR success never overwritten by hash
- [x] Config knobs: `hash_threshold`, `fill_direction`, refs dir
- [x] Clearer UNREADABLE alerts in CLI + UI
- [x] PyInstaller one-folder/one-file scripts + air-gap wheel docs
- [x] Fixtures + tests for unreadable→hash fallback
- [x] Existing tests stay green; no network at runtime

---

---

## Image library + availability check

Fully offline. Not live-camera–first: still images and the library are primary.

### Library management

Register a local image file with its QR payload (SKU id) and optional label.
Files are copied under `data/library/`; ImageHash refs are synced to
`data/library_refs/{QR}.png` so hash fallback can reuse library images.

```bash
python -m warehouse_slots library add --image path/to/ref.png --qr SKU-ALPHA --name "Alpha ref"
python -m warehouse_slots library list
python -m warehouse_slots library remove 1
```

### Check availability

Analyze a board/still (with slots config) and/or look up QR / library entries,
then cross-check SQLite SKU catalog + net stock from IN/OUT events.

Per SKU fields: `in_image_count`, `backend_available` (on_hand), `status`:

| Status | Meaning |
|--------|---------|
| `OK` | SKU known and on_hand covers image count |
| `LOW` | on_hand > 0 but less than `in_image_count` |
| `MISSING_IN_BACKEND` | SKU in catalog but on_hand ≤ 0 |
| `UNKNOWN_SKU` | QR/SKU not in catalog |

```bash
# After some IN/OUT events exist in data/warehouse.db:
python -m warehouse_slots check --image fixtures/F1.png --config config/F1.json
python -m warehouse_slots check --qr SKU-ALPHA --qr SKU-UNKNOWN
python -m warehouse_slots check --library-id 1 --db data/warehouse.db
```

### UI

```bash
python -m warehouse_slots ui --config config/slots.example.json --image fixtures/board.png
```

Primary control: **Check availability** (pick still or library image → results table).
Library panel: add image + QR, gallery list, open library image. Live camera is
optional and de-emphasized.


## Tests

```bash
pytest -q
```

Fixtures regenerate in test setup (offline).

## Slot JSON schema

```json
{
  "hash_threshold": 12,
  "fill_direction": "top_to_bottom",
  "reference_images_dir": "../fixtures/refs",
  "enable_hash_fallback": true,
  "slots": [
    {"id": "F1", "roi": [x, y, w, h], "capacity": 10}
  ]
}
```

`roi` is axis-aligned in image pixel coordinates. Cells are horizontal bands inside the ROI; index 0 is top or bottom per `fill_direction`.
