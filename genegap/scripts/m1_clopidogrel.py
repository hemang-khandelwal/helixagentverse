"""Milestone M1 (task 2.7): the whole chain for clopidogrel / CYP2C19, with zero AI.

    gene -> variants (2.1) -> trials (2.2) -> baseline demographics (2.3)
         -> ancestry mapping (2.4) -> target population (2.5) -> stats (2.6) -> gap table

Run it:
    python scripts/m1_clopidogrel.py                 # live APIs, cached after the first run
    python scripts/m1_clopidogrel.py --offline       # replay the recorded Phase 0 responses
    python scripts/m1_clopidogrel.py --gene VKORC1 --drug warfarin

Every number printed comes from genegap.stats operating on records fetched by the clients.
No language model is involved at any point in this script.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genegap import fixtures
from genegap.clients import ClinVarClient, CTGovClient, GnomadClient
from genegap.gap import compute_gap, format_gap_table, robust_across_mappings
from genegap.mapping import load_all_mappings, map_counts
from genegap.population import definitions_for_drug, load_target_population
from genegap.store import Store
from genegap.trials import collect_trials, pooled_race_counts
from genegap.variants import format_table, variants_for_gene


def banner(text: str) -> None:
    print(f"\n{'=' * 100}\n{text}\n{'=' * 100}")


def main() -> int:
    ap = argparse.ArgumentParser(description="GeneGap milestone M1: deterministic gap table, no LLM.")
    ap.add_argument("--gene", default="CYP2C19")
    ap.add_argument("--drug", default="clopidogrel")
    ap.add_argument("--population", default="us_acs_antiplatelet")
    ap.add_argument("--offline", action="store_true", help="replay recorded responses, no network")
    ap.add_argument("--max-trials", type=int, default=5)
    ap.add_argument("--target-carriers", type=float, default=30.0, help="carriers we would like each group to contribute")
    ap.add_argument("--k", type=int, default=10, help="threshold for P(fewer than k carriers)")
    ap.add_argument("--db", default=None, help="SQLite path (default: data/genegap.sqlite)")
    args = ap.parse_args()

    store = Store(args.db) if args.db else Store()

    if args.offline:
        gnomad, clinvar, ctgov, seeded = fixtures.offline_clients(store)
        banner("OFFLINE MODE: replaying recorded Phase 0 responses")
        for s in seeded:
            print(f"  replaying: {s}")
        print("  Anything not recorded is reported as unavailable rather than guessed.")
    else:
        gnomad = GnomadClient(store=store)
        clinvar = ClinVarClient(store=store)
        ctgov = CTGovClient(store=store)

    # ---- 2.1 gene -> filtered variant table -------------------------------------------
    banner(f"STEP 1 (task 2.1)  {args.gene}: variants and ancestry frequencies")
    table = variants_for_gene(args.gene, gnomad, clinvar)
    print(format_table(table))
    if not table.assessable:
        print(f"\nStopping: {args.gene} is not assessable with this pipeline. That refusal is the correct answer.")
        return 1
    if not table.variants:
        print("\nStopping: no variant passed the filters, so there is nothing to compute a gap for.")
        return 1

    # ---- 2.2 + 2.3 trials and their baseline tables -------------------------------------
    banner(f"STEP 2 (tasks 2.2, 2.3)  trials of {args.drug} and who took part")
    trials, trial_notes = collect_trials(ctgov, intervention=args.drug, max_trials=args.max_trials)
    for n in trial_notes:
        print(f"  - {n}")
    counts, used, pool_notes = pooled_race_counts(trials)
    for n in pool_notes:
        print(f"  - {n}")
    if not counts:
        print("\nStopping: no trial reported a race breakdown, so there is nothing to map.")
        print("The orchestrator would re-plan here and fall back to eligibility-only analysis.")
        return 1
    print(f"\n  Pooled self-identified race counts from {len(used)} trial(s):")
    for label, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"      {label:<46} {n:>8,}")
    print(f"      {'TOTAL':<46} {sum(counts.values()):>8,}")

    # ---- 2.5 target population ----------------------------------------------------------
    banner(f"STEP 3 (task 2.5)  target population: who will actually take {args.drug}")
    target = load_target_population(args.population)
    print(f"  {target.label}  ({target.geography})")
    for row in sorted(target.rows, key=lambda r: -r.share):
        mark = "ASSUMPTION" if row.is_assumption else "burden data"
        print(f"      {row.gnomad_group:<12} {row.share:>7.1%}   [{mark}]  {row.source}")
    for n in target.notes:
        print(f"  ! {n}")

    # The clinician's own definition table says whether this denominator is established.
    definitions = definitions_for_drug(args.drug)
    if definitions:
        print(f"\n  Definition table ({len(definitions)} indication rows for {args.drug}):")
        for d in definitions:
            state = "established" if d.distribution_established else "NOT ESTABLISHED"
            print(f"      {d.indication:<52} group mix: {state}")
        unestablished = [d for d in definitions if not d.distribution_established]
        if unestablished:
            print(f"\n  ! The clinician has recorded the group mix as NOT ESTABLISHED for "
                  f"{len(unestablished)} of {len(definitions)} indications.")
            print("  ! Every representation ratio below is therefore illustrative, not a measured gap.")
            print(f"  ! Needed: {unestablished[0].required_denominator_data}")
    else:
        print(f"\n  ! No target-population definition row for {args.drug} in data/target_population.csv.")

    # ---- 2.4 + 2.6 mapping and the gap table, under every mapping version ---------------
    mappings = load_all_mappings()
    banner(f"STEP 4 (tasks 2.4, 2.6)  gap table under {len(mappings)} ancestry mappings")
    print("  Trials report self-identified race; gnomAD reports genetic ancestry. These are")
    print("  different things, so the whole analysis is repeated under each mapping version")
    print("  and a finding only counts as robust if it holds under all of them.\n")

    for variant in table.variants:
        runs = {}
        for version, mapping in mappings.items():
            mapped = map_counts(counts, mapping)
            runs[version] = compute_gap(
                variant, mapped, target,
                inheritance=table.inheritance,
                target_carriers=args.target_carriers,
                k=args.k,
            )
        for version in sorted(runs):
            banner(f"{variant.rsid or variant.variant_id}  {variant.star_allele or ''}  mapping {version}")
            print(format_gap_table(runs[version]))

        # ---- the robustness verdict ----
        banner(f"ROBUSTNESS CHECK  {variant.rsid or variant.variant_id}")
        groups = sorted({g.group for r in runs.values() for g in r.result.groups})
        for g in groups:
            verdict = robust_across_mappings(runs, g)
            if verdict is None:
                word = "cannot tell (a ratio is missing under at least one mapping)"
            elif verdict:
                word = "UNDER-REPRESENTED under every mapping  <- robust finding"
            else:
                word = "mapping-dependent or adequately represented"
            print(f"  {g:<12} {word}")

    banner("DONE")
    print("  Every figure above came from a gnomAD, ClinVar or ClinicalTrials.gov record and was")
    print("  computed by genegap.stats. No language model produced any number in this run.")
    print("  This is a research-planning hypothesis for expert review, not clinical advice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
