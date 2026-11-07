#Complete the C2 integration for Project Helix.

#I have attached the current `agent.py`, the completed A7 `graph.py`, C3 `orchestrator.py`, `tools.py`, and frozen `contracts.py`.

#Read all files and the C2 integration TODO at the top of `agent.py`.

#Update only `agent.py` so its three decision functions work with the completed graph and orchestrator. Integrate structured LLM reasoning where appropriate, retain a deterministic fallback, and preserve the project's scientific, temporal and adjudication constraints.

#Do not modify `contracts.py` or other programmers' files. If an interface is missing or incompatible, identify the problem rather than inventing one.

#Run the available tests, add targeted C2 integration tests where appropriate, and return a complete replacement `agent.py`. Explain any remaining limitations.
# ============================================================
# C2 INTEGRATION TODO — COMPLETE AFTER A7 AND C3 ARE FINISHED
# ============================================================
#
# CONTEXT:
# This file implements C2, the Project Helix decision-making
# agent. The current implementation uses deterministic rules
# as a temporary fallback, rather than an LLM.
#
# Once A7 (graph.py) and C3 (orchestrator.py) are completed,
# integrate this module into the full agentic pipeline.
#
# REQUIRED INTEGRATION WORK:
#
# 1. CHECK THE INTERFACES
#    Read the final versions of contracts.py, graph.py,
#    tools.py and orchestrator.py.
#    Ensure choose_expansion(), assess_route() and
#    assess_transfer() use the correct input and output types.
#    Do not modify the frozen contracts.py.
#
# 2. CONNECT GRAPH EXPANSION (A7)
#    Make choose_expansion() operate on the actual evidence
#    graph and its scored frontier.
#    It must select the next useful node/tool to investigate,
#    avoid repeated or invalid expansions, and allow the
#    agent to stop when further investigation is unjustified.
#
# 3. CONNECT THE ORCHESTRATOR (C3)
#    The orchestrator must call choose_expansion(), validate
#    its decision, execute the selected tool, update the graph,
#    and repeat as appropriate.
#    The agent proposes actions; the orchestrator controls
#    execution and enforces budgets.
#
# 4. ADD LLM-BASED REASONING
#    Replace or supplement the deterministic heuristics with
#    structured LLM decisions for:
#      - choosing which evidence to investigate next;
#      - interpreting clinically relevant metabolic routes;
#      - judging whether an analogy is transferable.
#
#    Require structured outputs compatible with contracts.py.
#    Keep the deterministic implementation as a fallback.
#    Never invent citations, allele associations, shared
#    mechanisms, or missing clinical facts.
#
# 5. ENFORCE SAFETY AND SCIENTIFIC BOUNDARIES
#    The agent must not independently adjudicate hypotheses.
#    Only the designated adjudicating checks may reject.
#    Other checks annotate evidence and uncertainty.
#    Do not calculate clinical quantities using the LLM.
#    Respect historical evidence cutoffs and prevent
#    present-day association data leaking into historical arms.
#    Do not produce a confidence scalar.
#
# 6. HANDLE FAILURE AND STOPPING CONDITIONS
#    Respect the orchestrator's expansion, time and depth
#    budgets. Handle empty frontiers, unavailable evidence,
#    invalid tool choices and failed tool calls.
#    Preserve explicit abstention and unresolved outcomes.
#
# 7. TEST THE INTEGRATION
#    Verify that:
#      - C2 decisions match the final A7/C3 interfaces;
#      - the orchestrator can execute proposed tool calls;
#      - graph expansions do not repeat indefinitely;
#      - citations and historical cutoffs are preserved;
#      - unsupported analogies are not asserted as facts;
#      - the pipeline handles missing evidence gracefully;
#      - existing A1 tests still pass.
#
# DELIVERABLE:
# A fully integrated agent.py that works with the final
# graph.py and orchestrator.py, preserves the frozen data
# contracts, and can support the complete Helix pipeline.
#
# IMPORTANT:
# Do not assume that an LLM integration or final graph
# interface already exists. Inspect the completed modules
# before making changes. Do not silently edit files owned
# by other programmers.
# ============================================================


"""Project Helix — C2: conservative agent decisions.

Drop-in replacement for the A1 agent.py stub. This implementation uses only
standard-library, evidence-grounded rules; it does not pretend to be an LLM.
C3 may later replace the decision policy with a model, while retaining the
frozen contracts and validating all decisions before tool execution.
"""
from __future__ import annotations

import re
from contracts import (
    CompoundRecord, EvidenceGraph, ExpansionDecision, FrontierItem,
    LiteratureRecord, RouteJudgment, RunBudget, TransferJudgment,
)

