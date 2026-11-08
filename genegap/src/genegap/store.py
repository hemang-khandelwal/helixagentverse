"""Task 1.2: one SQLite file holding the HTTP cache, every Fact, and the agent step log."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional, Union

from .config import DB_PATH
from .schemas import Fact

SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache (
    key TEXT PRIMARY KEY, url TEXT, status INTEGER, body TEXT, fetched_at REAL);
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY, source TEXT, record_id TEXT, json TEXT);
CREATE INDEX IF NOT EXISTS facts_rec ON facts(source, record_id);
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, run_id TEXT, agent TEXT, kind TEXT, payload TEXT);
"""


class Store:
    def __init__(self, path: Union[str, Path] = DB_PATH):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---- HTTP cache ----
    def cache_get(self, key: str, max_age_s: Optional[float] = None) -> Optional[sqlite3.Row]:
        row = self.db.execute("SELECT * FROM http_cache WHERE key=?", (key,)).fetchone()
        if row and max_age_s is not None and time.time() - row["fetched_at"] > max_age_s:
            return None
        return row

    def cache_put(self, key: str, url: str, status: int, body: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO http_cache VALUES (?,?,?,?,?)", (key, url, status, body, time.time()))
        self.db.commit()

    # ---- Facts (provenance) ----
    def put_fact(self, fact: Fact) -> str:
        self.db.execute("INSERT OR REPLACE INTO facts VALUES (?,?,?,?)",
                        (fact.id, fact.source, fact.record_id, fact.model_dump_json()))
        self.db.commit()
        return fact.id

    def get_fact(self, fact_id: str) -> Optional[Fact]:
        row = self.db.execute("SELECT json FROM facts WHERE id=?", (fact_id,)).fetchone()
        return Fact.model_validate_json(row["json"]) if row else None

    def facts_for_record(self, source: str, record_id: str) -> list[Fact]:
        rows = self.db.execute("SELECT json FROM facts WHERE source=? AND record_id=?", (source, record_id)).fetchall()
        return [Fact.model_validate_json(r["json"]) for r in rows]

    # ---- Agent step log ----
    def log_step(self, run_id: str, agent: str, kind: str, payload: Any) -> None:
        self.db.execute("INSERT INTO steps (ts, run_id, agent, kind, payload) VALUES (?,?,?,?,?)",
                        (time.time(), run_id, agent, kind, json.dumps(payload, default=str)))
        self.db.commit()

    def steps(self, run_id: str) -> list[dict]:
        rows = self.db.execute("SELECT * FROM steps WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        return [dict(r) | {"payload": json.loads(r["payload"])} for r in rows]
