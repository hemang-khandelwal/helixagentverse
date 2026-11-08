"""Task 2.1: turn a gene name into a short, filtered table of variants.

gene -> rules (from config/variant_rules.csv) -> gnomAD frequencies -> ClinVar label -> filters -> table
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from .clients.base import ApiError
from .config import ROOT
from .schemas import Variant

CONFIG_DIR = ROOT / "config"
RULES_PATH = CONFIG_DIR / "variant_rules.csv"
DETAILS_COLUMNS = ["gene", "rsid", "star_allele", "function", "copies_needed", "source_link", "notes"]
REQUIRED_COLUMNS = ["gene", "assessable", "reason_if_not", "inheritance", "rsids",
                    "min_af", "min_an", "function_note", "source", "verified_by"]
INHERITANCE = {"dominant", "recessive", "x_linked"}


class RuleError(ValueError):
    """The rules file is missing something. We stop loudly instead of guessing a science decision."""


@dataclass
class GeneRule:
    gene: str
    assessable: bool
    reason_if_not: str
    inheritance: str
    rsids: list[str]
    min_af: float          # ignore a variant unless at least one group has this frequency or more
    min_an: int            # a group with fewer gene copies than this is "not enough data"
    function_note: str
    source: str
    verified_by: str


@dataclass
class VariantDetail:
    """One row of the medical student's variant file (config/variant_details*.csv)."""
    gene: str
    rsid: str
    star_allele: str
    function: str
    copies_needed: str
    source_link: str
    notes: str


@dataclass
class FilterOutcome:
    keep: bool
    reason: str
    low_data_groups: list[str] = field(default_factory=list)


@dataclass
class VariantTable:
    gene: str
    assessable: bool
    inheritance: str = "dominant"
    variants: list[Variant] = field(default_factory=list)
    low_data: dict[str, list[str]] = field(default_factory=dict)   # variant_id -> groups with too little data
    notes: list[str] = field(default_factory=list)                 # plain-English log of everything that happened


# ---------- 1. read the rules ----------
def load_rules(path: Path = RULES_PATH) -> dict[str, GeneRule]:
    rules: dict[str, GeneRule] = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise RuleError(f"{path.name} is missing columns: {missing}")
        for n, row in enumerate(reader, start=2):
            gene = (row["gene"] or "").strip()
            where = f"{path.name} line {n} ({gene or 'no gene'})"
            if not gene:
                raise RuleError(f"{where}: gene is blank")
            assessable = (row["assessable"] or "").strip().lower() == "yes"
            inheritance = (row["inheritance"] or "").strip().lower()
            if inheritance not in INHERITANCE:
                raise RuleError(f"{where}: inheritance must be one of {sorted(INHERITANCE)}, got {inheritance!r}")
            rsids = [x.strip() for x in (row["rsids"] or "").split(";") if x.strip()]
            min_af = min_an = None
            if assessable:
                try:
                    min_af, min_an = float(row["min_af"]), int(row["min_an"])
                except ValueError:
                    raise RuleError(f"{where}: min_af and min_an must be numbers")
            elif not (row["reason_if_not"] or "").strip():
                raise RuleError(f"{where}: a gene marked 'no' needs a reason_if_not")
            rules[gene.upper()] = GeneRule(gene, assessable, (row["reason_if_not"] or "").strip(), inheritance, rsids,
                                           min_af or 0.0, min_an or 0, (row["function_note"] or "").strip(),
                                           (row["source"] or "").strip(), (row["verified_by"] or "").strip())
    return rules


def load_variant_details(paths: list[Path] | None = None) -> dict[str, dict[str, VariantDetail]]:
    """Reads every config/variant_details*.csv. Returns {GENE: {rsid: VariantDetail}} in file order.
    Every row needs an rsID, a function label and a source link: no source, no row."""
    paths = paths if paths is not None else sorted(CONFIG_DIR.glob("variant_details*.csv"))
    out: dict[str, dict[str, VariantDetail]] = {}
    for path in paths:
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            missing = [c for c in DETAILS_COLUMNS if c not in (reader.fieldnames or [])]
            if missing:
                raise RuleError(f"{path.name} is missing columns: {missing}")
            for n, row in enumerate(reader, start=2):
                row = {k: (v or "").strip() for k, v in row.items()}
                where = f"{path.name} line {n}"
                if not row["gene"]:
                    raise RuleError(f"{where}: gene is blank")
                if not re.fullmatch(r"rs\d+", row["rsid"]):
                    raise RuleError(f"{where}: rsid must look like rs12345, got {row['rsid']!r}")
                for col in ("function", "source_link"):
                    if not row[col]:
                        raise RuleError(f"{where} ({row['rsid']}): {col} is blank")
                genes = out.setdefault(row["gene"].upper(), {})
                if row["rsid"] in genes:
                    raise RuleError(f"{where}: {row['rsid']} appears twice for {row['gene']}")
                genes[row["rsid"]] = VariantDetail(**{c: row[c] for c in DETAILS_COLUMNS})
    return out


