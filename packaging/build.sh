#!/usr/bin/env bash
# Linux/macOS helper — for Windows floor PCs prefer build_windows.ps1 on a Win builder.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
python -m pip install -U pip
python -m pip install -e ".[dev]"
python -m pip install pyinstaller
python -m warehouse_slots.generate_fixtures --out fixtures --write-config config/slots.example.json
MODE="${1:-folder}"
if [[ "$MODE" == "onefile" ]]; then
  python -m PyInstaller --noconfirm packaging/warehouse_slots_onefile.spec
  echo "Built: dist/warehouse_slots (one-file binary name depends on platform)"
else
  python -m PyInstaller --noconfirm packaging/warehouse_slots.spec
  echo "Built one-folder: dist/warehouse_slots/"
fi
