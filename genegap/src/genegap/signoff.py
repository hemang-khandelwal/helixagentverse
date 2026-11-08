"""Task 5.1 (full guide 6.3): explicit, persisted human review of findings.

The gate records decisions, not reviewer authentication. The calling application must
supply the identity of the person making the decision. A status string alone never
authorizes final export: the current content must match an audited human decision.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from .schemas import Fact, Flag
from .store import Store


_AGENT = "human_review"
_KIND = "signoff"
_DECISIONS = {"approve", "reject", "annotate"}


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _content(flag: Flag) -> dict[str, Any]:
    return flag.model_dump(mode="json", exclude={"status"})


def _flag_id(flag: Flag) -> str:
    # Flag has no persistent ID. Variant + exact claim distinguish findings about
    # different groups for the same variant without changing the shared schema.
    return _digest([flag.variant_id, flag.claim])


class SignOffGate:
    """Persist human decisions for one run and enforce them at final export."""

    def __init__(self, store: Store, run_id: str) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("A nonblank run_id is required for human review.")
        self.store = store
        self.run_id = run_id.strip()

    @staticmethod
    def _validate_flag(flag: Flag) -> None:
        if not isinstance(flag, Flag):
            raise ValueError("Human review requires a Flag record.")
        if not flag.variant_id.strip() or not flag.claim.strip():
            raise ValueError("A reviewed flag needs a nonblank variant_id and claim.")

    def _require_evidence(self, flag: Flag) -> None:
        if flag.evidence_tier not in {1, 2, 3, 4, 5}:
            raise ValueError("Approval requires an evidence_tier from 1 to 5.")
        if not flag.citations or any(not citation.strip() for citation in flag.citations):
            raise ValueError("Approval requires nonblank supporting citations.")
        if not flag.fact_ids:
            raise ValueError("Approval requires at least one supporting Fact.")
        missing = [fact_id for fact_id in flag.fact_ids if self.store.get_fact(fact_id) is None]
        if missing:
            raise ValueError("Approval references missing Facts: " + ", ".join(missing))

    def _fact_snapshots(self, flag: Flag) -> dict[str, Any]:
        """Bind the review to every source field, including retrieval timestamps.

        Fact IDs omit URL, quote, note, method and retrieval time. These fields are
        shown to reviewers, so a later change must invalidate the decision even
        when the Fact's ID remains unchanged. Missing Facts can be recorded for a
        rejected or annotated draft, but never for an approval.
        """
        snapshots = {}
        for fact_id in sorted(set(flag.fact_ids)):
            fact = self.store.get_fact(fact_id)
            snapshots[fact_id] = fact.model_dump(mode="json") if fact else None
        return snapshots

    def _facts_current(self, flag: Flag, decision: dict[str, Any]) -> bool:
        snapshots = decision.get("fact_snapshots")
        return (
            isinstance(snapshots, dict)
            and snapshots == self._fact_snapshots(flag)
            and decision.get("fact_fingerprint") == _digest(snapshots)
        )

    def decision_for(self, flag: Flag) -> dict[str, Any] | None:
        """Return the latest audited decision for this run and finding, if any.

        ``stale`` is true when the finding's evidence, citations, stored sources,
        or upstream critic clearance changed. Editing the claim identifies a new finding.
        Annotation is a review note and does not authorize final export.

        Payload keys are ``flag_id``, ``fingerprint``, ``decision``, ``reviewer``,
        ``reviewed_at`` (UTC ISO timestamp), ``note``, ``previous_status``, ``flag``
        (the full reviewed snapshot), ``fact_snapshots``, ``fact_fingerprint``,
        ``critic_clearance_withdrawn``, and ``stale``.
        """
        self._validate_flag(flag)
        flag_id = _flag_id(flag)
        for step in reversed(self.store.steps(self.run_id)):
            if step["agent"] != _AGENT or step["kind"] != _KIND:
                continue
            payload = step["payload"]
            if not isinstance(payload, dict) or payload.get("flag_id") != flag_id:
                continue
            decision = dict(payload)
            snapshot = payload.get("flag")
            # Status is excluded from the content hash to allow restoring a
            # human decision onto its original critic-cleared input. It must
            # not let that restore undo a later upstream draft/rejection.
            decision["critic_clearance_withdrawn"] = (
                flag.status in {"draft", "rejected"}
                and isinstance(snapshot, dict)
                and snapshot.get("status") in {"critic_passed", "human_approved"}
            )
            decision["stale"] = (
                payload.get("fingerprint") != _digest(_content(flag))
                or not self._facts_current(flag, payload)
                or decision["critic_clearance_withdrawn"]
            )
            return decision
        return None

    def _has_prior_approval(self, flag: Flag) -> bool:
        # An annotation makes final export pending again, but need not erase an
        # unchanged finding's prior critic clearance. A rejection does erase it.
        for step in reversed(self.store.steps(self.run_id)):
            payload = step["payload"]
            if (
                step["agent"] != _AGENT
                or step["kind"] != _KIND
                or not isinstance(payload, dict)
                or payload.get("flag_id") != _flag_id(flag)
                or payload.get("decision") == "annotate"
            ):
                continue
            return (
                payload.get("decision") == "approve"
                and payload.get("fingerprint") == _digest(_content(flag))
                and self._facts_current(flag, payload)
                and self._valid_audit(payload)
            )
        return False

    def review(
        self,
        flag: Flag,
        decision: str = "approve",
        reviewer: str = "",
        note: str = "",
    ) -> Flag:
        """Record an explicit approve, reject, or annotate action; return a new Flag.

        Approvals require critic clearance, cited evidence and stored provenance.
        Rejections and annotations can be made while a finding is still a draft.
        The caller's original record is never mutated.
        """
        self._validate_flag(flag)
        if not isinstance(decision, str) or decision not in _DECISIONS:
            raise ValueError("Decision must be approve, reject, or annotate.")
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError("A nonblank human reviewer identity is required.")
        if not isinstance(note, str):
            raise ValueError("The review note must be text.")
        if decision == "annotate" and not note.strip():
            raise ValueError("An annotation requires a nonblank note.")

        if decision == "approve":
            prior_approval = flag.status == "human_approved" and self._has_prior_approval(flag)
            if flag.status != "critic_passed" and not prior_approval:
                raise ValueError("Approval requires critic_passed status or a valid prior human approval.")
            self._require_evidence(flag)

        new_status = {"approve": "human_approved", "reject": "rejected"}.get(decision, flag.status)
        reviewed = flag.model_copy(deep=True, update={"status": new_status})
        fact_snapshots = self._fact_snapshots(reviewed)
        self.store.log_step(
            self.run_id,
            _AGENT,
            _KIND,
            {
                "flag_id": _flag_id(reviewed),
                "fingerprint": _digest(_content(reviewed)),
                "fact_snapshots": fact_snapshots,
                "fact_fingerprint": _digest(fact_snapshots),
                "decision": decision,
                "reviewer": reviewer.strip(),
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
                "note": note,
                "previous_status": flag.status,
                "flag": reviewed.model_dump(mode="json"),
            },
        )
        return reviewed

    @staticmethod
    def _valid_audit(decision: dict[str, Any]) -> bool:
        """Validate that an audit entry contains the reviewed snapshot and identity."""
        try:
            snapshot = Flag.model_validate(decision["flag"])
            facts = decision["fact_snapshots"]
            if not isinstance(facts, dict) or set(facts) != set(snapshot.fact_ids):
                return False
            if _digest(facts) != decision["fact_fingerprint"]:
                return False
            for fact_id, fact in facts.items():
                if fact is None:
                    if decision["decision"] == "approve":
                        return False
                elif Fact.model_validate(fact).id != fact_id:
                    return False
            reviewed_at = datetime.fromisoformat(decision["reviewed_at"])
            expected_status = {"approve": "human_approved", "reject": "rejected"}.get(decision["decision"])
            return (
                isinstance(decision["reviewer"], str)
                and bool(decision["reviewer"].strip())
                and reviewed_at.tzinfo is not None
                and expected_status is not None
                and snapshot.status == expected_status
                and _flag_id(snapshot) == decision["flag_id"]
                and _digest(_content(snapshot)) == decision["fingerprint"]
            )
        except (KeyError, TypeError, ValueError):
            return False

    def require_finalizable(self, flags: Iterable[Flag]) -> None:
        """Raise if any current finding lacks an audited approve/reject decision.

        At least one finding must have been reviewed. Reviewed rejections are
        allowed so a report can omit them. Every decision is checked against the
        complete current Fact snapshots before export.
        """
        problems: list[str] = []
        seen: set[str] = set()
        for flag in flags:
            self._validate_flag(flag)
            flag_id = _flag_id(flag)
            if flag_id in seen:
                problems.append(f"{flag.variant_id} ({flag.claim}): duplicate finding identity")
                continue
            seen.add(flag_id)
            decision = self.decision_for(flag)
            label = f"{flag.variant_id} ({flag.claim})"
            if decision is None:
                problems.append(f"{label}: no audited human decision")
            elif decision["critic_clearance_withdrawn"]:
                problems.append(f"{label}: critic clearance withdrawn; a new decision is required")
            elif decision["stale"]:
                problems.append(f"{label}: content changed since human review; review again")
            elif decision.get("decision") == "annotate":
                problems.append(f"{label}: annotated but still needs approval or rejection")
            elif not self._valid_audit(decision):
                problems.append(f"{label}: invalid human review audit")
            else:
                expected_status = {"approve": "human_approved", "reject": "rejected"}[decision["decision"]]
                if flag.status != expected_status:
                    problems.append(f"{label}: status does not match the audited human decision")
                elif decision["decision"] == "approve":
                    try:
                        self._require_evidence(flag)
                    except ValueError as exc:
                        problems.append(f"{label}: {exc}")
        if not seen:
            raise ValueError("Final export requires at least one analyzed finding and an audited human decision.")
        if problems:
            raise ValueError("Final export requires human review: " + "; ".join(problems))
