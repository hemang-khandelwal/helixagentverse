"""SQLite persistence store for HTTP caching, provenance facts, and agent step logs."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional, Union

from .config import DB_PATH
from .schemas import Fact

# Database table definitions:
# - http_cache: caches external API responses by request hash so repeat queries are instant
#   and the application can run offline during live presentations.
# - facts: immutable table of every verified scientific datum retrieved, indexed by source and ID.
# - steps: audit log of every action, prompt, and tool call taken by each agent.
SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache (
    key TEXT PRIMARY KEY,
    url TEXT,
    status INTEGER,
    body TEXT,
    fetched_at REAL
);

CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    source TEXT,
    record_id TEXT,
    json TEXT
);
CREATE INDEX IF NOT EXISTS facts_rec ON facts(source, record_id);

CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL,
    run_id TEXT,
    agent TEXT,
    kind TEXT,
    payload TEXT
);
"""


class Store:
    """
    Central database manager for caching web requests, tracking data provenance,
    and recording multi-agent execution histories.
    """

    def __init__(self, path: Union[str, Path] = DB_PATH) -> None:
        # Create the parent directory (e.g. data/) if it does not yet exist
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        # sqlite3.Row enables dict-like column access (row["body"], row["url"])
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---- HTTP cache methods ----

    def cache_get(self, key: str, max_age_s: Optional[float] = None) -> Optional[sqlite3.Row]:
        """
        Look up a cached response by its request hash key.
        If max_age_s is specified, expired entries are ignored.
        """
        row = self.db.execute("SELECT * FROM http_cache WHERE key=?", (key,)).fetchone()
        if row and max_age_s is not None and time.time() - row["fetched_at"] > max_age_s:
            return None
        return row

    def cache_put(self, key: str, url: str, status: int, body: str) -> None:
        """Save a successful API response body and status code into the cache."""
        self.db.execute(
            "INSERT OR REPLACE INTO http_cache VALUES (?,?,?,?,?)",
            (key, url, status, body, time.time()),
        )
        self.db.commit()

    # ---- Facts (provenance) methods ----

    def put_fact(self, fact: Fact, *, commit: bool = True) -> str:
        """
        Store a verified Fact record. Uses the fact's deterministic hash ID as primary key,
        so saving the same fact multiple times is idempotent and never creates duplicates.
        """
        self.db.execute(
            "INSERT OR REPLACE INTO facts VALUES (?,?,?,?)",
            (fact.id, fact.source, fact.record_id, fact.model_dump_json()),
        )
        if commit:
            self.db.commit()
        return fact.id

    def get_fact(self, fact_id: str) -> Optional[Fact]:
        """Look up a single Fact by its unique ID (e.g. 'F-a38f71c990b1')."""
        row = self.db.execute("SELECT json FROM facts WHERE id=?", (fact_id,)).fetchone()
        return Fact.model_validate_json(row["json"]) if row else None

    def facts_for_record(self, source: str, record_id: str) -> list[Fact]:
        """
        Look up all stored facts associated with a particular database entry
        (e.g. all facts recorded for source='gnomAD' and record_id='10-94781859-G-A').
        """
        rows = self.db.execute(
            "SELECT json FROM facts WHERE source=? AND record_id=?", (source, record_id)
        ).fetchall()
        return [Fact.model_validate_json(r["json"]) for r in rows]

    # ---- Agent step log methods ----

    def log_step(self, run_id: str, agent: str, kind: str, payload: Any, *, commit: bool = True) -> None:
        """
        Record one step of an agent run (e.g. 'plan', 'tool_call', 'tool_return', 'critique').
        This preserves full auditability of agent reasoning and decisions.
        """
        self.db.execute(
            "INSERT INTO steps (ts, run_id, agent, kind, payload) VALUES (?,?,?,?,?)",
            (time.time(), run_id, agent, kind, json.dumps(payload, default=str)),
        )
        if commit:
            self.db.commit()

    def steps(self, run_id: str) -> list[dict]:
        """Retrieve all recorded steps for an execution session in chronological order."""
        rows = self.db.execute("SELECT * FROM steps WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        return [dict(r) | {"payload": json.loads(r["payload"])} for r in rows]
