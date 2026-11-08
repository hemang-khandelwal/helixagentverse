"""Ask GeneGap anything. Safe for judges to type into.

  python scripts/ask.py                          type questions one after another (blank line to quit)
  python scripts/ask.py "does warfarin ..."      one question
  Options: --replay (use saved runs from scripts/demo.py --record when they exist)   --fast (quicker model, for testing)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genegap.agents.frontdoor import answer
from genegap.store import Store

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="*")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--fast", action="store_true")
    a = ap.parse_args()
    store = Store()
    mode = "replay" if a.replay else "live"

    def one(q):
        r = answer(q, store, mode=mode, fast=a.fast, pace=0.4)
        print("\n" + ("ANSWER:\n" if r.team else "") + r.text + "\n")

    if a.question:
        one(" ".join(a.question))
    else:
        while True:
            q = input("Ask GeneGap> ").strip()
            if not q:
                break
            one(q)
