"""Check actual orchestrator entry points and mode/review boundaries."""
import pytest
import requests

from genegap.agents import orchestrator
from genegap.agents.planning import AnalysisPlan
from genegap.store import Store


def test_offline_entrypoint_runs_actual_recorded_pipeline_without_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Recorded orchestrator attempted network access.")

    monkeypatch.setattr(requests.Session, "request", forbidden)
    store = Store(":memory:")
    events = []
    try:
        result = orchestrator.run_offline_query("clopidogrel", store=store, run_id="offline-test", on_event=lambda *event: events.append(event))
        assert result["run_id"] == "offline-test"
        assert result["flags"]
        assert all(flag["status"] == "draft" and flag["evidence_tier"] is None for flag in result["flags"])
        assert any(kind == "completed" for kind, _payload in events)
        assert any("offline" in str(payload).lower() for _kind, payload in events)
        assert not any(step["agent"] == "human_review" for step in store.steps("offline-test"))
    finally:
        store.db.close()


def test_live_entrypoint_routes_the_actual_planner_result_without_fallback(monkeypatch):
    calls = []

    def plan(query):
        calls.append(("planner", query))
        return AnalysisPlan(action="assess", drug="clopidogrel", genes=["CYP2C19"], population_id="us_acs_antiplatelet")

    def analyze(plan, *, store, run_id, on_event, offline):
        calls.append(("analysis", plan.drug, offline))
        return {"run_id": run_id, "title": "Synthetic routing test", "flags": [], "limitations": []}

    monkeypatch.setattr(orchestrator.planning, "gemini_plan", plan)
    monkeypatch.setattr(orchestrator.analysis, "execute_plan", analyze)
    store = Store(":memory:")
    try:
        result = orchestrator.run_query("original input", store=store, run_id="live-test", on_event=lambda *_: None)
        assert calls == [("planner", "original input"), ("analysis", "clopidogrel", False)]
        assert result["run_id"] == "live-test"
    finally:
        store.db.close()


def test_live_planning_error_is_not_silently_replaced_with_offline_data(monkeypatch):
    def fail(query):
        raise ValueError("Synthetic model configuration failure.")

    monkeypatch.setattr(orchestrator.planning, "gemini_plan", fail)
    store = Store(":memory:")
    try:
        with pytest.raises(ValueError, match="configuration failure"):
            orchestrator.run_query("clopidogrel", store=store, run_id="test", on_event=lambda *_: None)
        assert not store.db.execute("SELECT 1 FROM facts").fetchone()
    finally:
        store.db.close()


def test_event_budget_stops_an_unbounded_stage(monkeypatch):
    monkeypatch.setattr(orchestrator.planning, "offline_plan", lambda _: AnalysisPlan(action="clarify"))

    def unbounded(plan, *, on_event, **kwargs):
        for _ in range(orchestrator.MAX_EVENTS + 1):
            on_event("status", {"stage": "synthetic loop"})

    monkeypatch.setattr(orchestrator.analysis, "execute_plan", unbounded)
    store = Store(":memory:")
    try:
        with pytest.raises(ValueError, match="step limit"):
            orchestrator.run_offline_query("test", store=store, run_id="test", on_event=lambda *_: None)
        assert len(store.steps("test")) == orchestrator.MAX_EVENTS
    finally:
        store.db.close()
