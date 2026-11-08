"""Tests for tasks 3.1 (tools) and 3.2 (agent loop). No internet, no Gemini."""
import pytest

from genegap import fixtures
from genegap.agents.base import Agent, LLMTurn, ToolCall
from genegap.agents.tools import Param, Tool, ToolError, Workspace, build_tools
from genegap.store import Store


class FakeChat:
    """Plays back a script of model turns and records what it was sent."""
    def __init__(self, script):
        self.script, self.sent = list(script), []

    def send(self, text):
        self.sent.append(("user", text))
        return self.script.pop(0)

    def send_tool_results(self, results):
        self.sent.append(("tools", results))
        return self.script.pop(0)


def agent_with(script, tools, store=None, max_steps=8):
    chat = FakeChat(script)
    return Agent("t", "do the job", tools, lambda system, ts: chat, store=store, max_steps=max_steps), chat


ECHO = Tool("echo", "Echo a word", [Param("word", "string", "a word")], lambda word: {"said": word})
BOOM = Tool("boom", "Always fails", [], lambda: 1 / 0)


# ---------- the loop ----------
def test_plain_answer_needs_no_tools():
    a, _ = agent_with([LLMTurn(text="hello")], [ECHO])
    r = a.run("hi")
    assert r.finished and r.text == "hello" and r.steps == 0 and r.tool_calls == []


def test_tool_is_run_and_result_handed_back():
    a, chat = agent_with([LLMTurn(tool_calls=[ToolCall("echo", {"word": "cat"})]), LLMTurn(text="done")], [ECHO])
    r = a.run("go")
    assert r.finished and r.text == "done" and r.steps == 1
    assert r.tool_calls[0]["result"] == {"said": "cat"}
    assert chat.sent[1][1][0][1] == {"said": "cat"}


def test_bad_tool_calls_become_errors_for_the_model_not_crashes():
    script = [LLMTurn(tool_calls=[ToolCall("nope", {}), ToolCall("echo", {}), ToolCall("echo", {"word": "a", "x": 1}),
                                  ToolCall("boom", {})]), LLMTurn(text="ok")]
    a, _ = agent_with(script, [ECHO, BOOM])
    r = a.run("go")
    errs = [c["result"].get("error", "") for c in r.tool_calls]
    assert "no tool called" in errs[0] and "needs these inputs" in errs[1] and "does not take" in errs[2]
    assert "ZeroDivisionError" in errs[3] and r.finished


def test_step_limit_stops_a_looping_model():
    loop = [LLMTurn(tool_calls=[ToolCall("echo", {"word": "x"})]) for _ in range(10)]
    a, _ = agent_with(loop, [ECHO], max_steps=3)
    r = a.run("go")
    assert not r.finished and r.steps == 3 and "Stopped after 3 steps" in r.note


def test_every_step_is_logged(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    a, _ = agent_with([LLMTurn(tool_calls=[ToolCall("echo", {"word": "z"})]), LLMTurn(text="fin")], [ECHO], store=store)
    r = a.run("go")
    kinds = [s["kind"] for s in store.steps(r.run_id)]
    assert kinds == ["task", "tool_call", "tool_return", "final"]


def test_oversized_tool_result_is_refused():
    big = Tool("big", "x", [], lambda: {"x": "a" * 20000})
    a, _ = agent_with([LLMTurn(tool_calls=[ToolCall("big", {})]), LLMTurn(text="ok")], [big])
    assert "too large" in a.run("go").tool_calls[0]["result"]["error"]


def test_tool_input_types_are_checked():
    t = Tool("n", "x", [Param("k", "integer", "k")], lambda k: {"k": k})
    assert t.run({"k": "3"}) == {"k": 3}
    with pytest.raises(ToolError, match="integer"):
        t.run({"k": "abc"})
    s = t.schema()
    assert s["type"] == "OBJECT" and s["properties"]["k"]["type"] == "INTEGER" and s["required"] == ["k"]


# ---------- the real tools, on the recorded (offline) data ----------
@pytest.fixture
def ws(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    gnomad, clinvar, ctgov, _ = fixtures.offline_clients(store)
    return Workspace(gnomad, clinvar, ctgov, store=store)


def by_name(ws):
    return {t.name: t for t in build_tools(ws)}


def test_gap_tool_refuses_to_run_out_of_order(ws):
    with pytest.raises(ToolError, match="lookup_variants first"):
        by_name(ws)["compute_gap_table"].run({})


def test_unassessable_gene_gives_a_refusal_not_numbers(ws):
    out = by_name(ws)["lookup_variants"].run({"gene": "HLA-B"})
    assert out["assessable"] is False and out["variants"] == [] and "cannot be assessed" in out["message"]
    with pytest.raises(ToolError, match="nothing assessable"):
        by_name(ws)["compute_gap_table"].run({})


def test_whole_pipeline_through_the_tools(ws):
    t = by_name(ws)
    v = t["lookup_variants"].run({"gene": "CYP2C19"})
    assert v["assessable"] and any(x["rsid"] == "rs4244285" for x in v["variants"])
    tr = t["fetch_trial_demographics"].run({"drug": "clopidogrel", "max_trials": 3})
    assert tr["pooled_self_identified_race_counts"], tr
    pop = t["get_target_population"].run({"drug": "clopidogrel"})
    assert pop["shares"]
    gap = t["compute_gap_table"].run({})
    assert gap["variants"] and gap["variants"][0]["groups_under_mapping_A"]
    g0 = gap["variants"][0]["groups_under_mapping_A"][0]
    assert "carrier_ratio" in g0 and "robust_underrepresented_across_all_mappings" in g0


def test_agent_drives_the_real_tools_with_a_scripted_model(ws):
    script = [LLMTurn(tool_calls=[ToolCall("lookup_variants", {"gene": "CYP2C19"})]),
              LLMTurn(tool_calls=[ToolCall("fetch_trial_demographics", {"drug": "clopidogrel"})]),
              LLMTurn(tool_calls=[ToolCall("compute_gap_table", {})]),
              LLMTurn(text="summary")]
    a, _ = agent_with(script, build_tools(ws), store=ws.store)
    r = a.run("check clopidogrel")
    assert r.finished and [c["name"] for c in r.tool_calls] == ["lookup_variants", "fetch_trial_demographics", "compute_gap_table"]
    assert all("error" not in c["result"] for c in r.tool_calls)