# ---------- 2. the filter (no internet, easy to test) ----------
def apply_filters(variant: Variant, rule: GeneRule) -> FilterOutcome:
    low = [f.group for f in variant.frequencies if f.an < rule.min_an]
    usable = [f for f in variant.frequencies if f.an >= rule.min_an and f.af is not None]
    if not usable:
        return FilterOutcome(False, "no ancestry group has enough data", low)
    top = max(usable, key=lambda f: f.af)
    if top.af < rule.min_af:
        return FilterOutcome(False, f"too rare: highest is {top.af:.3%} in '{top.group}', below the {rule.min_af:.3%} cut-off", low)
    return FilterOutcome(True, f"kept: {top.af:.2%} in '{top.group}'", low)


# ---------- 3. put it together ----------
def variants_for_gene(gene: str, gnomad, clinvar, rules: dict[str, GeneRule] | None = None,
                      details: dict[str, dict[str, VariantDetail]] | None = None) -> VariantTable:
    rules = rules if rules is not None else load_rules()
    details = details if details is not None else load_variant_details()
    rule = rules.get(gene.strip().upper())
    if rule is None:
        return VariantTable(gene, False, notes=[
            f"No rule for {gene}. Ask the medical student to add a row to config/variant_rules.csv."])
    if not rule.assessable:
        return VariantTable(rule.gene, False, rule.inheritance, notes=[f"Cannot assess {rule.gene} yet: {rule.reason_if_not}"])

    table = VariantTable(rule.gene, True, rule.inheritance)   # use the rules file's spelling, not the typed one
    if not rule.verified_by:
        table.notes.append(f"Warning: the {gene} rsIDs have not been verified by the medical student yet.")

    gene_details = details.get(rule.gene.upper(), {})
    rsids = list(gene_details) or rule.rsids
    if gene_details:
        table.notes.append(f"Using {len(rsids)} variants from the medical student's file for {rule.gene}.")
    elif rsids:
        table.notes.append(f"No variant file for {rule.gene}: using rsIDs from variant_rules.csv, so no star-allele or function labels.")
    else:
        return VariantTable(rule.gene, False, rule.inheritance, notes=[
            f"No rsIDs for {rule.gene}. Add rows to a config/variant_details*.csv file or the rsids column."])

    for rsid in rsids:
        # Prefer the tolerant call where the client offers it: one unavailable allele of a
        # multiallelic rsID should not throw away the alleles that did resolve.
        detailed = getattr(gnomad, "variants_for_rsid_detailed", None)
        try:
            if detailed is not None:
                found, problems = detailed(rsid, gene=rule.gene)
                for p in problems:
                    table.notes.append(f"{rsid}: {p}")
            else:
                found = gnomad.variants_for_rsid(rsid, gene=rule.gene)
        except ApiError as e:
            table.notes.append(f"{rsid}: gnomAD lookup failed ({e})")
            continue
        if not found:
            table.notes.append(f"{rsid}: not found in gnomAD")
            continue
        if len(found) > 1:
            table.notes.append(f"{rsid}: matches {len(found)} alleles ({', '.join(v.variant_id for v in found)}). "
                               "Checking each one; the medical student should say which is meant.")

        labels, label_fact_ids = [], []
        try:
            for fact in clinvar.lookup_rsid(rsid):
                label_fact_ids.append(fact.id)
                if fact.value.get("classification"):
                    labels.append(fact.value["classification"])
        except ApiError as e:
            table.notes.append(f"{rsid}: ClinVar lookup failed ({e}). Continuing without a label.")

        d = gene_details.get(rsid)
        for v in found:
            if d:
                v.star_allele, v.function = d.star_allele or None, d.function
                v.copies_needed, v.curation_notes = d.copies_needed or None, d.notes or None
            v.classification = "; ".join(sorted(set(labels))) or None
            v.fact_ids = v.fact_ids + label_fact_ids
            outcome = apply_filters(v, rule)
            table.notes.append(f"{rsid} / {v.variant_id}: {outcome.reason}")
            if outcome.keep:
                table.variants.append(v)
                if outcome.low_data_groups:
                    table.low_data[v.variant_id] = outcome.low_data_groups
    return table


# ---------- 4. a readable printout ----------
def format_table(t: VariantTable) -> str:
    lines = [f"Gene: {t.gene}   assessable: {'yes' if t.assessable else 'NO'}   inheritance: {t.inheritance}"]
    for v in t.variants:
        low = set(t.low_data.get(v.variant_id, []))
        label = f"{v.star_allele} " if v.star_allele else ""
        lines.append(f"\n  {label}{v.variant_id}  ({v.rsid})   {v.function or 'function not given'}   ClinVar: {v.classification or 'no label found'}")
        if v.curation_notes:
            lines.append(f"      Caveat: {v.curation_notes}")
        for f in v.frequencies:
            shown = "not enough data" if f.group in low else f"{f.af:.2%}"
            lines.append(f"      {f.group:<10} {shown:<16} (counted {f.ac} of {f.an})")
    lines.append("\nWhat happened:")
    lines += [f"  - {n}" for n in t.notes]
    return "\n".join(lines)
