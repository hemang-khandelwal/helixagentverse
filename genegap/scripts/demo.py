"""Demo runner (task 7.2).

  python scripts/demo.py --record     run the team with the REAL Gemini and the live data sources, and save the run
  python scripts/demo.py              REPLAY the saved run (fast; needs no internet and no Gemini)
Options:  --drug clopidogrel   --pace 0.4 (seconds between lines when replaying)

How it stays honest: the replay is announced on screen, and the numbers are still recomputed by our code from the
data saved during the recording. If that data differs from the recording, the replay stops instead of showing
wording that no longer matches the numbers.
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from genegap import config
from genegap.agents.base import gemini_chat_factory
from genegap.agents.replay import Recording, Replay, ReplayMismatch
from genegap.agents.team import analyse
from genegap.clients import ClinVarClient, CTGovClient, GnomadClient
from genegap.store import Store

T0 = time.time()
PACE = 0.0


def show(agent, kind, payload):
    t = f"[{time.time() - T0:5.1f}s]"
    line = None
    if kind == "task":
        line = f"{t} {agent}: starting -> {payload['task'][:90]}"
    elif kind == "tool_call":
        line = f"{t} {agent}: calling {payload['name']}({payload['args']})"
    elif kind == "tool_return":
        r = payload["result"]
        bad = isinstance(r, dict) and "error" in r
        line = f"{t} {agent}: {payload['name']} -> " + ("ERROR: " + r["error"][:100] if bad else "ok")
    elif kind == "final":
        line = f"{t} {agent}: finished"
    elif kind == "stopped":
        line = f"{t} {agent}: STOPPED ({payload['reason']})"
    if line:
        print(line, flush=True)
        time.sleep(PACE)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--drug", default="clopidogrel")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--pace", type=float, default=0.4)
    a = ap.parse_args()
    path = ROOT / "data" / "demo_runs" / f"{a.drug.lower()}.json"
    store = Store()

    if a.record:
        gnomad, clinvar, ctgov = GnomadClient(store=store), ClinVarClient(store=store), CTGovClient(store=store)
        rec = Recording()
        print("RECORDING a live run with Gemini...")
        r = analyse(a.drug, gnomad, clinvar, ctgov, store=store,
                    smart_factory=rec.wrap(gemini_chat_factory(config.GEMINI_MODEL)),
                    fast_factory=rec.wrap(gemini_chat_factory(config.GEMINI_FAST_MODEL)), on_event=show)
        if not r.finished:
            sys.exit("The run did not finish, so it was NOT saved. Try again.")
        rec.save(path, drug=a.drug, smart_model=config.GEMINI_MODEL, fast_model=config.GEMINI_FAST_MODEL, final_text=r.text)
        print(f"\nSaved to {path}\n\nANSWER:\n{r.text}")
    else:
        if not path.exists():
            sys.exit(f"No recording at {path}. Make one first with:  python scripts/demo.py --record")
        PACE = a.pace
        rp = Replay(path)
        # offline=True means no internet: the clients read the data saved in the database during the recording
        gnomad, clinvar, ctgov = (GnomadClient(store=store, offline=True), ClinVarClient(store=store, offline=True),
                                  CTGovClient(store=store, offline=True))
        print(f"*** REPLAY of a run recorded {rp.meta['recorded_at']} with {rp.meta['smart_model']} / {rp.meta['fast_model']} ***")
        print("*** Our code recomputes every number now from saved data; only Gemini's wording is replayed. ***\n")
        try:
            r = analyse(a.drug, gnomad, clinvar, ctgov, store=store, smart_factory=rp.factory(), fast_factory=rp.factory(),
                        on_event=show)
        except ReplayMismatch as e:
            sys.exit(f"\nREPLAY STOPPED: {e}")
        print(f"\nANSWER (replayed wording):\n{r.text}")
