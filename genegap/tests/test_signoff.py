"""Human review must be explicit, persisted, and bound to the reviewed content."""
from datetime import datetime, timedelta

import pytest

from genegap.schemas import Fact, Flag
from genegap.signoff import SignOffGate
from genegap.store import Store


@pytest.fixture
def reviewed_case():
    store = Store(":memory:")
    fact = Fact(field="example", value=5, source="test fixture", record_id="record-1")
    store.put_fact(fact)
    flag = Flag(
        variant_id="10-1-A-G",
        claim="Research finding requiring review.",
        evidence_tier=3,
        citations=["PMID:test-fixture"],
        fact_ids=[fact.id],
        robust_across_mappings=True,
        status="critic_passed",
    )
    return store, SignOffGate(store, "run-1"), flag


def test_approval_is_explicit_and_records_the_reviewed_snapshot(reviewed_case):
    store, gate, flag = reviewed_case
    with pytest.raises(ValueError, match="no audited human decision"):
        gate.require_finalizable([flag])
    approved = gate.review(flag, reviewer="Research reviewer", note="Checked the cited evidence.")
    assert flag.status == "critic_passed"
    assert approved.status == "human_approved"
    gate.require_finalizable([approved])
    decision = gate.decision_for(approved)
    assert decision["decision"] == "approve"
    assert decision["reviewer"] == "Research reviewer"
    assert decision["note"] == "Checked the cited evidence."
    assert decision["flag"] == approved.model_dump(mode="json")
    assert datetime.fromisoformat(decision["reviewed_at"]).tzinfo is not None
    assert len(decision["fingerprint"]) == 64
    assert decision["fact_snapshots"][flag.fact_ids[0]] == store.get_fact(flag.fact_ids[0]).model_dump(mode="json")
    assert len(decision["fact_fingerprint"]) == 64
    assert not decision["stale"]
    assert len(store.steps("run-1")) == 1


def test_decisions_survive_reopening_the_database_and_are_scoped_to_a_run(tmp_path):
    path = tmp_path / "review.sqlite"
    first = Store(path)
    flag = Flag(variant_id="v", claim="Rejected draft")
    rejected = SignOffGate(first, "run-1").review(flag, "reject", "Reviewer")
    first.db.close()
    second = Store(path)
    SignOffGate(second, "run-1").require_finalizable([rejected])
    assert SignOffGate(second, "run-2").decision_for(rejected) is None
    second.db.close()


@pytest.mark.parametrize("status", ["draft", "human_approved", "rejected"])
def test_status_alone_cannot_grant_human_approval(reviewed_case, status):
    store, gate, flag = reviewed_case
    flag.status = status
    with pytest.raises(ValueError, match="critic_passed"):
        gate.review(flag, reviewer="Reviewer")
    assert store.steps("run-1") == []
    with pytest.raises(ValueError, match="no audited human decision"):
        gate.require_finalizable([flag])


@pytest.mark.parametrize("change", [
    {"evidence_tier": None},
    {"citations": []},
    {"citations": [" "]},
    {"fact_ids": []},
    {"fact_ids": ["F-does-not-exist"]},
])
def test_approval_requires_evidence_and_existing_facts(reviewed_case, change):
    store, gate, flag = reviewed_case
    with pytest.raises(ValueError, match="Approval"):
        gate.review(flag.model_copy(update=change), reviewer="Reviewer")
    assert store.steps("run-1") == []


@pytest.mark.parametrize("change", [
    {"claim": "An edited finding."},
    {"evidence_tier": 4},
    {"citations": ["PMID:different"]},
    {"fact_ids": ["F-new"]},
    {"robust_across_mappings": False},
    {"variant_id": "a-different-variant"},
])
def test_approval_does_not_cover_changed_content(reviewed_case, change):
    _, gate, flag = reviewed_case
    approved = gate.review(flag, reviewer="Reviewer")
    with pytest.raises(ValueError, match="no audited human decision|content changed"):
        gate.require_finalizable([approved.model_copy(update=change)])


