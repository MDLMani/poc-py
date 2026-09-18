# Build warehouse_slots for Windows (run in PowerShell from repo root)
# Requires: Python 3.10+, pip, Visual C++ redistributable on target PCs
#
#   .\packaging\build_windows.ps1
#   .\packaging\build_windows.ps1 -OneFile

param(
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

python -m pip install -U pip
python -m pip install -e ".[dev,packaging]"
python -m warehouse_slots.generate_fixtures --out fixtures --write-config config/slots.example.json

if ($OneFile) {
    python -m PyInstaller --noconfirm packaging/warehouse_slots_onefile.spec
    Write-Host "One-file exe: dist\warehouse_slots.exe"
} else {
    python -m pip install pyinstaller
    python -m PyInstaller --noconfirm packaging/warehouse_slots.spec
    Write-Host "One-folder app: dist\warehouse_slots\warehouse_slots.exe"
    Write-Host "Copy the entire dist\warehouse_slots\ folder to the warehouse PC (offline)."
}
