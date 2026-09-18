"""Offline SQLite SKU catalog + inbound/outbound events (Phase 2).

No network I/O. Default DB path: data/warehouse.db under the CWD or
an explicit path passed by the caller.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Union

DEFAULT_DB_NAME = "warehouse.db"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class Sku:
    sku_id: str
    name: str
    description: str = ""
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sku_id": self.sku_id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class Event:
    id: int
    direction: str  # IN or OUT
    image_path: str
    scan_json: str
    note: str
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        try:
            scan = json.loads(self.scan_json) if self.scan_json else {}
        except json.JSONDecodeError:
            scan = {"raw": self.scan_json}
        return {
            "id": self.id,
            "direction": self.direction,
            "image_path": self.image_path,
            "scan": scan,
            "note": self.note,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class LibraryImage:
    id: int
    image_path: str
    qr_payload: str
    name: str
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "image_path": self.image_path,
            "qr_payload": self.qr_payload,
            "name": self.name,
            "created_at": self.created_at,
        }


class WarehouseStore:
    """Local SQLite store for SKUs and IN/OUT events."""

    def __init__(self, db_path: Union[str, Path]):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sku_catalog (
                    sku_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    direction TEXT NOT NULL CHECK (direction IN ('IN', 'OUT')),
                    image_path TEXT NOT NULL,
                    scan_json TEXT NOT NULL DEFAULT '{}',
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS event_skus (
                    event_id INTEGER NOT NULL,
                    sku_id TEXT NOT NULL,
                    qty INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (event_id, sku_id),
                    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS image_library (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    image_path TEXT NOT NULL,
                    qr_payload TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                """
            )

    # --- SKU catalog -----------------------------------------------------

    def upsert_sku(
        self, sku_id: str, name: str, description: str = ""
    ) -> Sku:
        sku_id = sku_id.strip()
        name = name.strip()
        if not sku_id or not name:
            raise ValueError("sku_id and name are required")
        now = _utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sku_catalog (sku_id, name, description, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(sku_id) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description
                """,
                (sku_id, name, description or "", now),
            )
            row = conn.execute(
                "SELECT * FROM sku_catalog WHERE sku_id = ?", (sku_id,)
            ).fetchone()
        return Sku(
            sku_id=row["sku_id"],
            name=row["name"],
            description=row["description"],
            created_at=row["created_at"],
        )

    def get_sku(self, sku_id: str) -> Optional[Sku]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sku_catalog WHERE sku_id = ?", (sku_id,)
            ).fetchone()
        if row is None:
            return None
        return Sku(
            sku_id=row["sku_id"],
            name=row["name"],
            description=row["description"],
            created_at=row["created_at"],
        )

    def list_skus(self) -> List[Sku]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sku_catalog ORDER BY sku_id"
            ).fetchall()
        return [
            Sku(
                sku_id=r["sku_id"],
                name=r["name"],
                description=r["description"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def seed_common_skus(self) -> int:
        """Insert demo SKUs used by fixtures if missing. Returns new count."""
        demo = [
            ("SKU-ALPHA", "Alpha unit", "F1 fixture payload"),
            ("SKU-A", "SKU A", "F2 fixture"),
            ("SKU-B", "SKU B", "F2 fixture"),
            ("SKU-C", "SKU C", "F2 fixture"),
            ("SKU-X", "SKU X", "F3 fixture"),
            ("SKU-Y", "SKU Y", "F3 fixture"),
            ("SKU-Z", "SKU Z", "F3 fixture"),
        ]
        added = 0
        for sku_id, name, desc in demo:
            if self.get_sku(sku_id) is None:
                self.upsert_sku(sku_id, name, desc)
                added += 1
        return added

    # --- Events ----------------------------------------------------------

    def record_event(
        self,
        direction: str,
        image_path: Union[str, Path],
        scan: Optional[Dict[str, Any]] = None,
        note: str = "",
        sku_qtys: Optional[Sequence[tuple[str, int]]] = None,
    ) -> Event:
        direction = direction.upper().strip()
        if direction not in ("IN", "OUT"):
            raise ValueError("direction must be IN or OUT")
        image_path_s = str(image_path)
        scan = scan or {}
        scan_json = json.dumps(scan, sort_keys=True)
        now = _utc_now_iso()

        # Derive sku_qtys from scan counts if not provided
        if sku_qtys is None:
            sku_qtys = []
            for slot in scan.get("slots", []):
                for payload, qty in (slot.get("counts") or {}).items():
                    sku_qtys.append((str(payload), int(qty)))

        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO events (direction, image_path, scan_json, note, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (direction, image_path_s, scan_json, note or "", now),
            )
            event_id = int(cur.lastrowid)
            for sku_id, qty in sku_qtys:
                if qty <= 0:
                    continue
                # Ensure SKU exists (auto-register unknown payloads)
                existing = conn.execute(
                    "SELECT 1 FROM sku_catalog WHERE sku_id = ?", (sku_id,)
                ).fetchone()
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO sku_catalog (sku_id, name, description, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (sku_id, sku_id, "auto-registered from scan", now),
                    )
                conn.execute(
                    """
                    INSERT INTO event_skus (event_id, sku_id, qty)
                    VALUES (?, ?, ?)
                    ON CONFLICT(event_id, sku_id) DO UPDATE SET
                        qty = qty + excluded.qty
                    """,
                    (event_id, sku_id, int(qty)),
                )
            row = conn.execute(
                "SELECT * FROM events WHERE id = ?", (event_id,)
            ).fetchone()

        return Event(
            id=row["id"],
            direction=row["direction"],
            image_path=row["image_path"],
            scan_json=row["scan_json"],
            note=row["note"],
            created_at=row["created_at"],
        )

    def list_events(self, limit: int = 100) -> List[Event]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [
            Event(
                id=r["id"],
                direction=r["direction"],
                image_path=r["image_path"],
                scan_json=r["scan_json"],
                note=r["note"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def get_event(self, event_id: int) -> Optional[Event]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM events WHERE id = ?", (int(event_id),)
            ).fetchone()
        if row is None:
            return None
        return Event(
            id=row["id"],
            direction=row["direction"],
            image_path=row["image_path"],
            scan_json=row["scan_json"],
            note=row["note"],
            created_at=row["created_at"],
        )

    def event_sku_lines(self, event_id: int) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT sku_id, qty FROM event_skus
                WHERE event_id = ? ORDER BY sku_id
                """,
                (int(event_id),),
            ).fetchall()
        return [{"sku_id": r["sku_id"], "qty": r["qty"]} for r in rows]


    # --- Image library ---------------------------------------------------

    def library_dir(self) -> Path:
        d = self.db_path.parent / "library"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def refs_dir(self) -> Path:
        """Directory of ImageHash refs derived from library (stem = QR/SKU)."""
        d = self.db_path.parent / "library_refs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _safe_stem(text: str) -> str:
        keep = []
        for ch in (text or "").strip():
            if ch.isalnum() or ch in ("-", "_"):
                keep.append(ch)
            else:
                keep.append("_")
        return "".join(keep) or "image"

    def add_library_image(
        self,
        image_path: Union[str, Path],
        qr_payload: str,
        name: str = "",
        copy: bool = True,
    ) -> LibraryImage:
        """Register an image + QR payload (SKU id). Copies file into data/library/."""
        import shutil

        src = Path(image_path)
        if not src.is_file():
            raise FileNotFoundError(f"image not found: {src}")
        qr_payload = (qr_payload or "").strip()
        if not qr_payload:
            raise ValueError("qr_payload is required")
        name = (name or "").strip() or src.stem
        now = _utc_now_iso()

        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO image_library (image_path, qr_payload, name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                ("", qr_payload, name, now),
            )
            entry_id = int(cur.lastrowid)

        if copy:
            dest = self.library_dir() / f"{entry_id}_{self._safe_stem(qr_payload)}{src.suffix.lower() or '.png'}"
            shutil.copy2(src, dest)
            stored = str(dest.resolve())
        else:
            stored = str(src.resolve())

        with self._connect() as conn:
            conn.execute(
                "UPDATE image_library SET image_path = ? WHERE id = ?",
                (stored, entry_id),
            )
            row = conn.execute(
                "SELECT * FROM image_library WHERE id = ?", (entry_id,)
            ).fetchone()

        entry = LibraryImage(
            id=row["id"],
            image_path=row["image_path"],
            qr_payload=row["qr_payload"],
            name=row["name"],
            created_at=row["created_at"],
        )
        self.sync_library_refs()
        return entry

    def list_library(self) -> List[LibraryImage]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM image_library ORDER BY id"
            ).fetchall()
        return [
            LibraryImage(
                id=r["id"],
                image_path=r["image_path"],
                qr_payload=r["qr_payload"],
                name=r["name"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def get_library(self, entry_id: int) -> Optional[LibraryImage]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM image_library WHERE id = ?", (int(entry_id),)
            ).fetchone()
        if row is None:
            return None
        return LibraryImage(
            id=row["id"],
            image_path=row["image_path"],
            qr_payload=row["qr_payload"],
            name=row["name"],
            created_at=row["created_at"],
        )

    def remove_library(self, entry_id: int, delete_file: bool = True) -> bool:
        entry = self.get_library(entry_id)
        if entry is None:
            return False
        with self._connect() as conn:
            conn.execute("DELETE FROM image_library WHERE id = ?", (int(entry_id),))
        if delete_file:
            p = Path(entry.image_path)
            # Only delete files under our library dir
            try:
                if p.is_file() and self.library_dir() in p.resolve().parents:
                    p.unlink()
            except OSError:
                pass
        self.sync_library_refs()
        return True

    def sync_library_refs(self) -> Path:
        """Copy/link library images into library_refs/ as {qr_payload}.ext for ImageHash."""
        import shutil

        refs = self.refs_dir()
        # Clear stale refs that we manage (files only)
        for old in list(refs.iterdir()) if refs.is_dir() else []:
            if old.is_file():
                try:
                    old.unlink()
                except OSError:
                    pass
        for entry in self.list_library():
            src = Path(entry.image_path)
            if not src.is_file():
                continue
            stem = self._safe_stem(entry.qr_payload)
            dest = refs / f"{stem}{src.suffix.lower() or '.png'}"
            # Prefer last-registered image for a given QR
            try:
                shutil.copy2(src, dest)
            except OSError:
                continue
        return refs

    # --- Stock / on-hand from IN/OUT -------------------------------------

    def on_hand(self, sku_id: Optional[str] = None) -> Dict[str, int]:
        """Net stock per SKU from event_skus (IN adds, OUT subtracts)."""
        with self._connect() as conn:
            if sku_id is None:
                rows = conn.execute(
                    """
                    SELECT es.sku_id AS sku_id,
                           COALESCE(SUM(
                               CASE e.direction
                                   WHEN 'IN' THEN es.qty
                                   WHEN 'OUT' THEN -es.qty
                                   ELSE 0
                               END
                           ), 0) AS qty
                    FROM event_skus es
                    JOIN events e ON e.id = es.event_id
                    GROUP BY es.sku_id
                    ORDER BY es.sku_id
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT es.sku_id AS sku_id,
                           COALESCE(SUM(
                               CASE e.direction
                                   WHEN 'IN' THEN es.qty
                                   WHEN 'OUT' THEN -es.qty
                                   ELSE 0
                               END
                           ), 0) AS qty
                    FROM event_skus es
                    JOIN events e ON e.id = es.event_id
                    WHERE es.sku_id = ?
                    GROUP BY es.sku_id
                    """,
                    (sku_id,),
                ).fetchall()
        return {r["sku_id"]: int(r["qty"]) for r in rows}

    def backend_available(self, sku_id: str) -> int:
        return int(self.on_hand(sku_id).get(sku_id, 0))


def default_db_path(base: Optional[Union[str, Path]] = None) -> Path:
    root = Path(base) if base else Path.cwd()
    return root / "data" / DEFAULT_DB_NAME