# The names below are the ONLY tools accepted by the A1 HelixTools interface.
_TOOL_FOR_KIND = {
    "Drug": "fetch_literature",
    "Route": "fetch_literature",
    "Gene": "fetch_pgx",
    "Variant": "fetch_frequency",
    "Phenotype": "fetch_literature",
    "Analog": "fetch_literature",
    "Population": None,  # No ancestry is recorded on GraphNode; cannot guess one.
}

# These are textual clues, not assertions about any particular drug.
_ROUTE_PATTERNS = (
    ("CYP2D6", r"\bCYP\s*2D6\b"),
    ("CYP2C19", r"\bCYP\s*2C19\b"),
    ("CYP2C9", r"\bCYP\s*2C9\b"),
    ("CYP3A4", r"\bCYP\s*3A4\b"),
    ("CYP3A5", r"\bCYP\s*3A5\b"),
    ("CYP1A2", r"\bCYP\s*1A2\b"),
    ("UGT1A1", r"\bUGT\s*1A1\b"),
    ("UGT2B7", r"\bUGT\s*2B7\b"),
    ("DPYD", r"\bDPYD\b|\bDPD\b"),
    ("TPMT", r"\bTPMT\b"),
    ("NUDT15", r"\bNUDT\s*15\b"),
    ("CES1", r"\bCES\s*1\b"),
    ("renal excretion", r"\brenal (?:clearance|excretion|elimination)\b"),
    ("glucuronidation", r"\bglucuronidat(?:ion|ed)\b"),
)
_ROUTE_CONTEXT = re.compile(
    r"\b(metaboli(?:sm|c|[sz](?:e|ed|es|ing|ation))|clearance|eliminat(?:ion|ed)|"
    r"substrate|bioactivat(?:ion|ed)|inactivat(?:ion|ed)|"
    r"major pathway|primary pathway|predominant pathway|"
    r"pharmacokinetic|excret(?:ion|ed))\b", re.I
)
_STRONG_CONTEXT = re.compile(
    r"\b(major|primary|predominant|principal|main|rate.limiting|"
    r"responsible for|mediated by)\b", re.I
)
_NEGATION = re.compile(
    r"\b(not|no|neither|unlikely|does not|did not|isn't|wasn't|"
    r"not primarily|not a|minor|negligible)\b", re.I
)


def _sentences(article: LiteratureRecord) -> list[str]:
    # Abstract text is the actual evidence; titles can guide retrieval but
    # should not independently establish a mechanistic route.
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", article.abstract)
            if s.strip()]


def choose_expansion(
    graph: EvidenceGraph, frontier: list[FrontierItem], *,
    budget: RunBudget, expansions_used: int, elapsed_seconds: float,
) -> ExpansionDecision:
<<<<<<< HEAD
    # Ask the AI what to investigate next, or whether to stop. Supply the current
    # evidence, available search items, and remaining time/search limits. Still to
    # build: the error below deliberately stops this unfinished function.
    raise NotImplementedError("C2: choose a frontier node/tool or stop with a reason")


def assess_route(articles: list[LiteratureRecord]) -> RouteJudgment:
    # Ask the AI to read the supplied papers and explain which drug-processing route
    # matters, with sources. Still to build: the error below deliberately stops this
    # unfinished function.
    raise NotImplementedError("C2: read which route matters and cite its evidence")
=======
    """Choose an eligible frontier node and a safe typed tool call, or STOP.

    This is a deterministic fallback policy. C3 MUST separately enforce all
    budgets, validate arguments and prevent repeated tool calls.
    """
    if expansions_used >= budget.max_expansions:
        return ExpansionDecision("STOP", "Expansion budget exhausted")
    if elapsed_seconds >= budget.max_seconds:
        return ExpansionDecision("STOP", "Time budget exhausted")
    if not frontier:
        return ExpansionDecision("STOP", "No frontier nodes remain")

    # Only consider nodes present in the graph, and never exceed analog depth.
    eligible = []
    for item in frontier:
        node = graph.nodes.get(item.node_id)
        if node is None or node.analog_depth > budget.max_analog_hops:
            continue
        if _TOOL_FOR_KIND.get(node.kind) is None or node.kind == "Variant":
            continue
        if not node.label.strip():
            continue
        eligible.append((item, node))
    if not eligible:
        return ExpansionDecision("STOP", "No eligible frontier nodes with supported tools")

    # A7 provides heuristic scores; respect those instead of recomputing them.
    eligible.sort(key=lambda pair: (-pair[0].heuristic_score, pair[0].node_id))
    item, node = eligible[0]
    tool = _TOOL_FOR_KIND[node.kind]

    if tool == "fetch_frequency":
        # The A1 graph node does not carry ancestry. The tool requires it;
        # guessing a population would create a misleading result.
        return ExpansionDecision(
            "STOP", "Highest-ranked variant needs an ancestry to query frequency; "
            "A1 GraphNode does not provide one"
        )
    if tool == "fetch_pgx":
        args = {"gene_or_drug": node.label}
    else:
        args = {"query": node.label}

    return ExpansionDecision(
        action="EXPAND",
        reason=f"Investigate {node.kind} '{node.label}': {item.reason}",
        node_id=node.node_id,
        tool=tool,
        arguments=args,
    )


