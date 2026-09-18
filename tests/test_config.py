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


def test_load_config_options(tmp_path: Path):
    from warehouse_slots.config import load_config

    refs = tmp_path / "refs"
    refs.mkdir()
    p = tmp_path / "slots.json"
    p.write_text(
        json.dumps(
            {
                "hash_threshold": 8,
                "fill_direction": "bottom_to_top",
                "reference_images_dir": "refs",
                "enable_hash_fallback": True,
                "slots": [{"id": "A", "roi": [0, 0, 10, 20], "capacity": 2}],
            }
        ),
        encoding="utf-8",
    )
    slots, options = load_config(p)
    assert len(slots) == 1
    assert options.hash_threshold == 8
    assert options.fill_direction == "bottom_to_top"
    assert options.reference_images_dir == refs.resolve()
    assert options.enable_hash_fallback is True


def test_load_config_rejects_bad_fill_direction(tmp_path: Path):
    from warehouse_slots.config import load_config

    p = tmp_path / "bad.json"
    p.write_text(
        json.dumps(
            {
                "fill_direction": "sideways",
                "slots": [{"id": "A", "roi": [0, 0, 10, 20], "capacity": 2}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(p)
