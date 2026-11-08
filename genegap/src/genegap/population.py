"""Task 2.5: the target-population table.

This answers "who will actually use this drug?". It is the denominator of every
representation ratio, so it is curated by hand with a source and a short quote per row
(deliverable D5) rather than derived by the system.

Each row is one gnomAD ancestry group's share of the target population. Where no
disease-burden source exists for a group, the row is marked as an ASSUMPTION and the
whole table reports itself as assumption-based, so the report can say so plainly.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import ROOT
from .mapping import GNOMAD_GROUPS

DATA_DIR = ROOT / "data"
TARGET_POPULATION_PATH = DATA_DIR / "target_population.csv"
REQUIRED_COLUMNS = [
    "population_id", "label", "gnomad_group", "share",
    "source", "quote", "year", "geography", "basis", "caveat",
]
# "burden" = taken from a disease-burden source; "assumption" = general population mix used instead.
BASIS_VALUES = {"burden", "assumption"}
MAX_QUOTE_WORDS = 15


class PopulationError(ValueError):
    """The target-population file is malformed or unsourced. No source, no row."""


@dataclass
class PopulationRow:
    population_id: str
    label: str
    gnomad_group: str
    share: float
    source: str
    quote: str
    year: str
    geography: str
    basis: str
    caveat: str

    @property
    def is_assumption(self) -> bool:
        return self.basis == "assumption"


@dataclass
class TargetPopulation:
    """One named target population, e.g. 'UK adults needing antiplatelet therapy'."""

    population_id: str
    label: str
    rows: list[PopulationRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def shares(self) -> dict[str, float]:
        return {r.gnomad_group: r.share for r in self.rows}

    @property
    def geography(self) -> str:
        return self.rows[0].geography if self.rows else ""

    @property
    def assumption_share(self) -> float:
        """Share of the population whose figure is an assumption rather than disease-burden data."""
        return sum(r.share for r in self.rows if r.is_assumption)

    @property
    def is_assumption_based(self) -> bool:
        return self.assumption_share > 0.0


def _check_quote(quote: str, where: str) -> None:
    if len(quote.split()) > MAX_QUOTE_WORDS:
        raise PopulationError(
            f"{where}: quote is {len(quote.split())} words; keep it under {MAX_QUOTE_WORDS} (D5 / licence rules)"
        )


def load_target_populations(path: Path = TARGET_POPULATION_PATH) -> dict[str, TargetPopulation]:
    """Read data/target_population.csv into {population_id: TargetPopulation}."""
    if not path.exists():
        raise PopulationError(f"{path} does not exist. It is medical-student deliverable D5.")

    populations: dict[str, TargetPopulation] = {}
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        missing_cols = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing_cols:
            raise PopulationError(f"{path.name} is missing columns: {missing_cols}")

        for n, raw in enumerate(reader, start=2):
            row = {k: (v or "").strip() for k, v in raw.items() if k}
            where = f"{path.name} line {n}"

            pid = row["population_id"]
            if not pid:
                raise PopulationError(f"{where}: population_id is blank")

            group = row["gnomad_group"].lower()
            if group not in GNOMAD_GROUPS:
                raise PopulationError(
                    f"{where}: '{group}' is not a gnomAD v4 ancestry group {sorted(GNOMAD_GROUPS)}"
                )

            try:
                share = float(row["share"])
            except ValueError:
                raise PopulationError(f"{where}: share must be a number, got {row['share']!r}")
            if not 0.0 <= share <= 1.0:
                raise PopulationError(f"{where}: share must be between 0 and 1, got {share}")

            basis = row["basis"].lower()
            if basis not in BASIS_VALUES:
                raise PopulationError(f"{where}: basis must be one of {sorted(BASIS_VALUES)}, got {basis!r}")

            if not row["source"]:
                raise PopulationError(f"{where}: source is blank. No source, no row.")
            _check_quote(row["quote"], where)

            pop = populations.get(pid)
            if pop is None:
                pop = TargetPopulation(population_id=pid, label=row["label"] or pid)
                populations[pid] = pop
            if any(r.gnomad_group == group for r in pop.rows):
                raise PopulationError(f"{where}: '{group}' appears twice for population '{pid}'")

            pop.rows.append(PopulationRow(
                population_id=pid, label=row["label"], gnomad_group=group, share=share,
                source=row["source"], quote=row["quote"], year=row["year"],
                geography=row["geography"], basis=basis, caveat=row["caveat"],
            ))

    for pid, pop in populations.items():
        total = sum(r.share for r in pop.rows)
        if abs(total - 1.0) > 0.01:
            raise PopulationError(f"{path.name}: shares for '{pid}' sum to {total:.3f}, not 1.0")
        if pop.is_assumption_based:
            pop.notes.append(
                f"{pop.assumption_share:.0%} of this target population is an ASSUMPTION taken from the "
                "general population mix, not from disease-burden data. Treat the representation "
                "ratios as indicative."
            )
    return populations


def load_target_population(population_id: str, path: Path = TARGET_POPULATION_PATH) -> TargetPopulation:
    """Load one named target population, failing with the list of valid IDs if it is absent."""
    populations = load_target_populations(path)
    pop = populations.get(population_id)
    if pop is None:
        raise PopulationError(
            f"No target population '{population_id}' in {path.name}. Available: {sorted(populations)}"
        )
    return pop
