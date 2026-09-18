"""Image library + backend availability check."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from warehouse_slots.availability import check_availability, classify_status
from warehouse_slots.cli import main
from warehouse_slots.generate_fixtures import generate_all
from warehouse_slots.store import WarehouseStore

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
        (CONFIG_DIR / f"{name}.json").write_text(
            json.dumps({"slots": [slot]}, indent=2) + "\n", encoding="utf-8"
        )


@pytest.fixture
def store(tmp_path: Path) -> WarehouseStore:
    return WarehouseStore(tmp_path / "test.db")


def _tiny_png(path: Path, color=(200, 40, 40)) -> Path:
    Image.new("RGB", (32, 32), color).save(path)
    return path


def test_classify_status_matrix():
    assert classify_status(in_catalog=False, in_image_count=1, backend_available=0) == "UNKNOWN_SKU"
    assert classify_status(in_catalog=True, in_image_count=2, backend_available=0) == "MISSING_IN_BACKEND"
    assert classify_status(in_catalog=True, in_image_count=5, backend_available=2) == "LOW"
    assert classify_status(in_catalog=True, in_image_count=2, backend_available=10) == "OK"
    assert classify_status(in_catalog=True, in_image_count=0, backend_available=3) == "OK"


def test_library_add_list_remove(store: WarehouseStore, tmp_path: Path):
    img = _tiny_png(tmp_path / "alpha.png")
    entry = store.add_library_image(img, qr_payload="SKU-ALPHA", name="Alpha ref")
    assert entry.id >= 1
    assert entry.qr_payload == "SKU-ALPHA"
    assert Path(entry.image_path).is_file()
    listed = store.list_library()
    assert len(listed) == 1
    assert listed[0].name == "Alpha ref"
    # ImageHash refs synced
    refs = store.refs_dir()
    assert any(p.stem == "SKU-ALPHA" for p in refs.iterdir())
    assert store.remove_library(entry.id) is True
    assert store.list_library() == []
    assert store.remove_library(999) is False


def test_on_hand_from_events(store: WarehouseStore, tmp_path: Path):
    store.upsert_sku("SKU-ALPHA", "Alpha")
    img = tmp_path / "cap.png"
    img.write_bytes(b"x")
    store.record_event("IN", img, sku_qtys=[("SKU-ALPHA", 10)])
    store.record_event("OUT", img, sku_qtys=[("SKU-ALPHA", 3)])
    assert store.backend_available("SKU-ALPHA") == 7
    assert store.on_hand()["SKU-ALPHA"] == 7


def test_check_availability_qr(store: WarehouseStore, tmp_path: Path):
    store.upsert_sku("SKU-ALPHA", "Alpha")
    img = tmp_path / "cap.png"
    img.write_bytes(b"x")
    store.record_event("IN", img, sku_qtys=[("SKU-ALPHA", 5)])
    report = check_availability(store, qr_payloads=["SKU-ALPHA", "SKU-UNKNOWN"])
    by = {r["sku_id"]: r for r in report["skus"]}
    assert by["SKU-ALPHA"]["backend_available"] == 5
    assert by["SKU-ALPHA"]["status"] == "OK"
    assert by["SKU-UNKNOWN"]["status"] == "UNKNOWN_SKU"


def test_check_availability_image_vs_stock(store: WarehouseStore, tmp_path: Path):
    store.seed_common_skus()
    img = tmp_path / "cap.png"
    img.write_bytes(b"x")
    # IN less than F1 board count (10 ALPHA) → LOW for ALPHA after analyzing F1
    store.record_event("IN", img, sku_qtys=[("SKU-ALPHA", 3)])
    report = check_availability(
        store,
        image_path=FIXTURES / "F1.png",
        config_path=CONFIG_DIR / "F1.json",
    )
    by = {r["sku_id"]: r for r in report["skus"]}
    assert by["SKU-ALPHA"]["in_image_count"] == 10
    assert by["SKU-ALPHA"]["backend_available"] == 3
    assert by["SKU-ALPHA"]["status"] == "LOW"


def test_check_missing_in_backend(store: WarehouseStore):
    store.upsert_sku("SKU-ALPHA", "Alpha")
    # cataloged but never stocked
    report = check_availability(store, qr_payloads=["SKU-ALPHA"])
    assert report["skus"][0]["status"] == "MISSING_IN_BACKEND"


def test_cli_library_and_check(capsys, tmp_path: Path):
    db = str(tmp_path / "w.db")
    img = _tiny_png(tmp_path / "lib.png", (10, 120, 200))
    assert (
        main(
            [
                "library",
                "add",
                "--image",
                str(img),
                "--qr",
                "SKU-LIB",
                "--name",
                "Lib item",
                "--db",
                db,
            ]
        )
        == 0
    )
    added = json.loads(capsys.readouterr().out)
    assert added["qr_payload"] == "SKU-LIB"

    assert main(["library", "list", "--db", db]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed["library"]) == 1

    # Seed SKU + stock then check via --qr and --library-id
    assert main(["sku", "add", "SKU-LIB", "Lib", "--db", db]) == 0
    capsys.readouterr()
    # Record stock via store directly
    store = WarehouseStore(db)
    store.record_event("IN", tmp_path / "x.png", sku_qtys=[("SKU-LIB", 2)])
    (tmp_path / "x.png").write_bytes(b"x")

    assert main(["check", "--qr", "SKU-LIB", "--db", db]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["skus"][0]["status"] == "OK"

    assert main(["check", "--library-id", str(added["id"]), "--db", db]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["skus"][0]["sku_id"] == "SKU-LIB"

    assert main(["library", "remove", str(added["id"]), "--db", db]) == 0
    capsys.readouterr()
    assert main(["library", "list", "--db", db]) == 0
    assert json.loads(capsys.readouterr().out)["library"] == []


def test_cli_check_image(capsys, tmp_path: Path):
    db = str(tmp_path / "w.db")
    store = WarehouseStore(db)
    store.seed_common_skus()
    # Plenty of stock
    store.record_event(
        "IN",
        tmp_path / "c.png",
        sku_qtys=[("SKU-ALPHA", 100)],
    )
    (tmp_path / "c.png").write_bytes(b"x")
    code = main(
        [
            "check",
            "--image",
            str(FIXTURES / "F1.png"),
            "--config",
            str(CONFIG_DIR / "F1.json"),
            "--db",
            db,
        ]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    by = {r["sku_id"]: r for r in out["skus"]}
    assert by["SKU-ALPHA"]["status"] == "OK"
    assert by["SKU-ALPHA"]["in_image_count"] == 10