def assess_route(articles: list[LiteratureRecord]) -> RouteJudgment:
    """Identify a specifically supported route from retrieved abstract text.

    Requires route name and metabolism/clearance context in the same sentence.
    If competing routes are equally supported, abstain rather than guess.
    """
    matches: dict[str, set[str]] = {}
    strong: dict[str, set[str]] = {}
    for article in articles:
        sid = article.citation.source_id
        if not sid:
            continue
        for sentence in _sentences(article):
            if not _ROUTE_CONTEXT.search(sentence):
                continue
            for route, pattern in _ROUTE_PATTERNS:
                if re.search(pattern, sentence, re.I):
                    # A conservative sentence-level negation filter.
                    if _NEGATION.search(sentence):
                        continue
                    matches.setdefault(route, set()).add(sid)
                    if _STRONG_CONTEXT.search(sentence):
                        strong.setdefault(route, set()).add(sid)

    if not matches:
        return RouteJudgment(None, "No clearly supported metabolic/clearance route in supplied abstracts", [])
    # Prioritize explicit major/primary-route claims over incidental mentions.
    ranked = sorted(matches, key=lambda r: (-len(strong.get(r, set())),
                                              -len(matches[r]), r))
    best = ranked[0]
    if len(ranked) > 1:
        second = ranked[1]
        if (len(strong.get(best, set())), len(matches[best])) == (
            len(strong.get(second, set())), len(matches[second])
        ):
            return RouteJudgment(
                None, f"Ambiguous route: {best} and {second} have equally strong textual support",
                sorted(matches[best] | matches[second]),
            )
    return RouteJudgment(
        best,
        f"Abstract text links {best} to drug metabolism/clearance; "
        "verify relevance to the input drug and clinical phenotype before use",
        sorted(matches[best]),
    )
>>>>>>> b3ad50a4b235b0d5970af7054136a507dc3d8150


def assess_transfer(
    drug: CompoundRecord, analog: CompoundRecord, articles: list[LiteratureRecord],
) -> TransferJudgment:
<<<<<<< HEAD
    # Ask whether a related drug's finding could apply to this drug. Require a shared
    # biological reason and supporting sources. Still to build: the error below
    # deliberately stops this unfinished function.
    raise NotImplementedError("C2: assess transfer and name the shared mechanism")
=======
    """Conservatively assess analogy; do not infer liability from similarity alone.

    A transferable finding needs cited text naming both compounds and a shared
    mechanistic feature. This rule is deliberately stricter than a model-based
    assessment, and may abstain on genuine analogies.
    """
    if not drug.name.strip() or not analog.name.strip():
        return TransferJudgment(None, None, "Compound names are missing", [])
    if drug.compound_id == analog.compound_id or drug.name.casefold() == analog.name.casefold():
        return TransferJudgment(False, None, "The analog is the input compound itself", [])

    shared_targets = sorted(set(drug.target_genes) & set(analog.target_genes))
    supporting: dict[str, set[str]] = {}
    for article in articles:
        sid = article.citation.source_id
        if not sid:
            continue
        text = article.abstract
        # Both compound names must be explicit in the same abstract.
        if not (re.search(r"(?<!\w)" + re.escape(drug.name) + r"(?!\w)", text, re.I)
                and re.search(r"(?<!\w)" + re.escape(analog.name) + r"(?!\w)", text, re.I)):
            continue
        for target in shared_targets:
            if re.search(r"(?<!\w)" + re.escape(target) + r"(?!\w)", text, re.I):
                supporting.setdefault(f"shared target {target}", set()).add(sid)
        # Explicit shared-route statement, not just two separate mentions.
        for route, pattern in _ROUTE_PATTERNS:
            if re.search(pattern, text, re.I) and re.search(
                r"\b(both|shared|same|similarly|common)\b", text, re.I
            ) and _ROUTE_CONTEXT.search(text):
                supporting.setdefault(f"reported shared route {route}", set()).add(sid)

    if not supporting:
        return TransferJudgment(
            None, None,
            "Insufficient cited evidence that the two compounds share a relevant "
            "mechanism; structural similarity or common targets alone cannot "
            "establish transferable pharmacogenomic liability",
            [],
        )
    # Even a cited shared mechanism does not establish transferable clinical
    # liability. The judgment remains unresolved pending phenotype evidence.
    feature = sorted(supporting)[0]
    return TransferJudgment(
        None, feature,
        "A cited mechanistic bridge was found, but transfer of the clinical "
        "gene/allele/phenotype association still requires direct review",
        sorted(supporting[feature]),
    )
>>>>>>> b3ad50a4b235b0d5970af7054136a507dc3d8150
