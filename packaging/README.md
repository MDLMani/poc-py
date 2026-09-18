# Packaging (Phase 4)

## Windows one-folder (recommended)

On a networked Windows build machine:

```powershell
.\packaging\build_windows.ps1
# → dist\warehouse_slots\   (copy whole folder to warehouse PC)
```

One-file (single `.exe`, slower cold start):

```powershell
.\packaging\build_windows.ps1 -OneFile
```

Run on the floor PC (no internet):

```text
dist\warehouse_slots\warehouse_slots.exe analyze fixtures\board.png --config config\slots.example.json
dist\warehouse_slots\warehouse_slots.exe ui --config config\slots.example.json --image fixtures\board.png
```

## Air-gapped / no freeze path

If PyInstaller is hard on the target:

1. On a connected PC with the **same OS + Python minor version**:

   ```bash
   mkdir offline-wheels
   pip download -d offline-wheels -r requirements.txt
   pip download -d offline-wheels ImageHash pyinstaller
   # also bundle the repo source tree
   ```

2. Copy `offline-wheels/` + repo to the warehouse PC (USB).

3. Offline install:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate          # Windows
   pip install --no-index --find-links=offline-wheels -r requirements.txt
   pip install --no-index --find-links=offline-wheels -e .
   python -m warehouse_slots.generate_fixtures --out fixtures --write-config config/slots.example.json
   ```

No network calls at runtime after install.

## Note on ImageHash transitive deps

`ImageHash` pulls `scipy` and `PyWavelets`. Include them when building the offline wheel folder (`pip download` resolves them automatically from `requirements.txt`).
