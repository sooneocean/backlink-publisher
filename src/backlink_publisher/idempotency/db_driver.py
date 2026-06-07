from __future__ import annotations
import sqlite3
from typing import Any, Optional

class IdempotencyDBDriver:
    """Handles low-level SQLite interactions for idempotency store."""
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def execute_query(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._get_connection() as conn:
            return conn.execute(query, params)
    
    def fetch_record(self, query: str, params: tuple = ()) -> Optional[dict]:
        conn = self._get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(query, params)
        row = cursor.fetchone()
        return dict(row) if row else None
