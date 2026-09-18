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


def default_db_path(base: Optional[Union[str, Path]] = None) -> Path:
    root = Path(base) if base else Path.cwd()
    return root / "data" / DEFAULT_DB_NAME
