# PyInstaller spec — offline warehouse_slots (Phase 4)
# Build on Windows (recommended) or cross-check on Linux:
#   pyinstaller packaging/warehouse_slots.spec
# Output: dist/warehouse_slots/ (one-folder) containing warehouse_slots.exe / binary

block_cipher = None

a = Analysis(
    ['../warehouse_slots/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('../config', 'config'),
        ('../fixtures/refs', 'fixtures/refs'),
    ],
    hiddenimports=[
        'warehouse_slots',
        'warehouse_slots.cli',
        'warehouse_slots.config',
        'warehouse_slots.qr_detect',
        'warehouse_slots.slot_pipeline',
        'warehouse_slots.image_hash_match',
        'warehouse_slots.store',
        'warehouse_slots.ui_app',
        'warehouse_slots.local_api',
        'warehouse_slots.generate_fixtures',
        'customtkinter',
        'imagehash',
        'PIL',
        'cv2',
        'numpy',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['requests', 'urllib3', 'httpx', 'aiohttp'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='warehouse_slots',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='warehouse_slots',
)
