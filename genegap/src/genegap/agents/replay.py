"""Task 7.2: record a good run once, replay it at the demo.

RECORD: run the team with the real Gemini. Everything Gemini says (its tool requests and its text) is saved.
REPLAY: run the team again, but a stand-in for Gemini reads the saved turns back. Our tools still run for real
on the cached data, so the numbers are computed live. Only Gemini's wording is replayed.

Safety check: while replaying, the tool results are compared with the ones seen when recording. If the data has
changed, the replay stops with a clear message instead of showing text that no longer matches the numbers.
Always tell judges when you are showing a replay.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .base import ChatFactory, LLMTurn, ToolCall


class ReplayMismatch(RuntimeError):
    pass


def _digest(results: list) -> str:
    return hashlib.sha1(json.dumps([r for _, r in results], sort_keys=True, default=str).encode()).hexdigest()[:16]


def _turn_to_json(t: LLMTurn) -> dict:
    return {"text": t.text, "tool_calls": [{"name": c.name, "args": c.args, "id": c.id} for c in t.tool_calls]}


def _turn_from_json(d: dict) -> LLMTurn:
    return LLMTurn(d["text"], [ToolCall(c["name"], c["args"], c.get("id")) for c in d["tool_calls"]])


class Recording:
    """Collects every chat (one per agent run) in the order they were started."""
    def __init__(self):
        self.chats: list[dict] = []

    def wrap(self, inner_factory: ChatFactory) -> ChatFactory:
        def make(system, tools):
            entry = {"turns": [], "result_digests": []}
            self.chats.append(entry)
            return _RecordingChat(inner_factory(system, tools), entry)
        return make

    def save(self, path: Path, **meta: Any) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"), **meta, "chats": self.chats}, indent=1))


class _RecordingChat:
    def __init__(self, inner, entry):
        self.inner, self.entry = inner, entry

    def send(self, user_text):
        turn = self.inner.send(user_text)
        self.entry["turns"].append(_turn_to_json(turn))
        return turn

    def send_tool_results(self, results):
        self.entry["result_digests"].append(_digest(results))
        turn = self.inner.send_tool_results(results)
        self.entry["turns"].append(_turn_to_json(turn))
        return turn


class Replay:
    def __init__(self, path: Path):
        self.data = json.loads(Path(path).read_text())
        self.next_chat = 0

    @property
    def meta(self) -> dict:
        return {k: v for k, v in self.data.items() if k != "chats"}

    def factory(self) -> ChatFactory:
        def make(system, tools):
            if self.next_chat >= len(self.data["chats"]):
                raise ReplayMismatch("The replay asked for more agents than were recorded. Re-record the run.")
            entry = self.data["chats"][self.next_chat]
            self.next_chat += 1
            return _ReplayChat(entry)
        return make


class _ReplayChat:
    def __init__(self, entry):
        self.turns = [_turn_from_json(t) for t in entry["turns"]]
        self.digests = list(entry["result_digests"])

    def _next(self) -> LLMTurn:
        if not self.turns:
            raise ReplayMismatch("The replay ran out of recorded turns. Re-record the run.")
        return self.turns.pop(0)

    def send(self, user_text):
        return self._next()

    def send_tool_results(self, results):
        want = self.digests.pop(0) if self.digests else None
        if want is not None and want != _digest(results):
            raise ReplayMismatch("The data is not the same as when this run was recorded, so the recorded "
                                 "wording would not match the numbers. Re-record with: python scripts/demo.py --record")
        return self._next()
