"""快速翻译历史（SQLite 本地持久化）。"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class HistoryItem:
    id: int
    created_at: str
    direction: str
    source: str
    target: str
    backend: str


class TranslateHistoryStore:
    """线程安全的翻译历史存储。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS quick_translate_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        source TEXT NOT NULL,
                        target TEXT NOT NULL,
                        backend TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_history_created "
                    "ON quick_translate_history(created_at DESC)"
                )
                conn.commit()

    def add(
        self,
        *,
        direction: str,
        source: str,
        target: str,
        backend: str = "",
    ) -> HistoryItem:
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO quick_translate_history
                        (created_at, direction, source, target, backend)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (created_at, direction, source, target, backend or ""),
                )
                conn.commit()
                row_id = int(cur.lastrowid)
        return HistoryItem(
            id=row_id,
            created_at=created_at,
            direction=direction,
            source=source,
            target=target,
            backend=backend or "",
        )

    def list_recent(self, limit: int = 100) -> list[HistoryItem]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, created_at, direction, source, target, backend
                    FROM quick_translate_history
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [
            HistoryItem(
                id=int(r["id"]),
                created_at=str(r["created_at"]),
                direction=str(r["direction"]),
                source=str(r["source"]),
                target=str(r["target"]),
                backend=str(r["backend"] or ""),
            )
            for r in rows
        ]

    def clear(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM quick_translate_history")
                conn.commit()
