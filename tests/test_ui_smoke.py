"""Phase 3 headless UI smoke (no display required)."""

from __future__ import annotations

import json
from pathlib import Path

from warehouse_slots.generate_fixtures import generate_all
from warehouse_slots.ui_app import smoke_check

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
CONFIG_DIR = ROOT / "config"


def test_ui_smoke_headless(tmp_path: Path):
    result = generate_all(FIXTURES)
    cfg = CONFIG_DIR / "slots.example.json"
    cfg.write_text(
        json.dumps({"slots": result["board_slots"]}, indent=2) + "\n",
        encoding="utf-8",
    )
    out = smoke_check(
        config_path=cfg,
        image_path=FIXTURES / "board.png",
        db_path=tmp_path / "ui_smoke.db",
    )
    assert out["slots"] == 5
    assert out["event_in"] >= 1
    assert out["event_out"] >= 1
    assert Path(out["snap"]).is_file()
