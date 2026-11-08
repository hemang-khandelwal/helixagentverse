"""Task 6.3: adapt the existing sign-off gate for a resumable reviewer interface."""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from genegap.reporting import render_report, validate_report_destination
from genegap.schemas import Fact, Flag
from genegap.signoff import SignOffGate
from genegap.store import Store


class ReviewDraft(BaseModel):
    """Accept the same handoff used by scripts/review_report.py."""

    model_config = ConfigDict(extra="forbid")
    run_id: str
    title: str = "GeneGap findings"
    flags: list[Flag]
    limitations: list[str] = Field(default_factory=list)

    @field_validator("run_id")
    @classmethod
    def nonblank_run(cls, value):
        if not value.strip():
            raise ValueError("A nonblank run_id is required.")
        return value.strip()

    @model_validator(mode="after")
    def unique_findings(self):
        identities = set()
        for flag in self.flags:
            identity = (flag.variant_id, flag.claim)
            if not flag.variant_id.strip() or not flag.claim.strip() or identity in identities:
                raise ValueError("Findings must have nonblank, unique variant/claim identities.")
            identities.add(identity)
        return self


@dataclass(frozen=True)
class FindingView:
    index: int
    run_id: str
    flag: Flag
    source_flag: Flag
    facts: tuple[Optional[Fact], ...]
    decision: Optional[dict[str, Any]]
    status: str
    revision: str


class ReviewSession:
    """Own UI state only; all review and export rules remain in SignOffGate.

    Every operation reloads the handoff. A review revision binds the visible
    finding, source records and latest decision. Short SQLite transactions keep
    these checks and the gate's write consistent with other database writers.
    The caller owns the Store and must close it when the interface exits.
    """

    def __init__(self, draft_path: Path, store: Store):
        self.draft_path = Path(draft_path)
        self.store = store
        self._load()

    def _load(self):
        content = self.draft_path.read_bytes()
        return ReviewDraft.model_validate_json(content), hashlib.sha256(content).hexdigest()

    @contextmanager
    def _locked(self):
        if self.store.db.in_transaction:
            raise ValueError("Finish the existing database transaction before opening a review operation.")
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            yield

    def _view(self, draft: ReviewDraft, draft_hash: str, index: int) -> FindingView:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(draft.flags):
            raise ValueError("Choose a finding number from the current queue.")
        source_flag = draft.flags[index]
        gate = SignOffGate(self.store, draft.run_id)
        decision = gate.decision_for(source_flag)
        flag = source_flag
        status = "Awaiting review"
        if decision:
            if decision.get("critic_clearance_withdrawn"):
                status = "Critic clearance withdrawn — decision needed"
            elif decision["stale"]:
                status = "Review stale — reopen evidence"
            else:
                try:
                    flag = Flag.model_validate(decision["flag"])
                except (KeyError, ValueError) as exc:
                    raise ValueError("The saved review snapshot is invalid.") from exc
                if decision.get("decision") == "annotate":
                    status = "Annotated — decision needed"
                else:
                    try:
                        gate.require_finalizable([flag])
                    except ValueError:
                        status = "Review invalid — decision needed"
                    else:
                        status = "Approved" if flag.status == "human_approved" else "Rejected"
        facts = tuple(self.store.get_fact(fact_id) for fact_id in flag.fact_ids)
        visible = {
            "draft_hash": draft_hash,
            "source_flag": source_flag.model_dump(mode="json"),
            "facts": [fact.model_dump(mode="json") if fact else None for fact in facts],
            "decision": decision,
        }
        revision = hashlib.sha256(json.dumps(visible, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return FindingView(index, draft.run_id, flag, source_flag, facts, decision, status, revision)

    def overview(self) -> tuple[ReviewDraft, list[FindingView]]:
        with self._locked():
            draft, digest = self._load()
            return draft, [self._view(draft, digest, index) for index in range(len(draft.flags))]

    def view(self, index: int) -> FindingView:
        with self._locked():
            draft, digest = self._load()
            return self._view(draft, digest, index)

    def review(self, index: int, decision: str, reviewer: str, note: str, *, revision: str) -> Flag:
        """Record only an explicit decision about the exact version shown to a user."""
        with self._locked():
            draft, digest = self._load()
            current = self._view(draft, digest, index)
            if revision != current.revision:
                raise ValueError("The finding, sources or review changed. Reopen the finding before deciding.")
            # The upstream handoff can supply a critic-cleared revision for an
            # explicit reconsideration. Never synthesize critic clearance here.
            flag = current.source_flag if decision == "approve" and current.source_flag.status == "critic_passed" else current.flag
            return SignOffGate(self.store, draft.run_id).review(flag, decision, reviewer, note)

    def history(self) -> list[dict]:
        with self._locked():
            draft, _digest = self._load()
            return [step for step in self.store.steps(draft.run_id)
                    if step["agent"] == "human_review" and step["kind"] == "signoff"]

    def report(self, *, final: bool = False) -> str:
        # Keep source records stable between finalization and report formatting.
        with self._locked():
            draft, digest = self._load()
            flags = [self._view(draft, digest, index).flag for index in range(len(draft.flags))]
            return render_report(flags, SignOffGate(self.store, draft.run_id), draft.title, draft.limitations, final=final)

    def export(self, path: Path, *, final: bool = False) -> Path:
        destination = Path(path)
        validate_report_destination(destination, self.draft_path, self.store)
        text = self.report(final=final)
        destination.parent.mkdir(parents=True, exist_ok=True)
        validate_report_destination(destination, self.draft_path, self.store)
        destination.write_text(text, encoding="utf-8")
        return destination
