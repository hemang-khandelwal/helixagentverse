"""A owns this module. A7 implements the evidence graph operations."""

# A builds the evidence map here. Think of a diagram: boxes are drugs or genes, lines
# are connections, and each line points to a source.



from contracts import Candidate, CompoundRecord, EvidenceGraph, FrontierItem


def seed_graph(compound: CompoundRecord) -> EvidenceGraph:
    # Create the starting evidence map containing the chosen drug. Later searches
    # will add genes, effects, and supporting connections. Still to build: the error
    # below deliberately stops this unfinished function.
    raise NotImplementedError("A7: create the initial drug graph")


def score_frontier(graph: EvidenceGraph) -> list[FrontierItem]:
    # List the items we could investigate next and give them a search priority. This
    # priority guides searching; it does not measure medical certainty. Still to
    # build: the error below deliberately stops this unfinished function.
    raise NotImplementedError("A7: rank unexpanded nodes")


def enumerate_candidates(graph: EvidenceGraph) -> list[Candidate]:
    # Follow connections in the evidence map to collect possible gene/allele/effect
    # findings. The checker still needs to assess them. Still to build: the error
    # below deliberately stops this unfinished function.
    raise NotImplementedError("A7: enumerate cited gene/allele/phenotype paths")
