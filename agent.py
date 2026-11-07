"""C owns this module. C2 implements model decisions, not arithmetic."""

# C connects the AI here. Its job is to choose the next investigation step and read
# evidence. Ordinary Python code handles calculations.



from contracts import (
    CompoundRecord, EvidenceGraph, ExpansionDecision, FrontierItem,
    LiteratureRecord, RouteJudgment, RunBudget, TransferJudgment,
)


def choose_expansion(
    graph: EvidenceGraph, frontier: list[FrontierItem], *,
    budget: RunBudget, expansions_used: int, elapsed_seconds: float,
) -> ExpansionDecision:
    # Ask the AI what to investigate next, or whether to stop. Supply the current
    # evidence, available search items, and remaining time/search limits. Still to
    # build: the error below deliberately stops this unfinished function.
    raise NotImplementedError("C2: choose a frontier node/tool or stop with a reason")


def assess_route(articles: list[LiteratureRecord]) -> RouteJudgment:
    # Ask the AI to read the supplied papers and explain which drug-processing route
    # matters, with sources. Still to build: the error below deliberately stops this
    # unfinished function.
    raise NotImplementedError("C2: read which route matters and cite its evidence")


def assess_transfer(
    drug: CompoundRecord, analog: CompoundRecord, articles: list[LiteratureRecord],
) -> TransferJudgment:
    # Ask whether a related drug's finding could apply to this drug. Require a shared
    # biological reason and supporting sources. Still to build: the error below
    # deliberately stops this unfinished function.
    raise NotImplementedError("C2: assess transfer and name the shared mechanism")
