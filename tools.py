"""A owns this module. A4/A5/A6/A8 implement the retrieval methods.

All methods are stubs. They raise rather than pretending evidence was found.
Bind the historical cutoff when constructing the toolset; never expose a
per-call date override to the model. A6 must filter every retrieved citation.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from contracts import (
    AnalogRecord, CompoundRecord, FrequencyRecord, LiteratureRecord,
    PgxAssociationRecord,
)


class HelixTools:
    def __init__(
        self, *, max_date: date | None,
        data_dir: Path = Path("data"), cache_dir: Path = Path("cache"),
    ) -> None:
        self.max_date = max_date
        self.data_dir = data_dir
        self.cache_dir = cache_dir

    def fetch_compound(self, name_or_smiles: str) -> CompoundRecord:
        raise NotImplementedError("A5: load compound data")

    def similar_compounds(self, smiles: str, k: int = 5) -> list[AnalogRecord]:
        raise NotImplementedError("A4: RDKit similarity")

    def fetch_pgx(self, gene_or_drug: str) -> list[PgxAssociationRecord]:
        raise NotImplementedError("A5: load and date-filter association records")

    def fetch_frequency(self, variant: str, ancestry: str) -> FrequencyRecord | None:
        raise NotImplementedError("A5: load population frequencies")

    def fetch_literature(self, query: str, max_results: int = 20) -> list[LiteratureRecord]:
        raise NotImplementedError("A6: Europe PMC, with internally enforced cutoff")
