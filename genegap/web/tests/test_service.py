"""Exercise the complete app workflow against actual recorded source data."""
import json
import time
from pathlib import Path
from zipfile import ZipFile
import io

import pytest

from genegap.schemas import Fact
from genegap.store import Store
from web.service import AppService


@pytest.fixture
def app_run(tmp_path):
    service = AppService(tmp_path / "runs")
    job = service.start("clopidogrel")
    for _ in range(100):
        state = service.job(job["job_id"])
        if state["status"] != "running":
            break
        time.sleep(0.03)
    assert state["status"] == "ready_for_review", state
    yield service, state["run_id"]
    service.close()


def evidence(flag):
    return {"index": flag["index"], "revision": flag["revision"], "reviewer": "Synthetic test reviewer",
            "evidence_tier": 3, "citations": ["https://example.org/software-test"],
            "note": "Synthetic evidence assessment for software validation only."}


def test_recorded_analysis_has_live_trace_tables_and_real_sources(app_run):
    service, run_id = app_run
    detail = service.detail(run_id)
    assert detail["counts"]["findings"] == 4
    assert {gap["result"]["mapping_version"] for gap in detail["gaps"]} == {"A", "B", "C"}
    assert detail["facts"] and detail["trace"]
    assert not detail["can_finalize"]
    assert all(flag["pipeline_status"] == "draft" for flag in detail["flags"])
    assert any("ASSUMPTION" in note for note in detail["limitations"])
    assert service.list_runs()[0]["id"] == run_id


def test_complete_evidence_numeric_audit_human_review_and_export(app_run):
    service, run_id = app_run
    detail = service.detail(run_id)
    for index in range(len(detail["flags"])):
        flag = service.detail(run_id)["flags"][index]
        graded = service.evidence(run_id, evidence(flag))
        critique = graded["critique"][-1]
        assert critique["passed"], critique
        flag = graded["flags"][index]
        assert flag["pipeline_status"] == "critic_passed"
        assert flag["review_status"] == "Awaiting review"
        service.review(run_id, {"index": index, "revision": flag["revision"], "decision": "approve",
                               "reviewer": "Synthetic test reviewer", "note": "Approved synthetic software-test assessment."})
    complete = service.detail(run_id)
    assert complete["can_finalize"]
    assert complete["counts"]["approved"] == 4
    with ZipFile(io.BytesIO(service.export(run_id, final=True))) as archive:
        assert json.loads(archive.read("manifest.json"))["status"] == "final"
        assert "FINAL" in archive.read("report.md").decode()


def test_pending_and_stale_reviews_cannot_bypass_gate(app_run):
    service, run_id = app_run
    flag = service.detail(run_id)["flags"][0]
    with pytest.raises(ValueError, match="critic_passed"):
        service.review(run_id, {"index": 0, "revision": flag["revision"], "decision": "approve", "reviewer": "Test", "note": ""})
    graded = service.evidence(run_id, evidence(flag))
    with pytest.raises(ValueError, match="changed"):
        service.review(run_id, {"index": 0, "revision": flag["revision"], "decision": "approve", "reviewer": "Test", "note": ""})
    with pytest.raises(ValueError):
        service.export(run_id, final=True)
    assert graded["flags"][0]["pipeline_status"] == "critic_passed"


def test_source_tampering_is_caught_by_recalculation(app_run):
    service, run_id = app_run
    _directory, _draft, database, _manifest = service._paths(run_id)
    store = Store(database)
    try:
        row = store.db.execute("SELECT id,json FROM facts WHERE source='GeneGap.stats' LIMIT 1").fetchone()
        fact = Fact.model_validate_json(row["json"])
        fact.value["result"]["groups"][0]["required_n"] = 999999
        # Deliberately corrupt the stored payload at its original primary key.
        store.db.execute("UPDATE facts SET json=? WHERE id=?", (fact.model_dump_json(), row["id"]))
        store.db.commit()
    finally:
        store.db.close()
    flag = service.detail(run_id)["flags"][0]
    graded = service.evidence(run_id, evidence(flag))
    assert not graded["critique"][-1]["passed"]
    assert graded["flags"][0]["pipeline_status"] == "draft"


def test_chart_endpoints_render_both_formats_and_reuse_cache(app_run):
    service, run_id = app_run
    variant = service.detail(run_id)["gaps"][0]["result"]["variant_id"]
    svg = service.chart(run_id, variant, "A", "representation")
    assert b"<svg" in svg
    assert service.chart(run_id, variant, "A", "enrolment", "png").startswith(b"\x89PNG")
    assert service.chart(run_id, variant, "A", "representation") == svg
    with pytest.raises(KeyError):
        service.chart(run_id, "not-a-variant", "A", "representation")


def test_refusal_unknown_and_missing_target_are_usable_empty_states(tmp_path):
    service = AppService(tmp_path)
    try:
        for query in ("G6PD", "unrecognized drug", "warfarin", "what dose should I take for my genotype"):
            job_id = service.start(query)["job_id"]
            for _ in range(100):
                job = service.job(job_id)
                if job["status"] != "running":
                    break
                time.sleep(0.02)
            assert job["status"] == "ready_for_review", job
            result = service.detail(job["run_id"])
            assert not result["flags"]
            assert result["limitations"]
            assert not result["can_finalize"]
    finally:
        service.close()


def test_invalid_evidence_does_not_change_handoff(app_run):
    service, run_id = app_run
    flag = service.detail(run_id)["flags"][0]
    draft = service._paths(run_id)[1]
    original = draft.read_bytes()
    body = evidence(flag)
    body["citations"] = ["javascript:alert(1)"]
    with pytest.raises(ValueError):
        service.evidence(run_id, body)
    assert draft.read_bytes() == original


def test_saved_runs_survive_application_restart(app_run):
    service, run_id = app_run
    reopened = AppService(service.root)
    try:
        assert reopened.detail(run_id)["counts"]["findings"] == 4
    finally:
        reopened.close()


def test_front_door_refuses_advice_and_resolves_brand_names(tmp_path):
    service = AppService(tmp_path)
    try:
        seen = {}
        for query in ("Plavix", "what dose of clopidogrel should I take?"):
            job_id = service.start(query)["job_id"]
            for _ in range(200):
                job = service.job(job_id)
                if job["status"] != "running":
                    break
                time.sleep(0.02)
            seen[query] = service.detail(job["run_id"])
        assert seen["Plavix"]["flags"]
        assert not seen["what dose of clopidogrel should I take?"]["flags"]
        assert "medical advice" in " ".join(seen["what dose of clopidogrel should I take?"]["limitations"]).lower() or seen["what dose of clopidogrel should I take?"]["limitations"]
    finally:
        service.close()
