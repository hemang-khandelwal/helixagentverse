"""Test persistent review, changed evidence and the final-export boundary."""
import json
import os
import sqlite3

import pytest

from genegap.schemas import Fact, Flag
from genegap.store import Store
from review import session as session_module
from review.session import ReviewSession


@pytest.fixture
def review_case(tmp_path):
    path = tmp_path / "handoff.json"
    database = tmp_path / "review.sqlite"
    store = Store(database)
    fact = Fact(
        field="synthetic_count", value=17, source="Software-test fixture",
        record_id="TEST", url="https://example.org/test", note="Synthetic evidence, not clinical data.",
    )
    store.put_fact(fact)
    flag = Flag(
        variant_id="TEST-VARIANT", claim="Synthetic finding for software testing.",
        evidence_tier=3, citations=["TEST-CITATION"], fact_ids=[fact.id], status="critic_passed",
    )
    payload = {"run_id": "review-test", "flags": [flag.model_dump(mode="json")]}
    path.write_text(json.dumps(payload))
    yield ReviewSession(path, store), store, path, database, payload, fact
    store.db.close()


def decide(session, action="approve", note="Checked synthetic evidence."):
    view = session.view(0)
    return session.review(0, action, "Test reviewer", note, revision=view.revision)


def test_approval_is_restored_after_reopening_the_original_handoff(review_case):
    session, store, path, _, _, _ = review_case
    assert session.view(0).status == "Awaiting review"
    with pytest.raises(ValueError, match="no audited human decision"):
        session.report(final=True)
    decide(session)
    reopened = ReviewSession(path, store)
    assert reopened.view(0).status == "Approved"
    report = reopened.report(final=True)
    assert "**FINAL" in report
    assert "Test reviewer" in report
    assert reopened.history()[0]["payload"]["flag"]["status"] == "human_approved"


def test_annotations_persist_but_need_a_terminal_decision(review_case):
    session, store, path, _, _, _ = review_case
    decide(session)
    decide(session, "annotate", "Please re-check the wording.")
    reopened = ReviewSession(path, store)
    assert reopened.view(0).status == "Annotated — decision needed"
    assert reopened.view(0).decision["note"] == "Please re-check the wording."
    with pytest.raises(ValueError, match="annotated but still needs"):
        reopened.report(final=True)
    decide(reopened)
    assert "**FINAL" in reopened.report(final=True)


@pytest.mark.parametrize("change", ["claim", "sources", "run", "other_review"])
def test_open_view_cannot_authorize_a_changed_finding_or_source(review_case, change):
    session, store, path, database, payload, fact = review_case
    view = session.view(0)
    if change == "claim":
        payload["flags"][0]["claim"] = "Changed synthetic finding."
        path.write_text(json.dumps(payload))
    elif change == "sources":
        store.put_fact(fact.model_copy(update={"note": "Changed source context."}))
    elif change == "run":
        payload["run_id"] = "another-run"
        path.write_text(json.dumps(payload))
    else:
        second_store = Store(database)
        try:
            decide(ReviewSession(path, second_store), "reject")
        finally:
            second_store.db.close()
    before = len(session.history())
    with pytest.raises(ValueError, match="changed.*Reopen"):
        session.review(0, "approve", "Test reviewer", "", revision=view.revision)
    assert len(session.history()) == before


def test_fresh_view_does_not_synthesize_critic_clearance(review_case):
    session, _, path, _, payload, _ = review_case
    payload["flags"][0]["status"] = "draft"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="critic_passed"):
        decide(session)
    assert session.history() == []
    decide(session, "reject", "Waiting for critic review.")
    assert "No findings approved." in session.report(final=True)


def test_empty_queue_and_changed_sources_cannot_export_final(review_case, tmp_path):
    session, store, path, _, payload, fact = review_case
    decide(session)
    store.put_fact(fact.model_copy(update={"url": "https://example.org/replaced"}))
    assert session.view(0).status == "Review stale — reopen evidence"
    destination = tmp_path / "final.md"
    with pytest.raises(ValueError, match="content changed"):
        session.export(destination, final=True)
    assert not destination.exists()
    payload["flags"] = []
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="at least one"):
        session.export(destination, final=True)
    assert not destination.exists()
    assert "**DRAFT" in session.report()


