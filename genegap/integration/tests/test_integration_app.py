"""Integration launcher tests distinguish missing production code from test callbacks."""
import json
import asyncio
import sys
from types import ModuleType

from integration.app import main


def test_default_orchestrator_contract_is_available_without_starting_a_run(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GENEGAP_ORCHESTRATOR", raising=False)
    assert main(["--check", "--output-dir", str(tmp_path)]) == 0
    assert "genegap.agents.orchestrator:run_query" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_missing_selected_orchestrator_is_reported_without_a_fallback(tmp_path, capsys):
    status = main(["--orchestrator", "genegap.agents.missing_test_module:run_query", "--check", "--output-dir", str(tmp_path)])
    assert status == 2
    assert "not available" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_compatible_callback_opens_the_review_queue_only_after_a_success(tmp_path, monkeypatch, capsys):
    module = ModuleType("synthetic_ui_test_orchestrator")

    def run_query(query, *, store, run_id, on_event):
        on_event("plan", {"query": query})
        return {"run_id": run_id, "flags": [{"variant_id": "TEST", "claim": "Synthetic software test."}]}

    module.run_query = run_query
    monkeypatch.setitem(sys.modules, module.__name__, module)
    args = ["test query", "--orchestrator", f"{module.__name__}:run_query", "--output-dir", str(tmp_path), "--list"]
    assert main(args) == 0
    captured = capsys.readouterr()
    assert "Awaiting review" in captured.out
    assert "Ready for human review" in captured.out
    assert '"event": "plan"' in captured.err
    directory, = tmp_path.iterdir()
    assert json.loads((directory / "run.json").read_text())["status"] == "ready_for_review"


def test_producer_failure_does_not_open_a_review_queue(tmp_path, monkeypatch, capsys):
    module = ModuleType("broken_ui_test_orchestrator")

    def run_query(query, *, store, run_id, on_event):
        raise ValueError("Synthetic failure.")

    module.run_query = run_query
    monkeypatch.setitem(sys.modules, module.__name__, module)
    assert main(["test", "--orchestrator", f"{module.__name__}:run_query", "--output-dir", str(tmp_path), "--list"]) == 1
    captured = capsys.readouterr()
    assert "No review handoff was published" in captured.err
    assert "Ready for human review" not in captured.out


def test_async_cancellation_exits_cleanly_without_opening_review(tmp_path, monkeypatch, capsys):
    module = ModuleType("cancelled_ui_test_orchestrator")

    async def run_query(query, *, store, run_id, on_event):
        raise asyncio.CancelledError("Synthetic provider cancellation.")

    module.run_query = run_query
    monkeypatch.setitem(sys.modules, module.__name__, module)
    status = main(["test", "--orchestrator", f"{module.__name__}:run_query", "--output-dir", str(tmp_path), "--list"])
    assert status == 130
    assert "Ready for human review" not in capsys.readouterr().out
    directory, = tmp_path.iterdir()
    assert json.loads((directory / "run.json").read_text())["status"] == "interrupted"
    assert not (directory / "draft.json").exists()
