"""Execute a bounded analysis plan using the existing data and statistics modules.

The output is a review draft. Literature searches provide candidate references only;
clinical evidence grading, an independent critic, and human sign-off remain pending.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

from ..clients import ApiError, ClinVarClient, CTGovClient, EuropePmcClient, GnomadClient
from ..fixtures import offline_clients
from ..gap import DEFAULT_TARGET_CARRIERS, compute_gap, robust_across_mappings
from ..mapping import MappingError, load_all_mappings, map_counts
from ..population import PopulationError, load_target_population
from ..schemas import Fact, Flag
from ..store import Store
from ..variants import RuleError, load_rules, variants_for_gene
from ..trials import collect_trials, pooled_race_counts

MAX_GENES = 5
MAX_TRIALS = 5
REQUIRED_MAPPINGS = ("A", "B", "C")
PENDING_REVIEW = (
    "These are draft coverage hypotheses. Clinical evidence grading and an independent "
    "critic review are pending; no finding has human approval."
)


def _field(plan: Any, name: str, default: Any = None) -> Any:
    return plan.get(name, default) if isinstance(plan, dict) else getattr(plan, name, default)


def _unique(items):
    return list(dict.fromkeys(items))


def execute_plan(
    plan: Any, *, store: Store, run_id: str,
    on_event: Callable[[str, dict], None], offline: bool = False,
) -> dict:
    """Return a provenance-backed ReviewDraft dict without simulating review clearance."""
    drug = str(_field(plan, "drug", "")).strip().lower()
    requested_genes = _unique(str(g).strip().upper() for g in _field(plan, "genes", []) if str(g).strip())
    genes = requested_genes[:MAX_GENES]
    limitations = [PENDING_REVIEW]
    flags = []
    title = f"GeneGap draft coverage analysis: {drug or ', '.join(genes) or 'query'}"

    def finish():
        return {"run_id": run_id, "title": title, "flags": flags,
                "limitations": _unique(limitations)}

    def replan(reason: str, mode: str = "missing-data"):
        limitations.append(reason)
        on_event("replan", {"mode": mode, "reason": reason})

    action = _field(plan, "action", "clarify")
    if action != "assess":
        reason = (
            "This query requires clarification of the drug or gene before a coverage analysis."
            if action == "clarify" else
            "This query cannot be assessed as a trial-coverage question; individual treatment "
            "and dosing decisions are outside this analysis."
        )
        replan(reason, "clarification" if action == "clarify" else "out-of-scope")
        return finish()
    if not genes:
        replan("No gene was selected for analysis.")
        return finish()
    if len(requested_genes) > MAX_GENES:
        limitations.append(f"The run is bounded to the first {MAX_GENES} selected genes.")

    try:
        rules = load_rules()
    except (OSError, RuleError) as exc:
        replan(f"The curated variant rules could not be loaded: {exc}")
        return finish()

    eligible = []
    for gene in genes:
        rule = rules.get(gene)
        if rule is None:
            replan(f"No curated variant rule exists for {gene}; add a verified rule before assessment.")
        elif not rule.assessable:
            replan(f"Cannot assess {gene}: {rule.reason_if_not}", "eligibility-only")
        else:
            eligible.append(gene)
    if not eligible:
        return finish()

    population_id = _field(plan, "population_id")
    if not population_id:
        replan(f"No target-population denominator was selected for {drug or 'this drug'}. "
               "An appropriate curated population is required before representation ratios can be computed.")
        return finish()
    if "acs_antiplatelet" in population_id and drug != "clopidogrel":
        replan(f"Target population {population_id} is curated for the clopidogrel ACS demo, "
               f"so it cannot be used as the denominator for {drug or 'an unspecified drug'}.")
        return finish()
    try:
        target = load_target_population(population_id)
        mappings = load_all_mappings()
    except (OSError, PopulationError, MappingError) as exc:
        replan(f"The target population or ancestry mappings are unavailable: {exc}")
        return finish()
    if not drug:
        replan("No drug was selected, so a trial search cannot be performed.")
        return finish()

    limitations.extend(target.notes)
    target_ids = []
    for row in target.rows:
        target_ids.append(store.put_fact(Fact(
            field="target_population_row", value=asdict(row), source="GeneGap.population",
            record_id=f"{target.population_id}:{row.gnomad_group}", method="curated",
            quote=row.quote or None,
            note=" ".join([row.caveat, *target.notes]).strip() or None,
        )))
    mapping_ids = {}
    mapped_counts = {}
    for version, mapping in mappings.items():
        mapping_ids[version] = [store.put_fact(Fact(
            field="ancestry_mapping_row", value=asdict(row), source="GeneGap.mapping",
            record_id=f"{version}:{i}", method="curated",
            note=("PLACEHOLDER assumption; the weight requires a verified source. "
                  if row.is_placeholder else "") + row.rationale,
        )) for i, row in enumerate(mapping.rows)]
        limitations.extend(mapping.warnings)

    missing_mappings = sorted(set(REQUIRED_MAPPINGS) - set(mappings))
    if missing_mappings:
        replan(f"Missing ancestry mappings {', '.join(missing_mappings)}; no A/B/C-robust coverage "
               "hypothesis can be returned.")

    if offline:
        try:
            gnomad, clinvar, ctgov, seeded = offline_clients(store)
        except (OSError, ValueError) as exc:
            replan(f"Recorded offline data could not be loaded: {exc}")
            return finish()
        limitations.append("Offline mode replays the recorded clopidogrel/CYP2C19 responses; "
                           "other records are unavailable unless already cached.")
        on_event("tool_return", {"stage": "offline-cache", "records": seeded})
    else:
        gnomad, clinvar, ctgov = GnomadClient(store=store), ClinVarClient(store=store), CTGovClient(store=store)

    on_event("tool_call", {"stage": "trial-demographics", "drug": drug, "max_trials": MAX_TRIALS})
    trials, trial_notes = collect_trials(ctgov, intervention=drug, max_trials=MAX_TRIALS)
    limitations.extend(trial_notes)
    counts, used_nct_ids, pooled_notes = pooled_race_counts(trials)
    limitations.extend(pooled_notes)
    if not counts or not sum(counts.values()):
        replan("No usable trial race counts were available. Eligibility-only review is needed; "
               "representation ratios cannot be computed.", "eligibility-only")
        return finish()
    trial_ids = _unique(
        fact_id for trial in trials if trial.nct_id in used_nct_ids
        for fact_id in [*trial.fact_ids, *(fid for demo in trial.baseline for fid in demo.fact_ids)]
    )
    pooled_id = store.put_fact(Fact(
        field="pooled_race_counts", source="GeneGap.trials", record_id=run_id, method="computed",
        value={"counts": counts, "nct_ids": used_nct_ids, "fact_ids": trial_ids},
        note=" ".join([*trial_notes, *pooled_notes]),
    ))
    for version, mapping in mappings.items():
        mapped_counts[version] = map_counts(counts, mapping)
        limitations.extend(mapped_counts[version].notes)

    on_event("tool_call", {"stage": "variants", "genes": eligible})
    for gene in eligible:
        rule = rules[gene]
        try:
            table = variants_for_gene(gene, gnomad, clinvar, rules=rules)
        except (OSError, RuleError) as exc:
            replan(f"Curated variant data for {gene} could not be loaded: {exc}")
            continue
        limitations.extend(table.notes)
        if not table.variants:
            replan(f"No usable variants were available for {gene}; no carrier-coverage figures were invented.")
            continue
        rule_id = store.put_fact(Fact(
            field="gene_rule", value=asdict(rule), source="GeneGap.variants", record_id=gene,
            method="curated", note=rule.function_note or None,
        ))
        candidate_facts = []
        if not offline:
            try:
                candidate_facts = EuropePmcClient(store=store).search(f"{drug} AND {gene}", page_size=3)
            except (ApiError, ValueError, KeyError, TypeError) as exc:
                limitations.append(f"Candidate literature search for {gene} was unavailable: {exc}")
        if candidate_facts:
            limitations.append("Literature search hits are candidate references only; their titles "
                               "and metadata have not been graded as clinical evidence.")
        for variant in table.variants:
            runs = {}
            computed_ids = []
            provenance = _unique([*variant.fact_ids, *(f.fact_id for f in variant.frequencies),
                                  *trial_ids, pooled_id, rule_id, *target_ids,
                                  *(fid for ids in mapping_ids.values() for fid in ids),
                                  *(f.id for f in candidate_facts)])
            # An unresolved source ID is a missing input, never silently dropped provenance.
            missing_ids = [fid for fid in provenance if store.get_fact(fid) is None]
            if missing_ids:
                replan(f"Variant {variant.variant_id} has unresolved provenance ({', '.join(missing_ids)}); "
                       "no coverage hypothesis was created.")
                continue
            for version, mapped in mapped_counts.items():
                run = compute_gap(variant, mapped, target, inheritance=table.inheritance,
                                  min_an=rule.min_an, target_carriers=DEFAULT_TARGET_CARRIERS)
                run.result.fact_ids = provenance
                runs[version] = run
                notes = _unique([*trial_notes, *pooled_notes, *table.notes, *run.notes,
                                 "Clinical evidence grading and independent critic review are pending."])
                limitations.extend(run.notes)
                computed_ids.append(store.put_fact(Fact(
                    field="gap_result", source="GeneGap.stats", method="computed",
                    record_id=f"{run_id}:{variant.variant_id}:{version}",
                    value={"result": run.result.model_dump(mode="json"), "notes": notes,
                           "target_label": run.target_label, "target_carriers": DEFAULT_TARGET_CARRIERS},
                    note="Computed by genegap.gap.compute_gap; " + " ".join(run.notes),
                )))
            if missing_mappings:
                continue
            required_runs = {version: runs[version] for version in REQUIRED_MAPPINGS}
            for row in required_runs["A"].result.groups:
                if robust_across_mappings(required_runs, row.group) is not True:
                    continue
                flags.append(Flag(
                    variant_id=variant.variant_id,
                    claim=f"For {drug}, trial coverage of {gene} {variant.rsid or variant.variant_id} "
                          f"in group {row.group} suggests lower expected-carrier representation under "
                          "mappings A, B and C, subject to mapping and target-population denominator "
                          "assumptions. This is a draft coverage hypothesis.",
                    evidence_tier=None, citations=[f.url or f.record_id for f in candidate_facts],
                    fact_ids=_unique([*provenance, *computed_ids]),
                    robust_across_mappings=True, status="draft",
                ).model_dump(mode="json"))

    on_event("tool_return", {"stage": "coverage-analysis", "draft_flags": len(flags),
                             "mappings": list(mappings), "trial_ids": used_nct_ids})
    if not flags and not missing_mappings:
        limitations.append("No draft coverage hypothesis met the under-representation criterion "
                           "under all three mappings A, B and C with the available data.")
    return finish()
