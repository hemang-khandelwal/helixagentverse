"""Run a trusted orchestrator and publish a validated handoff for human review."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from pydantic import ValidationError

from genegap.store import Store
from integration.entrypoint import invoke, validate_callable
from review.session import ReviewDraft, ReviewSession


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: dict) -> None:
    """Publish complete JSON, leaving readers no partially written handoff."""
    pending = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".pending-", suffix=".json", encoding="utf-8", delete=False) as handle:
            pending = Path(handle.name)
            json.dump(value, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, path)
    finally:
        if pending is not None and pending.exists():
            pending.unlink()


@dataclass(frozen=True)
class CompletedRun:
    run_id: str
    directory: Path
    draft_path: Path
    db_path: Path


class HandoffError(ValueError):
    """An actionable connector validation failure without raw producer inputs."""


class RunFailed(RuntimeError):
    def __init__(self, run_id: str, directory: Path, cause: BaseException):
        reason = f" {cause}" if isinstance(cause, HandoffError) else ""
        super().__init__(f"Orchestrator run {run_id} failed ({type(cause).__name__}).{reason} No review handoff was published. Details: {directory / 'run.json'}")
        self.run_id = run_id
        self.directory = directory
        self.cause = cause


def _validate_handoff(raw, store: Store, run_id: str) -> ReviewDraft:
    try:
        if isinstance(raw, ReviewDraft):
            draft = ReviewDraft.model_validate(raw.model_dump(mode="json", warnings=False))
        elif isinstance(raw, dict):
            draft = ReviewDraft.model_validate(raw)
        else:
            raise HandoffError("The orchestrator must return ReviewDraft or a compatible dictionary.")
        # Existing Pydantic instances allow unchecked attribute updates. Revalidate
        # the complete serialized tree, including any nested Flag model instances.
        draft = ReviewDraft.model_validate(draft.model_dump(mode="json", warnings=False))
    except ValidationError as exc:
        issues = [f"{'.'.join(str(part) for part in item['loc']) or 'result'}: {item['msg']}"
                  for item in exc.errors(include_input=False, include_url=False)[:5]]
        raise HandoffError("Invalid handoff fields: " + "; ".join(issues)) from exc
    if draft.run_id != run_id:
        raise HandoffError("The orchestrator returned a different run_id from the one it was given.")
    for flag in draft.flags:
        if flag.status == "human_approved":
            raise HandoffError("The orchestrator cannot supply human approval. Findings need a separate human decision.")
        for fact_id in flag.fact_ids:
            fact = store.get_fact(fact_id)
            if fact is None or fact.id != fact_id:
                raise HandoffError(f"The orchestrator did not persist referenced Fact {fact_id} in the supplied Store.")
        if flag.status == "critic_passed" and (
            flag.evidence_tier is None or not flag.fact_ids or not flag.citations
            or any(not citation.strip() for citation in flag.citations)
        ):
            raise HandoffError("A critic-cleared finding must include an evidence tier, citations and stored source Facts.")
    if any(step["agent"] == "human_review" and step["kind"] == "signoff" for step in store.steps(run_id)):
        raise HandoffError("The orchestrator recorded a human decision before the review interface opened.")
    return draft


def run_pipeline(
    callback: Callable,
    query: str,
    *,
    output_root: Path,
    db_path: Optional[Path] = None,
    on_event: Optional[Callable[[str, dict], None]] = None,
) -> CompletedRun:
    """Call an actual supplied orchestrator; no mock or deterministic fallback.

    The callable receives a fresh run ID, the same Store used by the reviewer,
    and a synchronous progress callback. Facts and trace history persist even
    if the producer fails; a failed run never receives a published draft.
    """
    validate_callable(callback)
    if not isinstance(query, str) or not query.strip():
        raise ValueError("A nonblank drug, gene or condition query is required.")
    run_id = "run-" + uuid.uuid4().hex
    directory = Path(output_root).resolve() / run_id
    directory.mkdir(parents=True, exist_ok=False)
    database = Path(db_path).resolve() if db_path else directory / "run.sqlite"
    draft_path = directory / "draft.json"
    trace_path = directory / "events.jsonl"
    manifest = {
        "task": "6.4", "run_id": run_id, "query": query,
        "producer": f"{getattr(callback, '__module__', type(callback).__module__)}:{getattr(callback, '__qualname__', type(callback).__qualname__)}",
        "status": "running", "started_at": _now(), "finished_at": None,
        "database": str(database), "draft": None, "trace": str(trace_path),
    }
    _write_json(directory / "run.json", manifest)
    store = None
    pending_steps = deque()

    def flush_steps() -> None:
        # Store.log_step commits its connection. Delay audit writes while the
        # producer owns a transaction rather than accidentally committing it.
        if store is None or store.db.in_transaction:
            return
        while pending_steps:
            kind, encoded = pending_steps[0]
            store.log_step(run_id, "integration", kind, json.loads(encoded))
            pending_steps.popleft()

    try:
        store = Store(database)
        with trace_path.open("w", encoding="utf-8") as trace:
            def emit(kind: str, payload: dict) -> None:
                if not isinstance(kind, str) or not kind.strip() or not isinstance(payload, dict):
                    raise ValueError("Progress events need a nonblank kind and a JSON-object payload.")
                # Round-trip before writing so the trace has a fixed JSON value,
                # independent of later producer mutations to the same dictionary.
                encoded = json.dumps(payload, allow_nan=False)
                clean = json.loads(encoded)
                event = {"time": _now(), "kind": kind, "payload": clean}
                trace.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
                trace.flush()
                pending_steps.append((kind, encoded))
                flush_steps()
                if on_event:
                    on_event(kind, clean)

            emit("started", {"query": query})
            raw = invoke(callback, query, store=store, run_id=run_id, on_event=emit)
            flush_steps()
            # Hold source records stable while validating and publishing the
            # handoff. The human-review interface will check them again later.
            with store.db:
                if store.db.in_transaction:
                    raise ValueError("The orchestrator returned with an unfinished database transaction.")
                store.db.execute("BEGIN IMMEDIATE")
                draft = _validate_handoff(raw, store, run_id)
                _write_json(draft_path, draft.model_dump(mode="json"))
            emit("ready_for_review", {"findings": len(draft.flags)})
        manifest.update(status="ready_for_review", finished_at=_now(), draft=str(draft_path))
        _write_json(directory / "run.json", manifest)
        return CompletedRun(run_id, directory, draft_path, database)
    except (Exception, KeyboardInterrupt, SystemExit, asyncio.CancelledError) as exc:
        interrupted = isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError))
        terminal_kind = "interrupted" if interrupted else "failed"
        audit_errors = []

        def recover(operation, action):
            # Cleanup errors must not replace the original producer failure or
            # cancellation. Record their types when the manifest remains writable.
            try:
                action()
            except Exception as cleanup_error:
                audit_errors.append({"operation": operation, "error_type": type(cleanup_error).__name__})

        # Roll back unfinished producer work before flushing queued audit steps.
        # Otherwise Store.log_step would commit that work while recording failure.
        if store is not None:
            recover("rollback", store.db.rollback)
        # Remove only this run's provisional handoff if a later completion step
        # failed. Existing completed runs and their reviews are never replaced.
        if draft_path.exists():
            recover("remove_handoff", draft_path.unlink)
        terminal_payload = {"error_type": type(exc).__name__, "draft_published": False}
        terminal_event = {"time": _now(), "kind": terminal_kind, "payload": terminal_payload}

        def append_terminal_trace():
            with trace_path.open("a", encoding="utf-8") as terminal_trace:
                terminal_trace.write(json.dumps(terminal_event, ensure_ascii=False) + "\n")

        # Append directly: a failing UI observer must not be called again during
        # failure handling, and ready_for_review must not remain the final event.
        recover("terminal_trace", append_terminal_trace)
        pending_steps.append((terminal_kind, json.dumps(terminal_payload)))
        recover("audit_steps", flush_steps)
        manifest.update(status=terminal_kind, finished_at=_now(), error_type=type(exc).__name__, draft=None)
        if isinstance(exc, HandoffError):
            manifest["validation_summary"] = str(exc)
        if audit_errors:
            manifest["audit_errors"] = audit_errors
        recover("failure_manifest", lambda: _write_json(directory / "run.json", manifest))
        if interrupted:
            raise
        raise RunFailed(run_id, directory, exc) from exc
    finally:
        if store is not None:
            store.db.close()


def open_review(run: CompletedRun, store: Store) -> ReviewSession:
    """Open the exact validated handoff with its original database."""
    files = [row["file"] for row in store.db.execute("PRAGMA database_list").fetchall() if row["name"] == "main"]
    if not files or not files[0] or Path(files[0]).resolve() != run.db_path.resolve():
        raise ValueError("The review interface must use the orchestrator run's original database.")
    session = ReviewSession(run.draft_path, store)
    draft, _views = session.overview()
    if draft.run_id != run.run_id:
        raise ValueError("The published handoff no longer matches the completed orchestrator run.")
    return session
