"""Task 2.4: apply the ancestry mapping to trial counts.

Trials report self-identified race and ethnicity. gnomAD reports genetic ancestry groups.
These are not the same thing and there is no correct translation between them, so the
mapping is an explicit config file (config/ancestry_mapping_*.csv) written and cited by
the medical student, and every analysis is run under all available versions. A finding
only counts as robust if it survives all of them.

Two rules this module never bends:

  * "Unknown or Not Reported" is never reassigned to an ancestry group. It is carried
    through as a missing-data count.
  * A trial category with no row in the mapping file is left unmapped and reported, not
    quietly dropped or guessed at.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import ROOT

CONFIG_DIR = ROOT / "config"
MAPPING_GLOB = "ancestry_mapping_*.csv"
REQUIRED_COLUMNS = ["trial_category", "gnomad_group", "weight", "confidence", "source", "rationale"]
CONFIDENCE_LEVELS = {"high", "medium", "low"}

# The 10 top-level gnomAD v4 genetic ancestry groups. A mapping file may not invent others.
GNOMAD_GROUPS = frozenset({"afr", "ami", "amr", "asj", "eas", "fin", "mid", "nfe", "remaining", "sas"})

# Trial categories that record absent information. Never reassigned (D2).
MISSING_CATEGORIES = {"unknown or not reported", "unknown", "not reported", "missing"}

PLACEHOLDER = "PLACEHOLDER"


class MappingError(ValueError):
    """The mapping file is malformed. We stop loudly rather than guess a science decision."""


@dataclass
class MappingRow:
    trial_category: str
    gnomad_group: str          # "" means deliberately unassigned (missing-data category)
    weight: float
    confidence: str
    source: str
    rationale: str

    @property
    def is_placeholder(self) -> bool:
        return PLACEHOLDER in self.source.upper() or not self.source


@dataclass
class AncestryMapping:
    version: str
    path: Path
    rows: list[MappingRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def categories(self) -> dict[str, list[MappingRow]]:
        out: dict[str, list[MappingRow]] = {}
        for r in self.rows:
            out.setdefault(r.trial_category.strip().lower(), []).append(r)
        return out

    @property
    def placeholder_rows(self) -> list[MappingRow]:
        return [r for r in self.rows if r.is_placeholder and r.weight > 0]


@dataclass
class MappedCounts:
    """Trial headcounts expressed as gnomAD ancestry groups under one mapping version."""

    version: str
    groups: dict[str, float] = field(default_factory=dict)      # gnomAD group -> participants
    missing: int = 0                                            # unknown / not reported
    unmapped: dict[str, int] = field(default_factory=dict)      # category -> count, no mapping row
    total_reported: int = 0                                     # every participant in the input
    notes: list[str] = field(default_factory=list)

    @property
    def total_mapped(self) -> float:
        return sum(self.groups.values())

    @property
    def missing_fraction(self) -> Optional[float]:
        if not self.total_reported:
            return None
        return (self.missing + sum(self.unmapped.values())) / self.total_reported

    def shares(self) -> dict[str, float]:
        """Each group's share of the participants that could be mapped."""
        total = self.total_mapped
        if total <= 0:
            return {}
        return {g: n / total for g, n in self.groups.items()}


