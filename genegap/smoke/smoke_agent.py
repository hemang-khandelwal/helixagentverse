"""Tasks 3.1 + 3.2 for real:  python smoke/smoke_agent.py
Gemini decides which of our tools to call, in what order, and writes a short summary.
Add --offline to use the recorded responses instead of the live APIs (recommended for the first try).
Add --drug / --gene to change the question.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genegap import fixtures
from genegap.agents.base import Agent, gemini_chat_factory
from genegap.agents.tools import Workspace, build_tools
from genegap.clients import ClinVarClient, CTGovClient, GnomadClient
from genegap.store import Store

ROLE = """You check whether clinical trials of a drug enrolled enough people from each ancestry group to cover the
genetic variation that matters for that drug. Work in this order: lookup_variants for the gene, fetch_trial_demographics
for the drug, get_target_population, then compute_gap_table. Then write a short plain-language summary (under 200 words)
for a non-expert: what you looked at, which groups look under-represented, what the main uncertainties are
(including any warning about the target population or low data). Copy numbers exactly from the tool results."""

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gene", default="CYP2C19")
    ap.add_argument("--drug", default="clopidogrel")
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()

    store = Store()
    if a.offline:
        gnomad, clinvar, ctgov, _ = fixtures.offline_clients(store)
    else:
        gnomad, clinvar, ctgov = GnomadClient(store=store), ClinVarClient(store=store), CTGovClient(store=store)

    agent = Agent("orchestrator", ROLE, build_tools(Workspace(gnomad, clinvar, ctgov, store=store)),
                  gemini_chat_factory(), store=store)
    result = agent.run(f"Does the trial evidence for {a.drug} cover the variation in {a.gene}?")

    print("\nTOOLS GEMINI CALLED:")
    for c in result.tool_calls:
        bad = "  <-- ERROR: " + c["result"]["error"] if "error" in c["result"] else ""
        print(f"  {c['name']}({c['args']}){bad}")
    print(f"\nFINISHED: {result.finished}   steps: {result.steps}   run id: {result.run_id}")
    if result.note:
        print("NOTE:", result.note)
    print("\nANSWER:\n" + result.text)