def test_mutating_status_does_not_bypass_an_audited_rejection(reviewed_case):
    _, gate, flag = reviewed_case
    rejected = gate.review(flag, "reject", "Reviewer")
    rejected.status = "human_approved"
    with pytest.raises(ValueError, match="status does not match"):
        gate.require_finalizable([rejected])
    with pytest.raises(ValueError, match="critic_passed"):
        gate.review(rejected, reviewer="Reviewer")


def test_latest_decision_overrides_a_previous_approval(reviewed_case):
    _, gate, flag = reviewed_case
    approved = gate.review(flag, reviewer="First reviewer")
    rejected = gate.review(approved, "reject", "Second reviewer", "Evidence requires revision.")
    assert gate.decision_for(flag)["reviewer"] == "Second reviewer"
    assert gate.decision_for(flag)["decision"] == "reject"
    gate.require_finalizable([rejected])
    with pytest.raises(ValueError, match="status does not match"):
        gate.require_finalizable([approved])


def test_annotation_on_a_draft_preserves_status_and_requires_a_final_decision(reviewed_case):
    _, gate, flag = reviewed_case
    flag.status = "draft"
    annotated = gate.review(flag, "annotate", "Reviewer", "Please verify the denominator.")
    assert annotated.status == "draft"
    assert gate.decision_for(annotated)["note"] == "Please verify the denominator."
    with pytest.raises(ValueError, match="annotated but still needs"):
        gate.require_finalizable([annotated])
    rejected = gate.review(annotated, "reject", "Reviewer")
    gate.require_finalizable([rejected])


def test_approval_can_be_renewed_when_the_prior_approval_is_audited(reviewed_case):
    store, gate, flag = reviewed_case
    approved = gate.review(flag, reviewer="First reviewer")
    renewed = gate.review(approved, reviewer="Second reviewer")
    gate.require_finalizable([renewed])
    assert len(store.steps("run-1")) == 2
    assert gate.decision_for(renewed)["reviewer"] == "Second reviewer"


def test_annotation_after_approval_requires_an_explicit_renewed_decision(reviewed_case):
    _, gate, flag = reviewed_case
    approved = gate.review(flag, reviewer="First reviewer")
    annotated = gate.review(approved, "annotate", "Second reviewer", "Please confirm the final wording.")
    with pytest.raises(ValueError, match="annotated but still needs"):
        gate.require_finalizable([annotated])
    renewed = gate.review(annotated, reviewer="First reviewer")
    gate.require_finalizable([renewed])


@pytest.mark.parametrize("tier", [1, 2, 3, 4, 5])
def test_review_does_not_invent_a_tier_threshold_for_human_approval(reviewed_case, tier):
    _, gate, flag = reviewed_case
    approved = gate.review(flag.model_copy(update={"evidence_tier": tier}), reviewer="Reviewer")
    gate.require_finalizable([approved])


def test_missing_fact_is_rechecked_before_final_export(reviewed_case):
    store, gate, flag = reviewed_case
    approved = gate.review(flag, reviewer="Reviewer")
    store.db.execute("DELETE FROM facts WHERE id=?", (flag.fact_ids[0],))
    store.db.commit()
    assert gate.decision_for(approved)["stale"]
    with pytest.raises(ValueError, match="content changed"):
        gate.require_finalizable([approved])


@pytest.mark.parametrize("field,value", [
    ("url", "https://example.org/revised-source"),
    ("quote", "A revised supporting passage."),
    ("note", "A limitation added after review."),
    ("method", "llm_extracted"),
])
def test_source_metadata_changes_invalidate_approval_even_when_fact_id_is_unchanged(reviewed_case, field, value):
    store, gate, flag = reviewed_case
    approved = gate.review(flag, reviewer="Reviewer")
    old_fact = store.get_fact(flag.fact_ids[0])
    revised = old_fact.model_copy(update={field: value})
    assert revised.id == old_fact.id
    store.put_fact(revised)
    assert gate.decision_for(approved)["stale"]
    with pytest.raises(ValueError, match="content changed"):
        gate.require_finalizable([approved])
    with pytest.raises(ValueError, match="critic_passed"):
        gate.review(approved, reviewer="Reviewer")


