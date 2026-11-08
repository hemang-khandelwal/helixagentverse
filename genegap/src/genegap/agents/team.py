"""Tasks 3.3 + 3.4: the team of agents.

    Orchestrator (big model, plans and writes the summary)
        |-- find_gene_for_drug      (reads the medical lead's demo_scope.csv; no AI)
        |-- ask_variant_agent  ->  Variant agent (fast model)  -> lookup_variants
        |-- ask_trial_agent    ->  Trial agent   (fast model)  -> fetch_trial_demographics, get_target_population
        '-- ask_gap_agent      ->  Gap agent     (fast model)  -> compute_gap_table

The specialists do one small job each. The numbers still come from our code, never from a model.
Everything the team finds is also kept in the Workspace (ws), so later steps (the critic, the sign-off,
the report) can read the real data instead of re-reading the AI's words.
"""
from __future__ import annotations

import csv
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .. import config
from ..config import ROOT
from .base import Agent, AgentResult, ChatFactory, gemini_chat_factory
from .tools import Param, Tool, ToolError, Workspace, build_tools

DEMO_SCOPE = ROOT / "config" / "demo_scope.csv"
MAX_REPORT_CHARS = 700

VARIANT_ROLE = """You are the Variant agent. Call lookup_variants once for the gene you are given.
Then write a short report (under 120 words): whether the gene can be assessed, how many variants passed the
filters, and which ancestry groups have too little data. Copy any numbers exactly from the tool result."""

TRIAL_ROLE = """You are the Trial agent. Call fetch_trial_demographics for the drug you are given, then
get_target_population for the same drug. Write a short report (under 120 words): how many trials were read, how many
reported race, the total number of participants with race data, and any warning about the target population.
Copy numbers exactly from the tool results."""

GAP_ROLE = """You are the Gap agent. Call compute_gap_table once (the earlier steps have already been done).
Write a short report (under 150 words): which groups look under-represented, whether that holds under every ancestry
mapping, and what the main uncertainties are. Copy numbers exactly from the tool result."""

ORCH_ROLE = """You are the Orchestrator. You do not look anything up yourself; you ask your specialist agents.
Work in this order:
1. find_gene_for_drug to see which gene(s) to check. If it finds none, stop and say the drug is outside the demo scope.
2. ask_variant_agent for the gene. If the gene cannot be assessed or no variant passed the filters, STOP and say so
   plainly. That is the correct answer. Do not continue and do not guess.
3. ask_trial_agent for the drug. If no trial reported a race breakdown, stop and say the trial side cannot be assessed.
4. ask_gap_agent.
Then write your final answer for a non-expert, under 220 words: what was checked, which groups look under-represented
and whether that holds under every ancestry mapping, and the main uncertainties (low data, target-population warning,
provisional assumptions). Copy numbers exactly from the specialists' data. Name the real data sources (gnomAD, ClinVar, ClinicalTrials.gov) and fact ids, never the names of your tools or agents. Explain each group code once in words (eas = East Asian, sas = South Asian, amr = Admixed American, afr = African, nfe = Non-Finnish European, ami = Amish, asj = Ashkenazi Jewish, mid = Middle Eastern). End with: "This is a hypothesis for
expert review, not clinical advice." """


@dataclass
class TeamResult:
    text: str                 # the orchestrator's final answer
    finished: bool
    run_id: str
    workspace: Workspace      # the real data: ws.table, ws.counts, ws.gap_runs ...
    orchestrator: AgentResult
    specialist_runs: list     # [(agent name, AgentResult)] in the order they ran


def _drug_to_genes(drug: str, path=DEMO_SCOPE) -> list[dict]:
    rows, seen = [], set()
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r["drug"].strip().lower() == drug.strip().lower() and r["gene"] not in seen:
                seen.add(r["gene"])
                rows.append({"gene": r["gene"], "indication": r["indication"], "source": r["source"],
                             "verified_by": r["verified_by"]})
    return rows