def load_mapping(path: Path) -> AncestryMapping:
    """Read one ancestry_mapping_<version>.csv and validate it."""
    version = path.stem.replace("ancestry_mapping_", "") or path.stem
    mapping = AncestryMapping(version=version, path=path)

    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        missing_cols = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing_cols:
            raise MappingError(f"{path.name} is missing columns: {missing_cols}")

        for n, raw in enumerate(reader, start=2):
            row = {k: (v or "").strip() for k, v in raw.items() if k}
            where = f"{path.name} line {n}"
            category = row["trial_category"]
            if not category:
                raise MappingError(f"{where}: trial_category is blank")

            group = row["gnomad_group"].lower()
            if group and group not in GNOMAD_GROUPS:
                raise MappingError(
                    f"{where} ({category}): '{group}' is not a gnomAD v4 group {sorted(GNOMAD_GROUPS)}"
                )

            try:
                weight = float(row["weight"] or 0.0)
            except ValueError:
                raise MappingError(f"{where} ({category}): weight must be a number, got {row['weight']!r}")
            if weight < 0.0 or weight > 1.0:
                raise MappingError(f"{where} ({category}): weight must be between 0 and 1, got {weight}")

            confidence = row["confidence"].lower()
            if confidence not in CONFIDENCE_LEVELS:
                raise MappingError(
                    f"{where} ({category}): confidence must be one of {sorted(CONFIDENCE_LEVELS)}, got {confidence!r}"
                )

            if category.strip().lower() in MISSING_CATEGORIES and (group or weight):
                raise MappingError(
                    f"{where}: '{category}' must never be assigned to an ancestry group "
                    "(leave gnomad_group blank and weight 0)."
                )

            mapping.rows.append(MappingRow(category, group, weight, confidence, row["source"], row["rationale"]))

    # Weights for one category should sum to 1, otherwise participants silently vanish or multiply.
    for category, rows in mapping.categories.items():
        if category in MISSING_CATEGORIES:
            continue
        total = sum(r.weight for r in rows)
        if abs(total - 1.0) > 1e-6:
            raise MappingError(
                f"{path.name}: weights for '{rows[0].trial_category}' sum to {total:.3f}, not 1.0"
            )

    for row in mapping.placeholder_rows:
        mapping.warnings.append(
            f"{row.trial_category} -> {row.gnomad_group}: weight has no citation and is marked "
            f"{PLACEHOLDER}. It must be sourced before any result is reported."
        )
    return mapping


def load_all_mappings(directory: Path = CONFIG_DIR) -> dict[str, AncestryMapping]:
    """Load every mapping version, keyed by version letter, in filename order."""
    paths = sorted(directory.glob(MAPPING_GLOB))
    if not paths:
        raise MappingError(f"No {MAPPING_GLOB} files in {directory}")
    return {m.version: m for m in (load_mapping(p) for p in paths)}


def map_counts(counts: dict[str, int], mapping: AncestryMapping) -> MappedCounts:
    """
    Spread trial race counts across gnomAD ancestry groups using one mapping version.

    Category matching is case-insensitive and ignores surrounding whitespace, because the
    registry's spelling of a category is not stable between trials.
    """
    out = MappedCounts(version=mapping.version)
    by_category = mapping.categories

    for label, count in counts.items():
        out.total_reported += count
        key = label.strip().lower()

        if key in MISSING_CATEGORIES or any(term in key for term in ("unknown", "not reported")):
            out.missing += count
            continue

        rows = by_category.get(key)
        if not rows:
            out.unmapped[label] = out.unmapped.get(label, 0) + count
            continue

        for row in rows:
            if not row.gnomad_group or row.weight == 0.0:
                out.missing += count
                continue
            out.groups[row.gnomad_group] = out.groups.get(row.gnomad_group, 0.0) + count * row.weight

    if out.missing:
        out.notes.append(
            f"{out.missing} participants ({out.missing / out.total_reported:.1%}) have no usable race "
            "category. They are counted as missing data and never assigned to an ancestry group."
        )
    if out.unmapped:
        listed = ", ".join(f"{k} ({v})" for k, v in sorted(out.unmapped.items()))
        out.notes.append(
            f"No mapping row for: {listed}. Ask the medical student to add these to "
            f"{mapping.path.name} or confirm they should stay out."
        )
    placeholders = mapping.placeholder_rows
    if placeholders:
        categories = sorted({r.trial_category for r in placeholders})
        out.notes.append(
            f"Mapping {mapping.version}: {len(placeholders)} weights are marked {PLACEHOLDER} and "
            f"carry no citation ({', '.join(categories)}). They must be sourced before any result "
            "based on this mapping is reported."
        )
    return out


def map_counts_all_versions(
    counts: dict[str, int],
    mappings: Optional[dict[str, AncestryMapping]] = None,
) -> dict[str, MappedCounts]:
    """Run the same counts through every mapping version, for the robustness check."""
    mappings = mappings if mappings is not None else load_all_mappings()
    return {version: map_counts(counts, m) for version, m in mappings.items()}
