import pytest

from genegap import fixtures
from genegap.agents.base import LLMTurn, ToolCall
from genegap.agents.replay import Recording, Replay, ReplayMismatch
from genegap.agents.team import analyse
from genegap.store import Store


class FakeChat:
    def __init__(self, script): self.script = list(script)
    def send(self, text): return self.script.pop(0)
    def send_tool_results(self, results): return self.script.pop(0)


def call(name, **a):
    return LLMTurn(tool_calls=[ToolCall(name, a)])


def scripted():
    q = [[call("ask_variant_agent", gene="CYP2C19"), LLMTurn(text="final words")],
         [call("lookup_variants", gene="CYP2C19"), LLMTurn(text="vr")]]
    return lambda system, tools: FakeChat(q.pop(0))


def clients(tmp_path, name):
    st = Store(tmp_path / name)
    g, c, t, _ = fixtures.offline_clients(st)
    return g, c, t, st


def test_record_then_replay_gives_the_same_answer(tmp_path):
    g, c, t, st = clients(tmp_path, "a.sqlite")
    rec, f = Recording(), scripted()
    r1 = analyse("clopidogrel", g, c, t, st, smart_factory=rec.wrap(f), fast_factory=rec.wrap(f))
    path = tmp_path / "run.json"
    rec.save(path, drug="clopidogrel", smart_model="s", fast_model="f")

    g, c, t, st = clients(tmp_path, "b.sqlite")
    rp = Replay(path)
    r2 = analyse("clopidogrel", g, c, t, st, smart_factory=rp.factory(), fast_factory=rp.factory())
    assert r2.text == r1.text == "final words" and r2.finished
    assert r2.workspace.table.variants       # the tools really ran during the replay


def test_replay_stops_if_the_data_changed(tmp_path):
    g, c, t, st = clients(tmp_path, "a.sqlite")
    rec, f = Recording(), scripted()
    analyse("clopidogrel", g, c, t, st, smart_factory=rec.wrap(f), fast_factory=rec.wrap(f))
    rec.chats[1]["result_digests"][0] = "0000"       # pretend the data was different when recorded
    path = tmp_path / "run.json"
    rec.save(path, drug="x", smart_model="s", fast_model="f")
    g, c, t, st = clients(tmp_path, "b.sqlite")
    rp = Replay(path)
    with pytest.raises(ReplayMismatch, match="not the same"):
        analyse("clopidogrel", g, c, t, st, smart_factory=rp.factory(), fast_factory=rp.factory())
