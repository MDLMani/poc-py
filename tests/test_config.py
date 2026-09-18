"""Config loader smoke tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from warehouse_slots.config import load_slots


def test_load_slots_object(tmp_path: Path):
    p = tmp_path / "slots.json"
    p.write_text(
        json.dumps(
            {
                "slots": [
                    {"id": "A", "roi": [0, 0, 10, 20], "capacity": 2},
                ]
            }
        ),
        encoding="utf-8",
    )
    slots = load_slots(p)
    assert len(slots) == 1
    assert slots[0].id == "A"
    assert slots[0].roi == (0, 0, 10, 20)
    assert slots[0].capacity == 2


def test_load_slots_rejects_bad_capacity(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text(
        json.dumps({"slots": [{"id": "A", "roi": [0, 0, 10, 20], "capacity": 0}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_slots(p)