def build_team(ws: Workspace, smart_factory: ChatFactory, fast_factory: ChatFactory, store=None,
               on_event=None, run_id: Optional[str] = None, max_steps: int = 8):
    """Returns (orchestrator Agent, list that fills with (name, AgentResult) as specialists run)."""
    run_id = run_id or uuid.uuid4().hex[:12]
    base = {t.name: t for t in build_tools(ws)}
    common = dict(store=store, run_id=run_id, on_event=on_event, max_steps=max_steps)
    variant = Agent("variant_agent", VARIANT_ROLE, [base["lookup_variants"]], fast_factory, **common)
    trial = Agent("trial_agent", TRIAL_ROLE, [base["fetch_trial_demographics"], base["get_target_population"]],
                  fast_factory, **common)
    gap = Agent("gap_agent", GAP_ROLE, [base["compute_gap_table"]], fast_factory, **common)
    runs: list = []

    def delegate(agent: Agent, task: str, want: list[str]) -> dict:
        res = agent.run(task)
        runs.append((agent.name, res))
        data = {}
        for c in res.tool_calls:
            if c["name"] in want:
                data[c["name"]] = c["result"]      # the real tool output, not the model's retelling
        return {"agent": agent.name, "finished": res.finished, "report": res.text[:MAX_REPORT_CHARS], "data": data}

    def find_gene_for_drug(drug: str) -> dict:
        genes = _drug_to_genes(drug)
        if not genes:
            return {"drug": drug, "genes": [], "message": "This drug is not in the demo scope (config/demo_scope.csv)."}
        return {"drug": drug, "genes": genes}

    def ask_variant_agent(gene: str) -> dict:
        return delegate(variant, f"Look up the variants of the gene {gene}.", ["lookup_variants"])

    def ask_trial_agent(drug: str) -> dict:
        if ws.table is None:
            raise ToolError("Ask the variant agent first.")
        return delegate(trial, f"Find the trials of {drug} and the target population for {drug}.",
                        ["fetch_trial_demographics", "get_target_population"])

    def ask_gap_agent(population_id: str = "us_acs_antiplatelet") -> dict:
        if ws.table is None or not ws.counts:
            raise ToolError("Ask the variant agent and the trial agent first (and the trial agent must find race counts).")
        return delegate(gap, f"Compute the gap table using population {population_id}.", ["compute_gap_table"])

    tools = [
        Tool("find_gene_for_drug", "Find which gene(s) matter for a drug, from the medical lead's curated list.",
             [Param("drug", "string", "Drug name, e.g. clopidogrel")], find_gene_for_drug),
        Tool("ask_variant_agent", "Ask the Variant agent to look up a gene's variants and ancestry frequencies.",
             [Param("gene", "string", "Gene symbol")], ask_variant_agent),
        Tool("ask_trial_agent", "Ask the Trial agent to find the drug's trials and the target population.",
             [Param("drug", "string", "Drug name")], ask_trial_agent),
        Tool("ask_gap_agent", "Ask the Gap agent to compute the representation gap. Needs the two agents above first.",
             [Param("population_id", "string", "Optional population id", required=False)], ask_gap_agent),
    ]
    orch = Agent("orchestrator", ORCH_ROLE, tools, smart_factory, **common)
    return orch, runs


def analyse(drug: str, gnomad, clinvar, ctgov, store=None, smart_factory: Optional[ChatFactory] = None,
            fast_factory: Optional[ChatFactory] = None, on_event: Optional[Callable] = None,
            question: Optional[str] = None) -> TeamResult:
    """The one function everyone else calls:  analyse('clopidogrel', gnomad, clinvar, ctgov, store)"""
    ws = Workspace(gnomad, clinvar, ctgov, store=store)
    smart = smart_factory or gemini_chat_factory(config.GEMINI_MODEL)
    fast = fast_factory or gemini_chat_factory(config.GEMINI_FAST_MODEL)
    orch, runs = build_team(ws, smart, fast, store=store, on_event=on_event)
    res = orch.run(question or f"Does the clinical-trial evidence for {drug} cover the genetic variation that matters for it?")
    return TeamResult(res.text, res.finished, orch.run_id, ws, res, runs)
