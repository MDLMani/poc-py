"""CLI integration for analyze / confirm IN-OUT."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from warehouse_slots.cli import main
from warehouse_slots.generate_fixtures import generate_all

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
CONFIG_DIR = ROOT / "config"


@pytest.fixture(scope="module", autouse=True)
def fixtures():
    result = generate_all(FIXTURES)
    refs = FIXTURES / "refs"
    (CONFIG_DIR / "slots.example.json").write_text(
        json.dumps(
            {
                "hash_threshold": 12,
                "fill_direction": "top_to_bottom",
                "reference_images_dir": str(refs.resolve()),
                "enable_hash_fallback": True,
                "slots": result["board_slots"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    for name, slot in result["single_slots"].items():
        cfg = {"slots": [slot]}
        if name in ("F_hash_fallback", "F_unreadable"):
            cfg.update(
                {
                    "hash_threshold": 12,
                    "reference_images_dir": str(refs.resolve()),
                    "enable_hash_fallback": True,
                }
            )
        (CONFIG_DIR / f"{name}.json").write_text(
            json.dumps(cfg, indent=2) + "\n", encoding="utf-8"
        )


def test_cli_analyze_board(capsys, tmp_path):
    code = main(
        [
            "analyze",
            str(FIXTURES / "board.png"),
            "--config",
            str(CONFIG_DIR / "slots.example.json"),
        ]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["slots"]) == 3
    by_id = {s["slot_id"]: s for s in out["slots"]}
    assert by_id["F1"]["counts"] == {"SKU-ALPHA": 10}


def test_cli_confirm_in_out(capsys, tmp_path):
    db = str(tmp_path / "w.db")
    code = main(
        [
            "in",
            str(FIXTURES / "F1.png"),
            "--config",
            str(CONFIG_DIR / "F1.json"),
            "--db",
            db,
            "--note",
            "test-in",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["direction"] == "IN"
    assert payload["sku_lines"][0]["sku_id"] == "SKU-ALPHA"
    assert payload["sku_lines"][0]["qty"] == 10

    code = main(["out", str(FIXTURES / "F1.png"), "--config", str(CONFIG_DIR / "F1.json"), "--db", db])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["direction"] == "OUT"

    code = main(["events", "--db", db])
    assert code == 0
    events = json.loads(capsys.readouterr().out)["events"]
    assert len(events) == 2


def test_cli_sku(capsys, tmp_path):
    db = str(tmp_path / "w.db")
    assert main(["sku", "add", "SKU-T", "Test", "--db", db]) == 0
    capsys.readouterr()  # discard add output
    assert main(["sku", "list", "--db", db, "--seed"]) == 0
    data = json.loads(capsys.readouterr().out)
    ids = {s["sku_id"] for s in data["skus"]}
    assert "SKU-T" in ids
    assert "SKU-ALPHA" in ids
