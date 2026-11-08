"""Task 6.5 exports use synthetic findings and persisted human decisions."""
from __future__ import annotations

import hashlib
import json
import os
import zipfile
from datetime import datetime, timezone

import pytest

from exports.bundle import export_bundle
from genegap.schemas import Fact, Flag
from genegap.store import Store
from review.session import ReviewSession


@pytest.fixture
def bundle_session(tmp_path):
    store = Store(tmp_path / "review.sqlite")
    fact = Fact(
        field="synthetic_count",
        value={"count": 17, "group": "Test only — α"},
        source="Software-test fixture",
        record_id="TEST-RECORD",
        url="https://example.org/synthetic",
        method="curated",
        retrieved_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        quote="Synthetic source quote.",
        note="Synthetic data, not clinical evidence.",
    )
    store.put_fact(fact)
    flag = Flag(
        variant_id="TEST-VARIANT",
        claim="Synthetic software-test finding.",
        evidence_tier=3,
        citations=["TEST-CITATION"],
        fact_ids=[fact.id],
        status="critic_passed",
    )
    payload = {
        "run_id": "export-test",
        "title": "Synthetic test report",
        "flags": [flag.model_dump(mode="json")],
        "limitations": ["Synthetic test data only."],
    }
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(payload), encoding="utf-8")
    session = ReviewSession(draft_path, store)
    try:
        yield session, fact, payload
    finally:
        store.db.close()


def decide(session, index=0, decision="approve"):
    view = session.view(index)
    return session.review(
        index,
        decision,
        "Test reviewer",
        "Checked the synthetic fixture.",
        revision=view.revision,
    )


def read_bundle(path):
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_final_includes_exact_sources_and_verifiable_hashes(bundle_session, tmp_path):
    session, fact, payload = bundle_session
    approved = decide(session)

    destination = tmp_path / "nested" / "report.zip"
    assert export_bundle(session, destination, final=True) == destination
    entries = read_bundle(destination)

    assert set(entries) == {"report.md", "report.json", "facts.json", "reviews.json", "manifest.json"}
    report = json.loads(entries["report.json"])
    assert report == {
        "run_id": payload["run_id"],
        "title": payload["title"],
        "status": "final",
        "findings": [approved.model_dump(mode="json")],
        "rejected_findings": [],
        "limitations": payload["limitations"],
        "missing_fact_ids": [],
    }
    assert entries["report.md"].decode("utf-8") == session.report(final=True)
    assert "**FINAL" in entries["report.md"].decode("utf-8")
    assert json.loads(entries["facts.json"]) == [fact.model_dump(mode="json")]
    reviews = json.loads(entries["reviews.json"])
    assert len(reviews) == 1
    assert reviews[0]["decision"] == "approve"
    assert reviews[0]["reviewer"] == "Test reviewer"
    assert reviews[0]["stale"] is False
    assert reviews[0]["fact_snapshots"] == {fact.id: fact.model_dump(mode="json")}
    manifest = json.loads(entries["manifest.json"])
    assert {key: manifest[key] for key in ("task", "run_id", "status", "sha256")} == {
        "task": "6.5",
        "run_id": payload["run_id"],
        "status": "final",
        "sha256": {
            name: hashlib.sha256(content).hexdigest()
            for name, content in entries.items()
            if name != "manifest.json"
        },
    }
    assert manifest["input_sha256"] == hashlib.sha256(session.draft_path.read_bytes()).hexdigest()
    assert datetime.fromisoformat(manifest["created_at"]).tzinfo is not None


def test_rejected_finding_is_separate_from_approved_findings(bundle_session, tmp_path):
    session, _fact, payload = bundle_session
    rejected_input = dict(payload["flags"][0], variant_id="TEST-REJECTED", claim="Rejected synthetic claim.")
    payload["flags"].append(rejected_input)
    session.draft_path.write_text(json.dumps(payload), encoding="utf-8")
    approved = decide(session)
    rejected = decide(session, 1, "reject")

    entries = read_bundle(export_bundle(session, tmp_path / "final.zip", final=True))
    report = json.loads(entries["report.json"])
    assert report["findings"] == [approved.model_dump(mode="json")]
    assert report["rejected_findings"] == [rejected.model_dump(mode="json")]
    assert len(json.loads(entries["facts.json"])) == 1


def test_unreviewed_draft_is_explicit_and_contains_only_input_findings(bundle_session, tmp_path):
    session, _fact, payload = bundle_session
    entries = read_bundle(export_bundle(session, tmp_path / "draft.zip"))

    assert "**DRAFT" in entries["report.md"].decode("utf-8")
    report = json.loads(entries["report.json"])
    assert report["status"] == "draft"
    assert report["findings"] == payload["flags"]
    assert report["rejected_findings"] == []
    assert json.loads(entries["reviews.json"]) == []


@pytest.mark.parametrize("state", ["unreviewed", "source_changed", "critic_withdrawn", "empty"])
def test_final_blocks_pending_stale_withdrawn_and_empty_findings(bundle_session, tmp_path, state):
    session, fact, payload = bundle_session
    if state in {"source_changed", "critic_withdrawn"}:
        decide(session)
    if state == "source_changed":
        session.store.put_fact(fact.model_copy(update={"note": "Changed source context."}))
    elif state == "critic_withdrawn":
        payload["flags"][0]["status"] = "draft"
        session.draft_path.write_text(json.dumps(payload), encoding="utf-8")
    elif state == "empty":
        payload["flags"] = []
        session.draft_path.write_text(json.dumps(payload), encoding="utf-8")

    destination = tmp_path / "blocked.zip"
    with pytest.raises(ValueError):
        export_bundle(session, destination, final=True)
    assert not destination.exists()


def test_stale_draft_records_current_source_and_stale_review(bundle_session, tmp_path):
    session, fact, _payload = bundle_session
    decide(session)
    updated = fact.model_copy(update={"note": "Changed source context."})
    session.store.put_fact(updated)

    entries = read_bundle(export_bundle(session, tmp_path / "stale-draft.zip"))

    assert json.loads(entries["facts.json"]) == [updated.model_dump(mode="json")]
    reviews = json.loads(entries["reviews.json"])
    assert reviews[0]["stale"] is True
    assert reviews[0]["fact_snapshots"][fact.id] == fact.model_dump(mode="json")
    assert json.loads(entries["report.json"])["status"] == "draft"
    assert "**DRAFT" in entries["report.md"].decode("utf-8")


@pytest.mark.parametrize("target", ["draft", "database", "draft_hardlink", "database_hardlink"])
def test_cannot_overwrite_input_or_database(bundle_session, tmp_path, target):
    session, _fact, _payload = bundle_session
    source = session.draft_path if target.startswith("draft") else tmp_path / "review.sqlite"
    destination = source
    if target.endswith("hardlink"):
        destination = tmp_path / "alias.zip"
        os.link(source, destination)
    before = source.read_bytes()

    with pytest.raises(ValueError, match="cannot overwrite"):
        export_bundle(session, destination)

    assert source.read_bytes() == before


def test_failed_zip_write_preserves_previous_output(bundle_session, tmp_path, monkeypatch):
    session, _fact, _payload = bundle_session
    destination = tmp_path / "report.zip"
    previous = b"previous export remains available"
    destination.write_bytes(previous)

    def fail_write(*_args, **_kwargs):
        raise OSError("Synthetic ZIP write failure")

    monkeypatch.setattr(zipfile.ZipFile, "writestr", fail_write)
    with pytest.raises(OSError, match="Synthetic ZIP write failure"):
        export_bundle(session, destination)

    assert destination.read_bytes() == previous