def test_retrieval_timestamp_is_part_of_the_reviewed_source_snapshot(reviewed_case):
    store, gate, flag = reviewed_case
    approved = gate.review(flag, reviewer="Reviewer")
    fact = store.get_fact(flag.fact_ids[0])
    store.put_fact(fact.model_copy(update={"retrieved_at": fact.retrieved_at + timedelta(seconds=1)}))
    assert gate.decision_for(approved)["stale"]
    with pytest.raises(ValueError, match="content changed"):
        gate.require_finalizable([approved])


def test_findings_for_the_same_variant_have_independent_review_decisions(reviewed_case):
    _, gate, flag = reviewed_case
    approved = gate.review(flag, reviewer="Reviewer")
    another = flag.model_copy(update={"claim": "Another finding about the same variant."})
    assert gate.decision_for(another) is None
    with pytest.raises(ValueError, match="no audited human decision"):
        gate.require_finalizable([approved, another])


def test_duplicate_identities_cannot_reuse_a_review_for_conflicting_content(reviewed_case):
    _, gate, flag = reviewed_case
    approved = gate.review(flag, reviewer="Reviewer")
    conflict = approved.model_copy(update={"evidence_tier": 5})
    with pytest.raises(ValueError, match="duplicate finding identity"):
        gate.require_finalizable([approved, conflict])


@pytest.mark.parametrize("kwargs", [
    {"reviewer": ""},
    {"reviewer": "   "},
    {"reviewer": None},
    {"reviewer": "Reviewer", "decision": "automatic_approve"},
    {"reviewer": "Reviewer", "decision": "annotate", "note": " "},
    {"reviewer": "Reviewer", "note": None},
])
def test_invalid_review_inputs_write_no_audit(reviewed_case, kwargs):
    store, gate, flag = reviewed_case
    with pytest.raises(ValueError):
        gate.review(flag, **kwargs)
    assert store.steps("run-1") == []


def test_blank_run_identity_is_rejected():
    with pytest.raises(ValueError, match="run_id"):
        SignOffGate(Store(":memory:"), " ")


def test_no_findings_cannot_be_finalized():
    gate = SignOffGate(Store(":memory:"), "run-1")
    with pytest.raises(ValueError, match="at least one analyzed finding"):
        gate.require_finalizable([])


def test_all_rejected_findings_are_finalizable():
    gate = SignOffGate(Store(":memory:"), "run-1")
    rejected = gate.review(Flag(variant_id="v", claim="Unsupported draft"), "reject", "Reviewer")
    gate.require_finalizable([rejected])


@pytest.mark.parametrize("status", ["draft", "rejected"])
@pytest.mark.parametrize("annotated", [False, True])
def test_withdrawn_critic_clearance_cannot_restore_a_previous_human_approval(reviewed_case, status, annotated):
    _, gate, original = reviewed_case
    approved = gate.review(original, reviewer="Reviewer")
    if annotated:
        gate.review(approved, "annotate", "Reviewer", "Please confirm the wording.")
    withdrawn = original.model_copy(update={"status": status})
    decision = gate.decision_for(withdrawn)
    assert decision["stale"]
    assert decision["critic_clearance_withdrawn"]
    with pytest.raises(ValueError, match="critic clearance withdrawn"):
        gate.require_finalizable([withdrawn])
    with pytest.raises(ValueError, match="critic_passed"):
        gate.review(withdrawn, reviewer="Reviewer")
    # An explicit human rejection remains possible when the critic withdraws.
    rejected = gate.review(withdrawn, "reject", "Reviewer", "Clearance was withdrawn.")
    gate.require_finalizable([rejected])


@pytest.mark.parametrize("status", ["draft", "rejected"])
def test_annotation_cannot_restore_withdrawn_clearance_before_any_human_approval(reviewed_case, status):
    _, gate, original = reviewed_case
    gate.review(original, "annotate", "Reviewer", "Need another source.")
    decision = gate.decision_for(original.model_copy(update={"status": status}))
    assert decision["critic_clearance_withdrawn"]
    assert decision["stale"]
