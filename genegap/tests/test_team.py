"""Tests for tasks 3.3 + 3.4. A scripted fake model plays every agent; real tools run on the recorded data."""
import pytest

from genegap import fixtures
from genegap.agents.base import LLMTurn, ToolCall
from genegap.agents.team import _drug_to_genes, analyse
from genegap.store import Store


class FakeChat:
    def __init__(self, script): self.script = list(script)
    def send(self, text): return self.script.pop(0)
    def send_tool_results(self, results): return self.script.pop(0)


def call(name, **args):
    return LLMTurn(tool_calls=[ToolCall(name, args)])


def factory(*scripts):
    """Hands out one script per agent, in the order the agents are created (orchestrator first)."""
    q = [list(s) for s in scripts]
    return lambda system, tools: FakeChat(q.pop(0))


@pytest.fixture
def clients(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    g, c, t, _ = fixtures.offline_clients(store)
    return g, c, t, store


def test_drug_to_gene_uses_the_curated_list():
    assert _drug_to_genes("Clopidogrel")[0]["gene"] == "CYP2C19"
    assert _drug_to_genes("nonsense") == []


def test_full_team_run(clients):
    g, c, t, store = clients
    orch = [call("find_gene_for_drug", drug="clopidogrel"), call("ask_variant_agent", gene="CYP2C19"),
            call("ask_trial_agent", drug="clopidogrel"), call("ask_gap_agent"), LLMTurn(text="final summary")]
    f = factory(orch,
                [call("lookup_variants", gene="CYP2C19"), LLMTurn(text="variant report")],
                [call("fetch_trial_demographics", drug="clopidogrel"), call("get_target_population", drug="clopidogrel"),
                 LLMTurn(text="trial report")],
                [call("compute_gap_table"), LLMTurn(text="gap report")])
    events = []
    r = analyse("clopidogrel", g, c, t, store=store, smart_factory=f, fast_factory=f,
                on_event=lambda a, k, p: events.append((a, k)))
    assert r.finished and r.text == "final summary"
    assert [n for n, _ in r.specialist_runs] == ["variant_agent", "trial_agent", "gap_agent"]
    # the real numbers are kept in the workspace, not only in the AI's words
    assert r.workspace.table.variants and r.workspace.counts and r.workspace.gap_runs
    # the orchestrator was handed real tool output
    gap_call = [x for x in r.orchestrator.tool_calls if x["name"] == "ask_gap_agent"][0]
    assert gap_call["result"]["data"]["compute_gap_table"]["variants"]
    assert ("gap_agent", "tool_call") in events
    # one run id covers every agent, so the audit trail is one story
    assert {s["agent"] for s in store.steps(r.run_id)} == {"orchestrator", "variant_agent", "trial_agent", "gap_agent"}


def test_orchestrator_cannot_skip_ahead(clients):
    g, c, t, store = clients
    f = factory([call("ask_gap_agent"), call("ask_trial_agent", drug="clopidogrel"), LLMTurn(text="gave up")])
    r = analyse("clopidogrel", g, c, t, store=store, smart_factory=f, fast_factory=f)
    errs = [x["result"].get("error", "") for x in r.orchestrator.tool_calls]
    assert all("first" in e for e in errs) and r.specialist_runs == []


def test_unassessable_gene_stops_the_chain(clients):
    g, c, t, store = clients
    f = factory([call("ask_variant_agent", gene="HLA-B"), call("ask_gap_agent"), LLMTurn(text="cannot assess HLA-B")],
                [call("lookup_variants", gene="HLA-B"), LLMTurn(text="cannot be assessed")])
    r = analyse("carbamazepine", g, c, t, store=store, smart_factory=f, fast_factory=f)
    first = r.orchestrator.tool_calls[0]["result"]["data"]["lookup_variants"]
    assert first["assessable"] is False
    assert "first" in r.orchestrator.tool_calls[1]["result"]["error"]   # the gap agent refused to run
    assert r.text == "cannot assess HLA-B"
