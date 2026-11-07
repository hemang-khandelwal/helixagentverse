"""A owns this module. A7 implements the evidence graph operations."""

from contracts import Candidate, CompoundRecord, EvidenceGraph, FrontierItem


def seed_graph(compound: CompoundRecord) -> EvidenceGraph:
    raise NotImplementedError("A7: create the initial drug graph")


def score_frontier(graph: EvidenceGraph) -> list[FrontierItem]:
    raise NotImplementedError("A7: rank unexpanded nodes")


def enumerate_candidates(graph: EvidenceGraph) -> list[Candidate]:
    raise NotImplementedError("A7: enumerate cited gene/allele/phenotype paths")
