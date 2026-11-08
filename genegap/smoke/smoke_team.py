"""Tasks 3.3 + 3.4 for real:  python smoke/smoke_team.py --offline
A team of Gemini agents: an orchestrator plus a variant, trial and gap specialist.
It prints each step as it happens, so you can watch the team work.

  --offline   use the recorded data (fast, no internet)
  --fast      use the fast model for the orchestrator too (quicker; remove for the real demo)
  --drug X    try another drug from config/demo_scope.csv
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genegap import config, fixtures
from genegap.agents.base import gemini_chat_factory
from genegap.agents.team import analyse
from genegap.clients import ClinVarClient, CTGovClient, GnomadClient
from genegap.store import Store

T0 = time.time()


def show(agent, kind, payload):
    t = f"[{time.time() - T0:5.1f}s]"
    if kind == "task":
        print(f"{t} {agent}: starting -> {payload['task'][:90]}")
    elif kind == "tool_call":
        print(f"{t} {agent}: calling {payload['name']}({payload['args']})")
    elif kind == "tool_return":
        r = payload["result"]
        msg = "ERROR: " + r["error"][:120] if isinstance(r, dict) and "error" in r else "ok"
        print(f"{t} {agent}: {payload['name']} -> {msg}")
    elif kind == "final":
        print(f"{t} {agent}: finished")
    elif kind == "stopped":
        print(f"{t} {agent}: STOPPED ({payload['reason']})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--drug", default="clopidogrel")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--fast", action="store_true")
    a = ap.parse_args()

    store = Store()
    if a.offline:
        gnomad, clinvar, ctgov, _ = fixtures.offline_clients(store)
    else:
        gnomad, clinvar, ctgov = GnomadClient(store=store), ClinVarClient(store=store), CTGovClient(store=store)
    smart = gemini_chat_factory(config.GEMINI_FAST_MODEL if a.fast else config.GEMINI_MODEL)

    r = analyse(a.drug, gnomad, clinvar, ctgov, store=store, smart_factory=smart,
                fast_factory=gemini_chat_factory(config.GEMINI_FAST_MODEL), on_event=show)
    print(f"\nFINISHED: {r.finished}   run id: {r.run_id}   total time: {time.time() - T0:.0f}s")
    print("\nANSWER:\n" + r.text)