def test_final_export_does_not_mix_source_versions_while_rendering(review_case, monkeypatch):
    session, _, _, database, _, fact = review_case
    decide(session)
    competing_store = Store(database)
    competing_store.db.execute("PRAGMA busy_timeout=0")
    original_render = session_module.render_report
    blocked = []

    def render_with_competing_writer(*args, **kwargs):
        changed = fact.model_copy(update={"note": "A concurrent source replacement."})
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            competing_store.put_fact(changed)
        blocked.append(True)
        return original_render(*args, **kwargs)

    monkeypatch.setattr(session_module, "render_report", render_with_competing_writer)
    try:
        text = session.report(final=True)
    finally:
        competing_store.db.rollback()
        competing_store.db.close()
    assert blocked == [True]
    assert "**FINAL" in text
    assert "A concurrent source replacement" not in text


def test_review_and_reports_never_overwrite_the_handoff_or_database(review_case):
    session, _, path, database, _, _ = review_case
    original = path.read_bytes()
    for destination in (path, database):
        with pytest.raises(ValueError, match="cannot overwrite"):
            session.export(destination)
    assert path.read_bytes() == original
    assert session.history() == []


@pytest.mark.parametrize("source", ["handoff", "database"])
def test_export_cannot_overwrite_a_hardlink_to_its_inputs(review_case, tmp_path, source):
    session, _, path, database, _, _ = review_case
    original = path if source == "handoff" else database
    before = original.read_bytes()
    alias = tmp_path / "report.md"
    os.link(original, alias)
    with pytest.raises(ValueError, match="cannot overwrite"):
        session.export(alias)
    assert original.read_bytes() == before


def test_duplicate_findings_fail_before_any_review_write(review_case):
    session, store, path, _, payload, _ = review_case
    payload["flags"].append(dict(payload["flags"][0]))
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="unique"):
        session.overview()
    assert store.steps("review-test") == []


def test_final_report_covers_approved_and_rejected_findings(review_case):
    session, _, path, _, payload, _ = review_case
    payload["flags"].append({"variant_id": "REJECTED-TEST", "claim": "Unaccepted synthetic finding."})
    path.write_text(json.dumps(payload))
    decide(session)
    view = session.view(1)
    session.review(1, "reject", "Test reviewer", "Missing support.", revision=view.revision)
    text = session.report(final=True)
    assert "Synthetic finding for software testing." in text
    assert "Unaccepted synthetic finding." not in text
    assert "## Rejected findings" in text
    assert "Missing support." in text


@pytest.mark.parametrize("status", ["draft", "rejected"])
@pytest.mark.parametrize("annotated", [False, True])
def test_upstream_status_withdrawal_does_not_restore_an_old_approval(review_case, status, annotated):
    session, _, path, _, payload, _ = review_case
    decide(session)
    if annotated:
        decide(session, "annotate", "Please confirm the wording.")
    payload["flags"][0]["status"] = status
    path.write_text(json.dumps(payload))
    assert session.view(0).status == "Critic clearance withdrawn — decision needed"
    with pytest.raises(ValueError, match="critic clearance withdrawn"):
        session.report(final=True)
    with pytest.raises(ValueError, match="critic_passed"):
        decide(session)
    decide(session, "reject", "Upstream clearance withdrawn.")
    assert "No findings approved." in session.report(final=True)


@pytest.mark.parametrize("status", ["draft", "rejected"])
def test_annotation_before_approval_cannot_resurrect_withdrawn_clearance(review_case, status):
    session, _, path, _, payload, _ = review_case
    decide(session, "annotate", "Need another source.")
    payload["flags"][0]["status"] = status
    path.write_text(json.dumps(payload))
    assert session.view(0).status == "Critic clearance withdrawn — decision needed"
    with pytest.raises(ValueError, match="critic_passed"):
        decide(session)
