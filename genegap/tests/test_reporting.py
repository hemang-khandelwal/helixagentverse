"""Reports must preserve provenance and enforce the human review gate."""
from datetime import datetime, timezone
import sqlite3

import pytest

from genegap.reporting import render_report
from genegap.schemas import Fact, Flag
from genegap.signoff import SignOffGate
from genegap.store import Store


@pytest.fixture
def sourced_flag():
    store = Store(":memory:")
    fact = Fact(
        field="example_count",
        value={"ac": 17, "an": 12345, "estimate": 0.001377075739165654},
        source="Test source",
        record_id="TEST-RECORD",
        url="https://example.org/record?name=test&version=original",
        retrieved_at=datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc),
        quote="Synthetic example for a software test.",
        note="Not clinical evidence.",
    )
    store.put_fact(fact)
    flag = Flag(
        variant_id="TEST-VARIANT",
        claim="Synthetic finding for review.",
        evidence_tier=3,
        citations=["TEST-CITATION"],
        fact_ids=[fact.id],
        robust_across_mappings=None,
        status="critic_passed",
    )
    yield SignOffGate(store, "test-report"), flag, fact
    store.db.close()


def test_draft_contains_exact_source_values_and_provenance(sourced_flag):
    gate, flag, fact = sourced_flag
    text = render_report([flag], gate, limitations=["Synthetic test data only."])
    assert "**DRAFT" in text
    assert '"ac": 17' in text
    assert '"an": 12345' in text
    assert "0.001377075739165654" in text
    assert fact.id in text
    assert "TEST-RECORD" in text
    assert "2026-01-02T03:04:00+00:00" in text
    assert "[Open source]" in text
    assert fact.quote in text
    assert fact.note in text
    assert "Mapping robustness: Not assessed." in text
    assert "Synthetic test data only." in text


def test_unreviewed_and_forged_status_cannot_export_final(sourced_flag):
    gate, flag, _ = sourced_flag
    with pytest.raises(ValueError):
        render_report([flag], gate, final=True)
    with pytest.raises(ValueError):
        render_report([flag.model_copy(update={"status": "human_approved"})], gate, final=True)


def test_final_contains_approved_findings_and_review_audit(sourced_flag):
    gate, flag, _ = sourced_flag
    reviewed = gate.review(flag, "approve", "Example reviewer", "Checked synthetic source.")
    text = render_report([reviewed], gate, final=True)
    assert "**FINAL" in text
    assert flag.claim in text
    assert "Example reviewer" in text
    assert "Checked synthetic source." in text
    assert "Recorded decision: approve" in text


def test_rejected_claim_not_in_approved_findings(sourced_flag):
    gate, flag, _ = sourced_flag
    reviewed = gate.review(flag, "reject", "Example reviewer", "Insufficient support.")
    text = render_report([reviewed], gate, final=True)
    assert "No findings approved." in text
    assert "## Rejected findings" in text
    assert flag.claim not in text
    assert "Insufficient support." in text


def test_mixed_final_keeps_rejection_separate(sourced_flag):
    gate, flag, _ = sourced_flag
    second = flag.model_copy(update={"variant_id": "OTHER-TEST", "claim": "Rejected test claim."})
    approved = gate.review(flag, "approve", "Example reviewer")
    rejected = gate.review(second, "reject", "Example reviewer", "Not accepted.")
    text = render_report([approved, rejected], gate, final=True)
    approved_section, rejected_section = text.split("## Rejected findings")
    assert flag.claim in approved_section
    assert second.claim not in text
    assert second.variant_id not in approved_section
    assert second.variant_id in rejected_section


def test_changed_evidence_requires_review_again(sourced_flag):
    gate, flag, _ = sourced_flag
    reviewed = gate.review(flag, "approve", "Example reviewer")
    changed = reviewed.model_copy(update={"evidence_tier": 1})
    with pytest.raises(ValueError):
        render_report([changed], gate, final=True)
    draft = render_report([changed], gate)
    assert "content changed since the recorded review" in draft
    assert "Example reviewer" not in draft


def test_missing_facts_and_ungraded_evidence_are_explicit(sourced_flag):
    gate, _, _ = sourced_flag
    flag = Flag(variant_id="TEST", claim="Incomplete test finding.", fact_ids=["F-missing"])
    text = render_report([flag], gate)
    assert "Evidence tier: Not graded." in text
    assert "Citations: None supplied." in text
    assert "Source fact unavailable: F-missing." in text


def test_external_text_and_unsafe_urls_are_literal(sourced_flag):
    gate, flag, _ = sourced_flag
    fact = Fact(
        field="external_text",
        value="A string containing ``` and <script>alert('test')</script>",
        source="<script>external()</script>",
        record_id="TEST",
        url="javascript:alert('test')",
        quote="[Click](javascript:alert('test'))",
    )
    gate.store.put_fact(fact)
    flag = flag.model_copy(update={
        "claim": "**untrusted**\n# Fake heading <script>test()</script>",
        "fact_ids": [fact.id],
        "citations": ["[Click](javascript:alert('test'))"],
    })
    text = render_report([flag], gate, title="<script>title()</script>")
    assert "\\*\\*untrusted\\*\\*" in text
    assert "\\# Fake heading &lt;script&gt;" in text
    assert "<script>title" not in text
    assert "<script>external" not in text
    assert "[Open source]" not in text
    assert "\\[Click\\]" in text
    assert "````json" in text


def test_final_validation_and_source_rendering_use_one_database_snapshot(tmp_path):
    path = tmp_path / "snapshot.sqlite"
    store = Store(path)
    competing = Store(path)
    competing.db.execute("PRAGMA busy_timeout=0")
    try:
        fact = Fact(
            field="test_count", value=17, source="Synthetic software test", record_id="TEST",
            note="Original reviewed context.",
        )
        store.put_fact(fact)
        gate = SignOffGate(store, "snapshot-test")
        approved = gate.review(Flag(
            variant_id="TEST", claim="Synthetic finding.", evidence_tier=3,
            citations=["TEST"], fact_ids=[fact.id], status="critic_passed",
        ), reviewer="Software-test reviewer")
        validate = gate.require_finalizable
        attempted = []

        def interleaved_validation(flags):
            validate(flags)
            attempted.append(True)
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                competing.put_fact(fact.model_copy(update={"note": "Unreviewed replacement context."}))
            competing.db.rollback()

        gate.require_finalizable = interleaved_validation
        text = render_report([approved], gate, final=True)
        assert attempted == [True]
        assert "**FINAL" in text
        assert "Original reviewed context." in text
        assert "Unreviewed replacement context." not in text
        assert not gate.decision_for(approved)["stale"]
        assert not store.db.in_transaction
    finally:
        competing.db.close()
        store.db.close()


def test_final_renderer_does_not_commit_an_existing_caller_transaction(sourced_flag):
    gate, flag, _fact = sourced_flag
    approved = gate.review(flag, reviewer="Software-test reviewer")
    gate.store.db.execute("BEGIN IMMEDIATE")
    gate.store.db.execute(
        "INSERT INTO steps (ts,run_id,agent,kind,payload) VALUES (0,?,?,?,?)",
        ("caller-work", "test", "uncommitted", "{}"),
    )
    assert "**FINAL" in render_report([approved], gate, final=True)
    assert gate.store.db.in_transaction
    gate.store.db.rollback()
    assert gate.store.steps("caller-work") == []
