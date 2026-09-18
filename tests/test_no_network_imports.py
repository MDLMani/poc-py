"""Ensure analysis / inventory runtime modules do not import network stacks."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "warehouse_slots"

# Modules on the offline runtime path (analysis + inventory store + Phase 4 hash)
OFFLINE_MODULES = [
    "config.py",
    "qr_detect.py",
    "slot_pipeline.py",
    "image_hash_match.py",
    "store.py",
    "cli.py",
]

FORBIDDEN = {
    "requests",
    "urllib.request",
    "urllib3",
    "httpx",
    "aiohttp",
    "socket",  # store/cli must stay offline; local_api is separate
}


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
                names.add(node.module)
    return names


def test_offline_modules_have_no_network_imports():
    for name in OFFLINE_MODULES:
        path = ROOT / name
        imported = _imported_names(path)
        bad = imported & FORBIDDEN
        assert not bad, f"{name} imports forbidden network modules: {bad}"
