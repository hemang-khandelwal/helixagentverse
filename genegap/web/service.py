"""One application service for analysis, charts, evidence, review and exports."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import tempfile
import threading
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from charts.plots import plot_representation, plot_required_enrolment
from charts.render import ChartInput
from exports.bundle import export_bundle
from genegap import config
from genegap.agents import frontdoor
from genegap.agents.critic import audit_flag
from genegap.agents.orchestrator import run_offline_query, run_query
from genegap.agents.planning import load_catalog
from genegap.schemas import Fact
from genegap.signoff import SignOffGate
from genegap.store import Store
from integration.bridge import RunFailed, run_pipeline
from review.session import ReviewSession


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=0, strict=True)
    revision: str = Field(min_length=1)
    reviewer: str = Field(min_length=1, max_length=120)
    evidence_tier: int = Field(ge=1, le=5, strict=True)
    citations: list[str] = Field(min_length=1, max_length=10)
    note: str = Field(min_length=12, max_length=4000)
    quote: str = Field(default="", max_length=500)

    @field_validator("reviewer", "note")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("A reviewer name and evidence context are required.")
        return value.strip()

    @field_validator("citations")
    @classmethod
    def valid_citations(cls, values):
        cleaned = list(dict.fromkeys(value.strip() for value in values))
        if any(not re.fullmatch(r"(?:https?://\S+|(?:doi:)?10\.\d{4,9}/\S+|PMID:\s*\d+)", value, re.I) for value in cleaned):
            raise ValueError("Use source web URLs, DOI identifiers or PMID:number citations.")
        return cleaned

    @field_validator("quote")
    @classmethod
    def short_quote(cls, value):
        if len(value.split()) > 15:
            raise ValueError("Supporting excerpts must be at most 15 words.")
        return value


def _atomic_json(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, suffix=".json", delete=False, encoding="utf-8") as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


class AppService:
    def __init__(self, root: Path, *, legacy_roots=()):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.legacy_roots = [Path(path).resolve() for path in legacy_roots]
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.chart_lock = threading.RLock()
        self.jobs = {}
        self.run_locks = {}
        self.slots = threading.BoundedSemaphore(2)
        self.closed = False
        # Only recover our own runtime directory, never a separate CLI's jobs.
        for manifest in self.root.glob("run-*/run.json"):
            try:
                value = json.loads(manifest.read_text())
                if value.get("status") == "running":
                    value.update(status="interrupted", error_type="ApplicationRestart", finished_at=datetime.now(timezone.utc).isoformat())
                    _atomic_json(manifest, value)
            except (OSError, ValueError):
                continue

    def status(self):
        return {"name": "GeneGap", "csrf_token": self.token,
                "live_available": bool(config.GEMINI_API_KEY), "model": config.GEMINI_MODEL,
                "catalog": load_catalog(), "default_mode": "offline"}

    def runs(self):
        found = {}
        for root in [*self.legacy_roots, self.root]:
            for path in root.glob("run-*/run.json"):
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                    run_id = value["run_id"]
                    if re.fullmatch(r"run-[a-f0-9]{32}", run_id) and path.parent.name == run_id:
                        found[run_id] = (path.parent, value)
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        return found

    def list_runs(self):
        return sorted([{"id": key, "query": value.get("query", ""), "status": value.get("status"),
                        "started_at": value.get("started_at"), "error_type": value.get("error_type")}
                       for key, (_directory, value) in self.runs().items()], key=lambda item: item["started_at"] or "", reverse=True)[:100]

    def _paths(self, run_id):
        if not re.fullmatch(r"run-[a-f0-9]{32}", run_id):
            raise KeyError("Unknown analysis.")
        found = self.runs().get(run_id)
        if not found:
            raise KeyError("Unknown analysis.")
        directory, manifest = found
        if manifest.get("status") != "ready_for_review":
            raise ValueError("This analysis has not produced a reviewable result.")
        draft = Path(manifest["draft"])
        if draft.resolve() != (directory / "draft.json").resolve():
            raise ValueError("The run's saved handoff is inconsistent.")
        return directory, draft, Path(manifest["database"]), manifest

    def _run_lock(self, run_id):
        with self.lock:
            return self.run_locks.setdefault(run_id, threading.RLock())

    def start(self, query, mode="offline"):
        if not isinstance(query, str) or not query.strip() or len(query) > 4000:
            raise ValueError("Enter a short drug, gene or condition query.")
        if mode not in {"offline", "live"}:
            raise ValueError("Choose recorded demo or live analysis.")
        if mode == "live" and not config.GEMINI_API_KEY:
            raise ValueError("Live analysis needs GEMINI_API_KEY in .env. The recorded demo works immediately.")
        # Front door: refuse advice and out-of-scope input before any model runs, and pass
        # only the matched drug name on, so typed text can never steer the agents.
        fast_factory = None
        if mode == "live":
            try:
                from genegap.agents.base import gemini_chat_factory
                fast_factory = gemini_chat_factory(config.GEMINI_FAST_MODEL)
            except Exception:
                fast_factory = None
        decision = frontdoor.decide(query, frontdoor.load_scope(), fast_factory)
        refusal = decision.message if decision.action != "run" else None
        if not refusal:
            query = decision.drug
        if self.closed or not self.slots.acquire(blocking=False):
            raise ValueError("Two analyses are already running. Please wait for one to finish.")
        job_id = uuid.uuid4().hex
        with self.lock:
            self.jobs[job_id] = {"id": job_id, "status": "running", "run_id": None, "events": [], "error": None}

        def progress(kind, payload):
            with self.lock:
                self.jobs[job_id]["events"].append({"kind": kind, "payload": payload})
                self.jobs[job_id]["events"] = self.jobs[job_id]["events"][-200:]

        def producer(query, *, store, run_id, on_event):
            with self.lock:
                self.jobs[job_id]["run_id"] = run_id
            if refusal:   # nothing reaches a model or an agent: show the refusal as the result
                on_event("refused", {"agent": "front_door", "reason": decision.reason})
                return {"run_id": run_id, "title": "Request not analysed", "flags": [], "limitations": [refusal]}
            callback = run_offline_query if mode == "offline" else run_query
            return callback(query, store=store, run_id=run_id, on_event=on_event)

        def work():
            try:
                result = run_pipeline(producer, query, output_root=self.root, on_event=progress)
                with self.lock:
                    self.jobs[job_id].update(status="ready_for_review", run_id=result.run_id)
            except BaseException as exc:
                cause = exc.cause if isinstance(exc, RunFailed) else exc
                with self.lock:
                    self.jobs[job_id].update(status="failed", error=f"{type(cause).__name__}: {cause}")
                    if isinstance(exc, RunFailed):
                        self.jobs[job_id]["run_id"] = exc.run_id
            finally:
                self.slots.release()
        threading.Thread(target=work, name=f"genegap-{job_id[:8]}", daemon=True).start()
        return {"job_id": job_id}

    def job(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise KeyError("Unknown analysis job.")
            return json.loads(json.dumps(self.jobs[job_id]))

    def detail(self, run_id):
        with self._run_lock(run_id):
            _directory, draft_path, db_path, manifest = self._paths(run_id)
            store = Store(db_path)
            try:
                session = ReviewSession(draft_path, store)
                with session._locked():
                    draft, digest = session._load()
                    views = [session._view(draft, digest, index) for index in range(len(draft.flags))]
                    facts = {fact.id: fact.model_dump(mode="json") for view in views for fact in view.facts if fact}
                    computed = store.db.execute("SELECT json FROM facts WHERE source=? AND record_id LIKE ? AND json LIKE ?", ("GeneGap.stats", run_id + ":%", '%"gap_result"%')).fetchall()
                    gaps = []
                    for record in computed:
                        fact = Fact.model_validate_json(record["json"])
                        if fact.field == "gap_result":
                            envelope = ChartInput.model_validate(fact.value).model_dump(mode="json")
                            gaps.append({"fact_id": fact.id, **envelope})
                            facts[fact.id] = fact.model_dump(mode="json")
                    flags = []
                    for view in views:
                        review = None
                        if view.decision:
                            review = {key: view.decision.get(key) for key in ("decision", "reviewer", "note", "reviewed_at", "stale")}
                        flags.append({"index": view.index, **view.flag.model_dump(mode="json"),
                                      "review_status": view.status, "revision": view.revision,
                                      "pipeline_status": view.source_flag.status, "review": review})
                    try:
                        SignOffGate(store, draft.run_id).require_finalizable([view.flag for view in views])
                        finalizable, blocked = True, ""
                    except ValueError as exc:
                        finalizable, blocked = False, str(exc)
                    critique = [step["payload"] for step in store.steps(run_id) if step["agent"] == "critic"]
                    trace = [step for step in store.steps(run_id) if step["agent"] == "orchestrator"]
                    return {"id": run_id, "query": manifest.get("query"), "title": draft.title,
                            "started_at": manifest.get("started_at"), "limitations": draft.limitations,
                            "flags": flags, "facts": list(facts.values()), "gaps": gaps,
                            "can_finalize": finalizable, "final_blocked": blocked, "critique": critique,
                            "trace": [{"kind": step["kind"], "payload": step["payload"]} for step in trace],
                            "counts": {"findings": len(flags), "approved": sum(view.status == "Approved" for view in views),
                                       "rejected": sum(view.status == "Rejected" for view in views), "sources": len(facts)}}
            finally:
                store.db.close()

    def review(self, run_id, body):
        with self._run_lock(run_id):
            _directory, draft_path, database, _manifest = self._paths(run_id)
            store = Store(database)
            try:
                if not isinstance(body.get("index"), int) or isinstance(body["index"], bool):
                    raise ValueError("Choose a finding to review.")
                ReviewSession(draft_path, store).review(body["index"], body["decision"], body.get("reviewer", ""), body.get("note", ""), revision=body.get("revision", ""))
            finally:
                store.db.close()
        return self.detail(run_id)

    def evidence(self, run_id, body):
        data = EvidenceInput.model_validate(body)
        with self._run_lock(run_id):
            _directory, draft_path, database, _manifest = self._paths(run_id)
            store = Store(database)
            original = draft_path.read_bytes()
            changed = False
            try:
                session = ReviewSession(draft_path, store)
                with session._locked():
                    draft, digest = session._load()
                    view = session._view(draft, digest, data.index)
                    if view.revision != data.revision:
                        raise ValueError("The finding or sources changed. Reopen it before grading evidence.")
                    evidence = Fact(field="human_evidence", source="GeneGap.human_evidence", method="curated",
                                    record_id=f"{run_id}:{hashlib.sha256(view.flag.claim.encode()).hexdigest()[:16]}",
                                    value={"evidence_tier": data.evidence_tier, "citations": data.citations,
                                           "reviewer": data.reviewer, "note": data.note}, quote=data.quote or None,
                                    note="Reviewer-supplied evidence assessment, not independently verified by a language model.")
                    store.put_fact(evidence, commit=False)
                    ids = [key for key in view.source_flag.fact_ids if (store.get_fact(key) is None or store.get_fact(key).source != "GeneGap.human_evidence")]
                    updated = view.source_flag.model_copy(deep=True, update={"evidence_tier": data.evidence_tier,
                              "citations": data.citations, "fact_ids": [*ids, evidence.id], "status": "draft"})
                    critique = audit_flag(updated, store)
                    if critique.passed:
                        updated.status = "critic_passed"
                    store.log_step(run_id, "critic", "numerical_review", {"variant_id": updated.variant_id,
                                   "claim": updated.claim, "evidence_reviewer": data.reviewer, **asdict(critique)}, commit=False)
                    draft.flags[data.index] = updated
                    _atomic_json(draft_path, draft.model_dump(mode="json"))
                    changed = True
            except BaseException:
                if changed:
                    draft_path.write_bytes(original)
                raise
            finally:
                store.db.close()
        return self.detail(run_id)

    def chart(self, run_id, variant, mapping, kind, fmt="svg"):
        if kind not in {"representation", "enrolment"} or fmt not in {"svg", "png"}:
            raise ValueError("Unknown chart format.")
        detail = self.detail(run_id)
        payload = next((gap for gap in detail["gaps"] if gap["result"]["variant_id"] == variant and gap["result"]["mapping_version"] == mapping), None)
        if payload is None:
            raise KeyError("Chart data was not available for this variant and mapping.")
        key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        directory = self._paths(run_id)[0] / "charts" / key
        path = directory / f"{kind}.{fmt}"
        with self.chart_lock, self._run_lock(run_id):
            if not path.exists():
                directory.mkdir(parents=True, exist_ok=True)
                # fact_id is transport metadata, not part of the shared chart schema.
                envelope = ChartInput.model_validate({name: value for name, value in payload.items() if name != "fact_id"})
                notes = [note for note in envelope.notes if any(term in note.lower() for term in
                         ("assumption", "placeholder", "fewer than", "maps nobody", "no share", "missing data", "review are pending"))][:6]
                figure = (plot_representation(envelope.result, notes=notes, target_label=envelope.target_label)
                          if kind == "representation" else plot_required_enrolment(envelope.result, notes=notes,
                              target_label=envelope.target_label, target_carriers=envelope.target_carriers))
                try:
                    output = io.BytesIO()
                    figure.savefig(output, format=fmt, dpi=150, bbox_inches="tight")
                    path.write_bytes(output.getvalue())
                finally:
                    figure.clear()
        return path.read_bytes()

    def export(self, run_id, *, final=False):
        with self._run_lock(run_id):
            directory, draft, database, _manifest = self._paths(run_id)
            store = Store(database)
            try:
                output = directory / "exports" / ("final.zip" if final else "draft.zip")
                export_bundle(ReviewSession(draft, store), output, final=final)
                return output.read_bytes()
            finally:
                store.db.close()

    def close(self):
        self.closed = True
