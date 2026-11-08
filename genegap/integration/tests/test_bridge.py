"""Contract tests use synthetic callbacks; no production agent is simulated."""
import asyncio
import json

import pytest

from genegap.schemas import Fact, Flag
from genegap.store import Store
from integration.bridge import RunFailed, open_review, run_pipeline
from review.session import ReviewDraft


def synthetic_callback(query, *, store, run_id, on_event):
    """A software-test producer, not clinical evidence or a live orchestrator."""
    on_event("plan", {"stage": "synthetic software test", "query": query})
    fact = Fact(field="test_count", value=17, source="Synthetic software test", record_id="TEST")
    store.put_fact(fact)
    return {
        "run_id": run_id, "title": "Synthetic integration test",
        "flags": [Flag(
            variant_id="TEST", claim="Synthetic software-test finding.",
            evidence_tier=3, citations=["TEST-CITATION"], fact_ids=[fact.id],
            status="critic_passed",
        ).model_dump(mode="json")],
    }


def test_callback_to_same_database_to_human_review_and_export(tmp_path):
    events = []
    run = run_pipeline(synthetic_callback, "test query", output_root=tmp_path, on_event=lambda *event: events.append(event))
    manifest = json.loads((run.directory / "run.json").read_text())
    assert manifest["status"] == "ready_for_review"
    assert manifest["run_id"] == run.run_id
    assert manifest["draft"] == str(run.draft_path)
    assert manifest["producer"].endswith(":synthetic_callback")
    assert [kind for kind, _payload in events] == ["started", "plan", "ready_for_review"]
    recorded = [json.loads(line) for line in (run.directory / "events.jsonl").read_text().splitlines()]
    assert [event["kind"] for event in recorded] == [kind for kind, _payload in events]
    store = Store(run.db_path)
    try:
        session = open_review(run, store)
        view = session.view(0)
        assert view.status == "Awaiting review"
        assert view.facts[0].value == 17
        with pytest.raises(ValueError, match="no audited human decision"):
            session.report(final=True)
        session.review(0, "approve", "Software-test reviewer", "Reviewed synthetic fixture.", revision=view.revision)
        assert "Software-test reviewer" in session.report(final=True)
    finally:
        store.db.close()


