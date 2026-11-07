"""A owns this module. A4/A5/A6/A8 implement the retrieval methods.

All methods are stubs. They raise rather than pretending evidence was found.
Bind the historical cutoff when constructing the toolset; never expose a
per-call date override to the model. A6 must filter every retrieved citation.
"""

# A tool is a Python function that gets information for the AI. A writes these tools.
# The AI asks for information; the tool implementation controls how it is retrieved.



from __future__ import annotations

from datetime import date
from pathlib import Path

from contracts import (
    AnalogRecord, CompoundRecord, FrequencyRecord, LiteratureRecord,
    PgxAssociationRecord,
)


class HelixTools:
    # The collection of information-finding tools. Creating it only stores settings.
    # The retrieval methods below still need to be written.
    def __init__(
        self, *, max_date: date | None,
        data_dir: Path = Path("data"), cache_dir: Path = Path("cache"),
    ) -> None:
        # Remember the date limit and folder locations for later tool calls. This
        # does not retrieve or filter any evidence yet.
        self.max_date = max_date
        self.data_dir = data_dir
        self.cache_dir = cache_dir

    def fetch_compound(self, name_or_smiles: str) -> CompoundRecord:
        # Look up a drug by its name or chemical structure text. Return one
        # CompoundRecord with source details. Still to build: the error below
        # deliberately stops this unfinished function.
        raise NotImplementedError("A5: load compound data")

    def similar_compounds(self, smiles: str, k: int = 5) -> list[AnalogRecord]:
        # Find up to k drugs with similar structures. For example, k=5 asks for five
        # matches. Return their information and similarity scores. Still to build:
        # the error below deliberately stops this unfinished function.
        raise NotImplementedError("A4: RDKit similarity")

    def fetch_pgx(self, gene_or_drug: str) -> list[PgxAssociationRecord]:
        # Find reported genetic associations for the requested gene or drug. The
        # implementation must exclude evidence newer than the saved date limit. Still
        # to build: the error below deliberately stops this unfinished function.
        raise NotImplementedError("A5: load and date-filter association records")

    def fetch_frequency(self, variant: str, ancestry: str) -> FrequencyRecord | None:
        # Look up how common this variant is in the requested ancestry group. Return
        # None when no suitable record is available. Still to build: the error below
        # deliberately stops this unfinished function.
        raise NotImplementedError("A5: load population frequencies")

    def fetch_literature(self, query: str, max_results: int = 20) -> list[LiteratureRecord]:
        # Search papers using query, such as a drug name plus a gene. Return at most
        # max_results papers, enforcing the saved date limit inside this tool. Still
        # to build: the error below deliberately stops this unfinished function.
        raise NotImplementedError("A6: Europe PMC, with internally enforced cutoff")
