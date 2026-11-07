"""C owns this module. C2 implements model decisions, not arithmetic."""

from contracts import (
    CompoundRecord, EvidenceGraph, ExpansionDecision, FrontierItem,
    LiteratureRecord, RouteJudgment, RunBudget, TransferJudgment,
)


def choose_expansion(
    graph: EvidenceGraph, frontier: list[FrontierItem], *,
    budget: RunBudget, expansions_used: int, elapsed_seconds: float,
) -> ExpansionDecision:
    raise NotImplementedError("C2: choose a frontier node/tool or stop with a reason")


def assess_route(articles: list[LiteratureRecord]) -> RouteJudgment:
    raise NotImplementedError("C2: read which route matters and cite its evidence")


def assess_transfer(
    drug: CompoundRecord, analog: CompoundRecord, articles: list[LiteratureRecord],
) -> TransferJudgment:
    raise NotImplementedError("C2: assess transfer and name the shared mechanism")