@pytest.mark.parametrize("problem", [
    "wrong_run", "missing_fact", "auto_approval", "missing_critic_evidence",
    "duplicate_findings", "invalid_nested_model", "human_event", "wrong_type",
])
def test_invalid_or_unauditable_output_never_publishes_a_handoff(tmp_path, problem):
    def producer(query, *, store, run_id, on_event):
        result = synthetic_callback(query, store=store, run_id=run_id, on_event=on_event)
        if problem == "wrong_run":
            result["run_id"] = "another-run"
        elif problem == "missing_fact":
            result["flags"][0]["fact_ids"] = ["F-not-in-the-supplied-store"]
        elif problem == "auto_approval":
            result["flags"][0]["status"] = "human_approved"
        elif problem == "missing_critic_evidence":
            result["flags"][0]["citations"] = []
        elif problem == "duplicate_findings":
            result["flags"].append(dict(result["flags"][0]))
        elif problem == "invalid_nested_model":
            flag = Flag.model_validate(result["flags"][0])
            flag.evidence_tier = 99
            result["flags"] = [flag]
        elif problem == "human_event":
            store.log_step(run_id, "human_review", "signoff", {"decision": "approve"})
        else:
            return "a narrative instead of a structured result"
        return result

    with pytest.raises(RunFailed) as caught:
        run_pipeline(producer, "test query", output_root=tmp_path)
    directory = caught.value.directory
    manifest = json.loads((directory / "run.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["validation_summary"]
    assert manifest["draft"] is None
    assert not (directory / "draft.json").exists()
    assert caught.value.cause is caught.value.__cause__
    assert (directory / "events.jsonl").exists()


def test_failed_new_run_never_opens_or_removes_a_prior_success(tmp_path):
    first = run_pipeline(synthetic_callback, "test query", output_root=tmp_path)
    original = first.draft_path.read_bytes()

    def broken(query, *, store, run_id, on_event):
        on_event("warning", {"stage": "synthetic failure"})
        raise RuntimeError("Synthetic provider failure.")

    with pytest.raises(RunFailed) as caught:
        run_pipeline(broken, "test query", output_root=tmp_path)
    assert caught.value.run_id != first.run_id
    assert first.draft_path.read_bytes() == original
    assert not (caught.value.directory / "draft.json").exists()


def test_progress_events_cannot_promote_flags_to_critic_or_human_approved(tmp_path):
    def producer(query, *, store, run_id, on_event):
        on_event("signoff", {"status": "human_approved", "decision": "approve"})
        return ReviewDraft(run_id=run_id, flags=[Flag(variant_id="TEST", claim="Still a draft.")])

    run = run_pipeline(producer, "test query", output_root=tmp_path)
    store = Store(run.db_path)
    try:
        session = open_review(run, store)
        assert session.view(0).source_flag.status == "draft"
        assert session.history() == []
        with pytest.raises(ValueError):
            session.report(final=True)
    finally:
        store.db.close()


def test_async_provider_is_awaited_before_publishing(tmp_path):
    async def producer(query, *, store, run_id, on_event):
        await asyncio.sleep(0)
        return synthetic_callback(query, store=store, run_id=run_id, on_event=on_event)

    run = run_pipeline(producer, "test query", output_root=tmp_path)
    assert run.draft_path.exists()


def test_review_refuses_an_unrelated_database_or_replaced_run(tmp_path):
    run = run_pipeline(synthetic_callback, "test query", output_root=tmp_path)
    other = Store(tmp_path / "other.sqlite")
    try:
        with pytest.raises(ValueError, match="original database"):
            open_review(run, other)
    finally:
        other.db.close()
    payload = json.loads(run.draft_path.read_text())
    payload["run_id"] = "changed-run"
    run.draft_path.write_text(json.dumps(payload))
    store = Store(run.db_path)
    try:
        with pytest.raises(ValueError, match="no longer matches"):
            open_review(run, store)
    finally:
        store.db.close()


def test_invalid_query_does_not_call_provider_or_create_a_run(tmp_path):
    calls = []

    def producer(query, *, store, run_id, on_event):
        calls.append(query)

    with pytest.raises(ValueError, match="nonblank"):
        run_pipeline(producer, " ", output_root=tmp_path)
    assert calls == []
    assert not list(tmp_path.iterdir())


def test_interrupt_retains_a_failed_trace_without_a_review_handoff(tmp_path):
    def producer(query, *, store, run_id, on_event):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_pipeline(producer, "test query", output_root=tmp_path)
    directory, = tmp_path.iterdir()
    assert json.loads((directory / "run.json").read_text())["status"] == "interrupted"
    assert not (directory / "draft.json").exists()


@pytest.mark.parametrize("exit_code", [0, 2])
def test_exiting_provider_cannot_leave_a_run_marked_as_running(tmp_path, exit_code):
    def producer(query, *, store, run_id, on_event):
        raise SystemExit(exit_code)

    with pytest.raises(RunFailed) as caught:
        run_pipeline(producer, "test query", output_root=tmp_path)
    manifest = json.loads((caught.value.directory / "run.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["error_type"] == "SystemExit"
    assert not (caught.value.directory / "draft.json").exists()


def test_async_cancellation_persists_terminal_state_then_propagates(tmp_path):
    cancellation = asyncio.CancelledError("Synthetic cancellation.")

    async def producer(query, *, store, run_id, on_event):
        on_event("plan", {"stage": "synthetic cancellation test"})
        raise cancellation

    with pytest.raises(asyncio.CancelledError):
        run_pipeline(producer, "test query", output_root=tmp_path)
    directory, = tmp_path.iterdir()
    manifest = json.loads((directory / "run.json").read_text())
    assert manifest["status"] == "interrupted"
    assert manifest["finished_at"] is not None
    assert manifest["error_type"] == "CancelledError"
    assert not (directory / "draft.json").exists()
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    assert events[-1]["kind"] == "interrupted"
    store = Store(manifest["database"])
    try:
        assert store.steps(manifest["run_id"])[-1]["kind"] == "interrupted"
    finally:
        store.db.close()


def test_progress_never_commits_unfinished_producer_work_on_failure(tmp_path):
    database = tmp_path / "shared.sqlite"
    initial = Store(database)
    initial.cache_put("existing", "test://existing", 200, "{}")
    initial.db.close()
    original_failure = ValueError("Synthetic producer failure before commit.")

    def producer(query, *, store, run_id, on_event):
        store.db.execute("BEGIN")
        store.db.execute("INSERT INTO http_cache VALUES (?,?,?,?,?)", ("must_rollback", "test://pending", 200, "{}", 0))
        on_event("progress", {"stage": "pending batch"})
        assert store.db.in_transaction
        raise original_failure

    with pytest.raises(RunFailed) as caught:
        run_pipeline(producer, "test query", output_root=tmp_path / "runs", db_path=database)
    assert caught.value.cause is original_failure
    store = Store(database)
    try:
        assert store.cache_get("must_rollback") is None
        assert store.cache_get("existing") is not None
        assert [step["kind"] for step in store.steps(caught.value.run_id)] == ["started", "progress", "failed"]
    finally:
        store.db.close()


def test_queued_progress_is_saved_after_a_producer_commits(tmp_path):
    def producer(query, *, store, run_id, on_event):
        store.db.execute("BEGIN")
        store.db.execute("INSERT INTO http_cache VALUES (?,?,?,?,?)", ("committed", "test://batch", 200, "{}", 0))
        on_event("progress", {"stage": "before commit"})
        assert store.db.in_transaction
        store.db.commit()
        return {"run_id": run_id, "flags": []}

    run = run_pipeline(producer, "test query", output_root=tmp_path)
    store = Store(run.db_path)
    try:
        assert store.cache_get("committed") is not None
        assert [step["kind"] for step in store.steps(run.run_id)] == ["started", "progress", "ready_for_review"]
    finally:
        store.db.close()


def test_completion_observer_failure_has_a_failed_terminal_trace(tmp_path):
    original_failure = ValueError("Synthetic observer failure.")
    observed = []

    def observer(kind, payload):
        observed.append(kind)
        if kind == "ready_for_review":
            raise original_failure

    with pytest.raises(RunFailed) as caught:
        run_pipeline(synthetic_callback, "test query", output_root=tmp_path, on_event=observer)
    assert caught.value.cause is original_failure
    directory = caught.value.directory
    manifest = json.loads((directory / "run.json").read_text())
    assert manifest["status"] == "failed"
    assert not (directory / "draft.json").exists()
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    assert events[-1]["kind"] == "failed"
    assert observed[-1] == "ready_for_review"  # The failing observer is not called during cleanup.
    store = Store(manifest["database"])
    try:
        assert store.steps(caught.value.run_id)[-1]["kind"] == "failed"
    finally:
        store.db.close()
