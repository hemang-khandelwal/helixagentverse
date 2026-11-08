"""Task 3.1: the tools Gemini is allowed to call.

A "tool" is one of our own Python functions, wrapped so Gemini can ask for it by name.
Gemini only ever passes small, simple inputs (a gene name, a drug name). The tool does the real work with
our tested code and hands back plain facts and numbers. Gemini never touches the maths.

Tools keep what they find in a Workspace (a small notebook for one run), so a later tool can use an
earlier tool's result without Gemini having to copy big tables around (where it could make mistakes).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..gap import compute_gap, robust_across_mappings
from ..mapping import load_all_mappings, map_counts
from ..population import definitions_for_drug, load_target_population
from ..trials import collect_trials, pooled_race_counts
from ..variants import variants_for_gene


class ToolError(Exception):
    """A problem the model should be told about in plain words (bad input, step called too early, ...)."""


@dataclass
class Param:
    name: str
    type: str                       # "string" | "integer" | "number"
    description: str
    required: bool = True


@dataclass
class Tool:
    name: str
    description: str
    params: list[Param]
    fn: Callable[..., Any]

    def schema(self) -> dict:
        """The shape Gemini needs: a JSON-schema style description of the inputs."""
        return {
            "type": "OBJECT",
            "properties": {p.name: {"type": p.type.upper(), "description": p.description} for p in self.params},
            "required": [p.name for p in self.params if p.required],
        }

    def run(self, args: dict) -> Any:
        known = {p.name: p for p in self.params}
        extra = set(args) - set(known)
        if extra:
            raise ToolError(f"{self.name} does not take these inputs: {sorted(extra)}. It takes: {sorted(known)}")
        missing = [p.name for p in self.params if p.required and args.get(p.name) in (None, "")]
        if missing:
            raise ToolError(f"{self.name} needs these inputs: {missing}")
        clean = {}
        for k, v in args.items():
            t = known[k].type
            try:
                clean[k] = {"string": str, "integer": lambda x: int(float(x)), "number": float}[t](v)
            except (TypeError, ValueError):
                raise ToolError(f"{self.name}: {k} must be a {t}, got {v!r}")
        return self.fn(**clean)


@dataclass
class Workspace:
    """Everything one run has found so far, plus the data sources it should use."""
    gnomad: Any
    clinvar: Any
    ctgov: Any
    store: Any = None
    table: Any = None                    # VariantTable from lookup_variants
    drug: Optional[str] = None
    counts: dict = field(default_factory=dict)       # pooled race counts from fetch_trial_demographics
    trial_ids: list = field(default_factory=list)
    gap_runs: dict = field(default_factory=dict)     # variant_id -> {mapping version -> GapRun}


def _r(x: Any, digits: int = 5) -> Any:
    return round(x, digits) if isinstance(x, float) else x


def build_tools(ws: Workspace) -> list[Tool]:
    """The four tools of the pipeline, all sharing one Workspace."""

    # ---------------- 1. variants ----------------
    def lookup_variants(gene: str) -> dict:
        table = variants_for_gene(gene, ws.gnomad, ws.clinvar)
        ws.table = table
        out = {
            "gene": table.gene,
            "assessable": table.assessable,
            "inheritance": table.inheritance,
            "notes": table.notes[-12:],
            "variants": [],
        }
        for v in table.variants:
            out["variants"].append({
                "variant_id": v.variant_id, "rsid": v.rsid, "star_allele": v.star_allele,
                "function": v.function, "clinvar": v.classification, "caveat": v.curation_notes,
                "allele_frequency_by_group": {f.group: _r(f.af) for f in v.frequencies if f.af is not None},
                "groups_with_too_little_data": table.low_data.get(v.variant_id, []),
                "fact_ids": v.fact_ids,
            })
        if not table.assessable:
            out["message"] = "This gene cannot be assessed with this pipeline. Report that; do not guess."
        elif not table.variants:
            out["message"] = "No variant passed the filters, so there is nothing to compute a gap for."
        return out

    # ---------------- 2. trials ----------------
    def fetch_trial_demographics(drug: str, max_trials: int = 5) -> dict:
        trials, notes = collect_trials(ws.ctgov, intervention=drug, max_trials=max(1, min(int(max_trials), 10)))
        counts, used, pool_notes = pooled_race_counts(trials)
        ws.drug, ws.counts, ws.trial_ids = drug, counts, list(used)
        out = {
            "drug": drug,
            "trials_read": [t.nct_id for t in trials],
            "trials_with_race_data": list(used),
            "pooled_self_identified_race_counts": counts,
            "total_participants_with_race_data": sum(counts.values()),
            "notes": (notes + pool_notes)[-12:],
        }
        if not counts:
            out["message"] = "No trial reported a race breakdown. Report that the trial side cannot be assessed."
        return out

    # ---------------- 3. target population ----------------
    def get_target_population(drug: str, population_id: str = "us_acs_antiplatelet") -> dict:
        try:
            target = load_target_population(population_id)
        except Exception as e:
            raise ToolError(f"Could not load target population {population_id!r}: {e}")
        defs = definitions_for_drug(drug)
        unestablished = [d for d in defs if not d.distribution_established]
        return {
            "population_id": population_id,
            "label": target.label,
            "geography": target.geography,
            "shares": {g: _r(s) for g, s in target.shares.items()},
            "rows_that_are_assumptions": [r.gnomad_group for r in target.rows if r.is_assumption],
            "notes": target.notes,
            "clinician_definition_rows": len(defs),
            "indications_with_group_mix_NOT_ESTABLISHED": len(unestablished),
            "warning": ("The clinician recorded the group mix as NOT ESTABLISHED for some indications, so every ratio "
                        "is illustrative, not a measured gap." if unestablished else
                        ("No definition row for this drug." if not defs else "")),
        }

    # ---------------- 4. the gap ----------------
    COMPACT_KEYS = ["group", "n_trial", "carrier_freq", "carrier_ratio", "enrolment_ratio", "required_n", "insufficient_data"]

    def compute_gap_table(population_id: str = "us_acs_antiplatelet", target_carriers: float = 30.0,
                          rsid: str = "") -> dict:
        if ws.table is None:
            raise ToolError("Call lookup_variants first.")
        if not ws.table.assessable or not ws.table.variants:
            raise ToolError("lookup_variants found nothing assessable, so there is no gap to compute.")
        if not ws.counts:
            raise ToolError("Call fetch_trial_demographics first (and it must find race counts).")
        try:
            target = load_target_population(population_id)
        except Exception as e:
            raise ToolError(f"Could not load target population {population_id!r}: {e}")
        variants = [v for v in ws.table.variants if not rsid or v.rsid == rsid]
        if not variants:
            raise ToolError(f"No variant with rsid {rsid!r}. Known: {[v.rsid for v in ws.table.variants]}")
        detail = bool(rsid)   # one variant asked for -> give every column; otherwise a compact table per variant
        mappings = load_all_mappings()
        result = {"population_id": population_id, "mapping_versions": sorted(mappings),
                  "detail": "full" if detail else "compact (ask again with rsid for the full table of one variant)",
                  "variants": []}
        for variant in variants:
            runs = {}
            for version, mapping in mappings.items():
                mapped = map_counts(ws.counts, mapping)
                runs[version] = compute_gap(variant, mapped, target, inheritance=ws.table.inheritance,
                                            target_carriers=target_carriers)
            ws.gap_runs[variant.variant_id] = runs
            base = runs.get("A") or next(iter(runs.values()))
            groups, no_data = [], []
            for g in sorted(base.result.groups, key=lambda x: (x.carrier_ratio is None, x.carrier_ratio or 0.0)):
                full = g.model_dump()
                if g.insufficient_data and not detail:
                    no_data.append(g.group)
                    continue
                d = {k: _r(v, 4) for k, v in full.items() if detail or k in COMPACT_KEYS}
                if not detail:
                    d = {k: v for k, v in d.items() if v is not None}
                d["robust_underrepresented_all_mappings"] = robust_across_mappings(runs, g.group)
                groups.append(d)
            entry = {
                "variant_id": variant.variant_id, "rsid": variant.rsid, "star_allele": variant.star_allele,
                "function": variant.function,
                "groups_under_mapping_A": groups,
                "groups_with_insufficient_data": no_data,
                "fact_ids": base.result.fact_ids[:6],
            }
            if detail:
                entry["assumptions"] = base.notes[-8:]
            result["variants"].append(entry)
        return result

    return [
        Tool("lookup_variants",
             "Look up the important genetic variants of a gene and how common each one is in each ancestry group (gnomAD), "
             "with ClinVar labels. Say if the gene cannot be assessed. Call this first.",
             [Param("gene", "string", "Gene symbol, e.g. CYP2C19")], lookup_variants),
        Tool("fetch_trial_demographics",
             "Find completed clinical trials of a drug on ClinicalTrials.gov and add up the self-identified race "
             "of the people enrolled.",
             [Param("drug", "string", "Drug name, e.g. clopidogrel"),
              Param("max_trials", "integer", "How many trials to read (1 to 10). Default 5.", required=False)],
             fetch_trial_demographics),
        Tool("get_target_population",
             "Show who is expected to take the drug in real life (group shares) and any warning that the group mix "
             "is not established.",
             [Param("drug", "string", "Drug name"),
              Param("population_id", "string", "Population table id. Default us_acs_antiplatelet.", required=False)],
             get_target_population),
        Tool("compute_gap_table",
             "Compute the gap table: for each variant and ancestry group, the carrier frequency with a confidence "
             "interval, the enrolment and carrier ratios, and how many more people are needed. Repeats the analysis "
             "under every ancestry mapping and reports whether a finding is robust. Needs lookup_variants and "
             "fetch_trial_demographics to have been called first.",
             [Param("population_id", "string", "Population table id. Default us_acs_antiplatelet.", required=False),
              Param("target_carriers", "number", "Carriers wanted per group. Default 30.", required=False),
              Param("rsid", "string", "Optional: one variant's rsID (e.g. rs4244285) to get its full table.", required=False)],
             compute_gap_table),
    ]
