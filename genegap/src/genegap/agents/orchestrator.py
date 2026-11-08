"""Task 3.11: bounded planning and routing through the existing GeneGap core.

run_query uses actual Gemini planning and live cached API clients. The separately
named run_offline_query replays recorded data without a model or network calls.
Neither path fabricates evidence grading, critic clearance or human approval.
"""
from __future__ import annotations

from typing import Callable

from .. import config
from ..store import Store
from . import analysis, planning


MAX_EVENTS = 64
MAX_QUERY_LENGTH = 4000


def _run(query: str, *, store: Store, run_id: str, on_event: Callable, offline: bool) -> dict:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Provide a drug, gene or condition query.")
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError("The query is too long; provide a short aggregate research question.")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("A nonblank run_id is required.")
    if not callable(on_event):
        raise ValueError("on_event must be callable.")
    emitted = 0

    def emit(kind: str, payload: dict):
        nonlocal emitted
        emitted += 1
        if emitted > MAX_EVENTS:
            raise ValueError("The orchestrator reached its execution step limit.")
        store.log_step(run_id, "orchestrator", kind, payload)
        on_event(kind, {"agent": "orchestrator", **payload})

    mode = "recorded_offline" if offline else "live_gemini"
    emit("plan", {"mode": mode, "stage": "resolve query"})
    if not offline and not config.GEMINI_API_KEY:
        emit("warning", {"message": "Live planning needs GEMINI_API_KEY and an accessible GEMINI_MODEL. Use --offline for the recorded demo."})
    plan = planning.offline_plan(query) if offline else planning.gemini_plan(query)
    emit("plan", {"mode": mode, **plan.model_dump(mode="json")})
    result = analysis.execute_plan(plan, store=store, run_id=run_id, on_event=emit, offline=offline)
    if result.get("run_id") != run_id:
        raise ValueError("Analysis returned a different run_id.")
    if any(flag.get("status", "draft") != "draft" for flag in result.get("flags", [])):
        raise ValueError("This orchestrator produces drafts; critic and human decisions belong to their review stages.")
    emit("completed", {"mode": mode, "findings": len(result["flags"]), "status": "draft"})
    return result


def run_query(query: str, *, store: Store, run_id: str, on_event: Callable) -> dict:
    """Resolve with Gemini, then run actual cached API/data/statistics tools."""
    return _run(query, store=store, run_id=run_id, on_event=on_event, offline=False)


def run_offline_query(query: str, *, store: Store, run_id: str, on_event: Callable) -> dict:
    """Run the same analysis routing using curated names and recorded API data."""
    return _run(query, store=store, run_id=run_id, on_event=on_event, offline=True)
