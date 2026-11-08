"""Export a portable, consistent report and its supporting review records."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from genegap.reporting import render_report, validate_report_destination
from genegap.signoff import SignOffGate
from review.session import ReviewSession


def _json(value) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _snapshot(session: ReviewSession, final: bool) -> dict[str, bytes]:
    # Use the review controller's existing transaction and state restoration.
    # Encode every entry while locked, keeping report text, facts and decisions
    # from the same snapshot. No shared model or approval rule is changed.
    with session._locked():
        draft, digest = session._load()
        views = [session._view(draft, digest, index) for index in range(len(draft.flags))]
        flags = [view.flag for view in views]
        markdown = render_report(
            flags, SignOffGate(session.store, draft.run_id), draft.title,
            draft.limitations, final=final,
        )
        findings = [flag for flag in flags if not final or flag.status == "human_approved"]
        rejected = [flag for flag in flags if final and flag.status == "rejected"]
        facts = {}
        missing = set()
        for view in views:
            for fact_id, fact in zip(view.flag.fact_ids, view.facts):
                if fact is None:
                    missing.add(fact_id)
                else:
                    facts[fact_id] = fact.model_dump(mode="json")
        status = "final" if final else "draft"
        files = {
            "report.md": markdown.encode("utf-8"),
            "report.json": _json({
                "run_id": draft.run_id, "title": draft.title, "status": status,
                "findings": [flag.model_dump(mode="json") for flag in findings],
                "rejected_findings": [flag.model_dump(mode="json") for flag in rejected],
                "limitations": draft.limitations, "missing_fact_ids": sorted(missing),
            }),
            "facts.json": _json([facts[key] for key in sorted(facts)]),
            "reviews.json": _json([view.decision for view in views if view.decision is not None]),
        }
        files["manifest.json"] = _json({
            "task": "6.5", "run_id": draft.run_id, "status": status,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "input_sha256": digest,
            "sha256": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
        })
        return files


def export_bundle(session: ReviewSession, destination: Path, *, final: bool = False) -> Path:
    """Write a ZIP atomically; final export requires audited human decisions."""
    destination = Path(destination)
    validate_report_destination(destination, session.draft_path, session.store)
    files = _snapshot(session, final)
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".genegap-report-", suffix=".zip", delete=False) as handle:
            pending = Path(handle.name)
        with ZipFile(pending, "w", compression=ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        validate_report_destination(destination, session.draft_path, session.store)
        os.replace(pending, destination)
        return destination
    finally:
        if pending is not None and pending.exists():
            pending.unlink()
