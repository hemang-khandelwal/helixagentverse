"""C owns this module. C3 enforces budgets and records the investigation."""

from contracts import EvidenceGraph, RunRequest, RunResult
from tools import HelixTools


def run_b_full(request: RunRequest, tools: HelixTools) -> RunResult:
    raise NotImplementedError("C3: expansion loop, Python budgets, verifier, run trace")


def run_baseline(request: RunRequest) -> RunResult:
    raise NotImplementedError("C/B7 handoff: model-only arm with abstention")


def run_a_plus(request: RunRequest, graph: EvidenceGraph) -> RunResult:
    raise NotImplementedError("C/B7 handoff: replay B_full's exact graph; no verifier")
