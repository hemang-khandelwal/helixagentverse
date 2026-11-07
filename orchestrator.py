"""C owns this module. C3 enforces budgets and records the investigation."""

# C writes the workflow controller here. It calls the other parts in order, keeps
# track of progress, and stops when time or search limits are reached.



from contracts import EvidenceGraph, RunRequest, RunResult
from tools import HelixTools


def run_b_full(request: RunRequest, tools: HelixTools) -> RunResult:
    # Run the full investigation: retrieve evidence, ask the AI for next steps,
    # update the map, enforce limits, check findings, and record the result. Still to
    # build: the error below deliberately stops this unfinished function.
    raise NotImplementedError("C3: expansion loop, Python budgets, verifier, run trace")


def run_baseline(request: RunRequest) -> RunResult:
    # Run the comparison version using the AI without retrieval tools. This gives us
    # something to compare the full system against. Still to build: the error below
    # deliberately stops this unfinished function.
    raise NotImplementedError("C/B7 handoff: model-only arm with abstention")


def run_a_plus(request: RunRequest, graph: EvidenceGraph) -> RunResult:
    # Give the AI exactly the evidence map found by the full system, but no further
    # search or programmed checker. This helps measure what the checker adds. Still
    # to build: the error below deliberately stops this unfinished function.
    raise NotImplementedError("C/B7 handoff: replay B_full's exact graph; no verifier")
