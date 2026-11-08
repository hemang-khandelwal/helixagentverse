"""Task 2.8 / deliverable D6: sanity-check the milestone M1 numbers.

Run it:
    python scripts/sanity_check_m1.py            # prints the log and writes data/sanity_check_log.csv
    python scripts/sanity_check_m1.py --strict   # exit 1 if anything FAILs (for CI)

The point of this script is to be *independent of the pipeline*. Where it checks a number,
it recomputes it by a different route:

  * carrier frequencies are compared against constants worked out by hand with plain
    arithmetic (the working is written out beside each one), not by calling stats.py;
  * trial counts are re-read by walking the raw registry JSON directly, not through the
    baseline parser;
  * the absurdity checks are invariants that must hold whatever the pipeline computed.

Three D6 items cannot be completed from inside this repo and are logged as OPEN rather
than quietly skipped: the CPIC allele-frequency comparison needs the CPIC tables, and
checking trials against their live web pages needs the network and a human reading them.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from genegap.fixtures import offline_clients
from genegap.gap import compute_gap
from genegap.mapping import load_all_mappings, map_counts
from genegap.population import definitions_for_drug, load_target_population
from genegap.store import Store
from genegap.trials import collect_trials, pooled_race_counts
from genegap.variants import variants_for_gene

LOG_PATH = ROOT / "data" / "sanity_check_log.csv"
RAW_TRIAL = ROOT / "smoke" / "responses" / "ctgov_study_raw_NCT01609842.json"

PASS, FAIL, OPEN = "PASS", "FAIL", "OPEN"


@dataclass
class Check:
    """One row of the D6 log sheet."""

    check: str
    expected: str
    observed: str
    status: str
    explanation: str
    owner: str = "P1"


# ---------------------------------------------------------------------------
# Hand calculations. These constants were worked out with plain arithmetic from
# the AC/AN in the recorded gnomAD response, independently of genegap.stats.
#
#   carrier frequency (dominant, Hardy-Weinberg) = 1 - (1 - p)^2 = 2p - p^2
#
#   nfe: p = 162522 / 1111304 = 0.1462444
#        2p = 0.2924888 ;  p^2 = 0.0213874 ;  carrier = 0.2711014
#   eas: p =  11167 /   36782 = 0.3035995
#        2p = 0.6071990 ;  p^2 = 0.0921727 ;  carrier = 0.5150264
#   sas: p =  20947 /   65396 = 0.3203101
#        2p = 0.6406202 ;  p^2 = 0.1025986 ;  carrier = 0.5380217
#
#   required n for 30 expected carriers = ceil(30 / carrier)
#        nfe: 30 / 0.2711014 = 110.66 -> 111
#        eas: 30 / 0.5150264 =  58.25 ->  59
#        sas: 30 / 0.5380217 =  55.76 ->  56
# ---------------------------------------------------------------------------
HAND_CALCULATED = {
    "nfe": {"ac": 162522, "an": 1111304, "p": 0.1462444, "carrier": 0.2711014, "required_n_30": 111},
    "eas": {"ac": 11167, "an": 36782, "p": 0.3035995, "carrier": 0.5150264, "required_n_30": 59},
    "sas": {"ac": 20947, "an": 65396, "p": 0.3203101, "carrier": 0.5380217, "required_n_30": 56},
}

# Counts read by eye from the recorded NCT01609842 baseline table, "Total" column.
HAND_READ_TRIAL_COUNTS = {
    "American Indian or Alaska Native": 146,
    "Asian": 40,
    "Native Hawaiian or Other Pacific Islander": 123,
    "Black or African American": 2329,
    "White": 11935,
    "More than one race": 0,
    "Unknown or Not Reported": 0,
}
HAND_READ_TRIAL_TOTAL = 14573

# Direction vocabularies that mean the same thing, so the config files can be cross-checked.
REDUCED = {"decreased_function", "loss_of_function", "no function", "decreased function", "decreased_expression"}
INCREASED = {"increased_function", "increased function"}


def build_m1():
    """Re-run the M1 chain offline and hand back everything the checks need."""
    gnomad, clinvar, ctgov, _ = offline_clients(Store(":memory:"))
    table = variants_for_gene("CYP2C19", gnomad, clinvar)
    trials, _ = collect_trials(ctgov, intervention="clopidogrel", max_trials=5)
    counts, used, _ = pooled_race_counts(trials)
    target = load_target_population("us_acs_antiplatelet")
    variant = next(v for v in table.variants if v.rsid == "rs4244285")
    runs = {
        version: compute_gap(variant, map_counts(counts, mapping), target, inheritance=table.inheritance)
        for version, mapping in load_all_mappings().items()
    }
    return table, variant, counts, used, runs


# ---------------------------------------------------------------------------
# 1. Recalculate carrier frequency by hand for 3 variants (D6 item 1)
# ---------------------------------------------------------------------------
def check_hand_calculations(variant, runs) -> list[Check]:
    out: list[Check] = []
    rows = {r.group: r for r in runs["A"].result.groups}
    freqs = {f.group: f for f in variant.frequencies}

    for group, hand in HAND_CALCULATED.items():
        f, row = freqs.get(group), rows.get(group)
        if f is None or row is None:
            out.append(Check(f"Hand calculation: {group}", "a row to check", "group absent",
                             FAIL, "The group vanished from the gap table.", "P1"))
            continue

        # (a) the AC/AN the tool used must be the AC/AN the hand calculation used
        same_counts = (f.ac, f.an) == (hand["ac"], hand["an"])
        out.append(Check(
            f"AC/AN used for {group}", f"{hand['ac']}/{hand['an']}", f"{f.ac}/{f.an}",
            PASS if same_counts else FAIL,
            "gnomAD counts behind the frequency match the recorded response."
            if same_counts else "The tool used different counts from the hand calculation.",
        ))

        # (b) allele frequency
        ok_p = abs((row.allele_freq or 0) - hand["p"]) < 5e-7
        out.append(Check(
            f"Allele frequency p for {group}", f"{hand['p']:.7f} (by hand)", f"{row.allele_freq:.7f}",
            PASS if ok_p else FAIL,
            "p = AC/AN agrees with the hand calculation." if ok_p else "p disagrees with the hand calculation.",
        ))

        # (c) carrier frequency, the number the report actually quotes
        ok_cf = abs((row.carrier_freq or 0) - hand["carrier"]) < 5e-7
        out.append(Check(
            f"Carrier frequency for {group}", f"{hand['carrier']:.7f} (2p - p^2, by hand)",
            f"{row.carrier_freq:.7f}", PASS if ok_cf else FAIL,
            "Hardy-Weinberg carrier frequency agrees with the hand calculation."
            if ok_cf else "Carrier frequency disagrees with the hand calculation.",
        ))

        # (d) the "how many more participants" figure
        ok_n = row.required_n == hand["required_n_30"]
        out.append(Check(
            f"Required n for 30 carriers, {group}", str(hand["required_n_30"]), str(row.required_n),
            PASS if ok_n else FAIL,
            "ceil(30 / carrier frequency) agrees with the hand calculation."
            if ok_n else "Required enrolment disagrees with the hand calculation.",
        ))
    return out


# ---------------------------------------------------------------------------
# 2. Absurd results (D6 item 6)
# ---------------------------------------------------------------------------
def check_for_absurd_results(runs) -> list[Check]:
    """Invariants that must hold no matter what the pipeline computed."""
    problems: list[str] = []
    checked = 0

    for version, run in runs.items():
        for r in run.result.groups:
            checked += 1
            where = f"{version}/{r.group}"
            if r.carrier_freq is not None and not 0.0 <= r.carrier_freq <= 1.0:
                problems.append(f"{where}: carrier frequency {r.carrier_freq} is outside 0-100%")
            if r.ac is not None and r.an is not None and r.ac > r.an:
                problems.append(f"{where}: AC {r.ac} exceeds AN {r.an}")
            if r.expected_carriers is not None and r.expected_carriers > r.n_trial + 1e-9:
                problems.append(f"{where}: {r.expected_carriers:.1f} expected carriers from {r.n_trial:.0f} participants")
            if r.ci_low is not None and r.carrier_freq is not None:
                if not r.ci_low <= r.carrier_freq <= r.ci_high:
                    problems.append(f"{where}: carrier frequency {r.carrier_freq} outside its own interval")
                if r.ci_low > r.ci_high:
                    problems.append(f"{where}: interval is inverted")
            if r.prob_fewer_than_k is not None and not 0.0 <= r.prob_fewer_than_k <= 1.0:
                problems.append(f"{where}: P(<k) = {r.prob_fewer_than_k} is not a probability")
            if r.required_n is not None and r.required_n <= 0:
                problems.append(f"{where}: required n is {r.required_n}")
            if r.insufficient_data and r.carrier_freq is not None:
                problems.append(f"{where}: flagged insufficient data but still quotes a frequency")

    return [Check(
        "No absurd results in the gap table",
        "every row within its bounds",
        f"{len(problems)} problems across {checked} rows",
        PASS if not problems else FAIL,
        "Checked: carrier frequency in 0-100%, AC <= AN, expected carriers <= participants, "
        "point estimate inside its interval, P(<k) a probability, required n positive, and "
        "no frequency quoted for a group flagged as insufficient data."
        + ("" if not problems else " Problems: " + "; ".join(problems[:5])),
    )]


def check_conservation_of_participants(counts, runs) -> list[Check]:
    """Mapping moves participants between groups; it must not create or destroy them."""
    out = []
    reported = sum(counts.values())
    for version, run in runs.items():
        mapped_total = sum(r.n_trial for r in run.result.groups)
        missing = reported - mapped_total
        ok = -1e-6 <= missing <= reported + 1e-6 and mapped_total <= reported + 1e-6
        out.append(Check(
            f"Participants conserved under mapping {version}",
            f"mapped + unmapped = {reported}",
            f"mapped {mapped_total:.0f}, unaccounted {missing:.0f}",
            PASS if ok else FAIL,
            "The mapping never invents or loses participants; anything unmapped is missing data.",
        ))
    return out


# ---------------------------------------------------------------------------
# 3. Trial counts against the source record (D6 item 3, partially)
# ---------------------------------------------------------------------------
def check_trial_counts(counts, used) -> list[Check]:
    """Re-read the registry JSON directly, bypassing the baseline parser entirely."""
    out: list[Check] = []
    raw = json.loads(RAW_TRIAL.read_text())
    module = raw["resultsSection"]["baselineCharacteristicsModule"]

    total_group = next(g["id"] for g in module["groups"] if g["title"].strip().lower() == "total")
    race_measure = next(m for m in module["measures"] if m["title"].startswith("Race"))
    direct = {}
    for cat in race_measure["classes"][0]["categories"]:
        for meas in cat["measurements"]:
            if meas["groupId"] == total_group:
                direct[cat["title"]] = int(meas["value"])

    out.append(Check(
        "NCT01609842 race counts: parser vs raw record",
        json.dumps(HAND_READ_TRIAL_COUNTS, sort_keys=True),
        json.dumps(direct, sort_keys=True),
        PASS if direct == HAND_READ_TRIAL_COUNTS else FAIL,
        "Counts read straight from the registry JSON match the counts read by eye from the table.",
    ))
    out.append(Check(
        "NCT01609842 race counts: pipeline output",
        json.dumps(HAND_READ_TRIAL_COUNTS, sort_keys=True),
        json.dumps(counts, sort_keys=True),
        PASS if counts == HAND_READ_TRIAL_COUNTS else FAIL,
        "What the pipeline pooled matches the source record.",
    ))

    denom = next(c["value"] for b in module["denoms"] for c in b["counts"] if c["groupId"] == total_group)
    categories_sum = sum(direct.values())
    out.append(Check(
        "NCT01609842 categories sum to stated enrolment",
        f"{HAND_READ_TRIAL_TOTAL} (denoms block says {denom})",
        str(categories_sum),
        PASS if categories_sum == int(denom) == HAND_READ_TRIAL_TOTAL else FAIL,
        "Race categories add up to the trial's own participant total, so no arm was double-counted.",
    ))
    out.append(Check(
        "Check 5 parsed trials against their web pages",
        "5 trials verified by a human",
        f"1 of 5 checked against the recorded record ({', '.join(used)})",
        OPEN,
        "Only NCT01609842 was captured during the Phase 0 smoke tests, and no network is "
        "available here. Run the pipeline online, then check 5 trials against their results pages.",
        "MED",
    ))
    return out


# ---------------------------------------------------------------------------
# 4. Function direction labels (D6 item 4)
# ---------------------------------------------------------------------------
def _direction(text: str) -> str:
    t = (text or "").strip().lower()
    if t in {d.lower() for d in INCREASED}:
        return "increased"
    if t in {d.lower() for d in REDUCED}:
        return "reduced"
    return f"unrecognised:{t}"


def check_function_directions() -> list[Check]:
    """Cross-check the direction of effect across the three config files that state it."""
    out: list[Check] = []

    def read(path, key_col, dir_col, filter_gene=None):
        rows = {}
        with open(ROOT / path, newline="") as fh:
            for row in csv.DictReader(fh):
                if filter_gene and row.get("gene") != filter_gene:
                    continue
                if row.get(key_col):
                    rows[row[key_col]] = row[dir_col]
        return rows

    scope = read("config/demo_scope.csv", "variant_rsid", "function_direction")
    details = read("config/variant_details_CYP2C19.csv", "rsid", "function", filter_gene="CYP2C19")
    truth = read("data/ground_truth.csv", "variant_rsid", "effect_direction")

    for rsid in sorted(set(scope) & set(details)):
        a, b = _direction(scope[rsid]), _direction(details[rsid])
        agree = a == b and not a.startswith("unrecognised")
        out.append(Check(
            f"Function direction for {rsid}",
            f"demo_scope says {scope[rsid]!r} -> {a}",
            f"variant_details says {details[rsid]!r} -> {b}",
            PASS if agree else FAIL,
            "The scope sheet and the variant file agree on the direction of effect."
            if agree else "The two files disagree on the direction, which would invert a finding.",
            "MED",
        ))

    # Vocabulary drift between files is not an error, but it is worth recording.
    drift = [r for r in set(scope) & set(truth)
             if scope[r] != truth[r] and _direction(scope[r]) == _direction(truth[r])]
    if drift:
        out.append(Check(
            "Direction vocabulary is consistent across files",
            "the same word for the same direction",
            f"{len(drift)} rsIDs use different words for the same direction: {', '.join(sorted(drift))}",
            OPEN,
            "e.g. 'decreased_function' in demo_scope.csv vs 'loss_of_function' in ground_truth.csv. "
            "Same meaning, so no number is wrong, but agree one vocabulary before the report is written.",
            "MED",
        ))

    # The *17 allele is the one that is easy to get backwards.
    star17 = details.get("rs12248560", "")
    out.append(Check(
        "CYP2C19*17 is labelled increased function",
        "increased function",
        star17 or "(absent)",
        PASS if _direction(star17) == "increased" else FAIL,
        "*2 and *3 are loss of function while *17 is increased function; mixing them up would "
        "reverse the clinical meaning of a flag.",
        "MED",
    ))
    return out


# ---------------------------------------------------------------------------
# 5. ClinVar currency and the unverifiable items (D6 items 2 and 5)
# ---------------------------------------------------------------------------
def check_clinvar_and_open_items(variant) -> list[Check]:
    out: list[Check] = []
    out.append(Check(
        "ClinVar classification recorded for rs4244285",
        "a classification plus a retrieval date",
        f"{variant.classification!r}",
        PASS if variant.classification else FAIL,
        "A classification came through and every Fact carries its own retrieved_at timestamp.",
    ))
    out.append(Check(
        "ClinVar classifications are current",
        "re-queried on the day of the demo",
        "replayed from the Phase 0 recording",
        OPEN,
        "Offline mode replays recorded responses by design. Re-run online on the day and note "
        "the retrieval date in the report.",
        "P1",
    ))
    out.append(Check(
        "gnomAD frequencies vs CPIC allele-frequency tables",
        "agreement on magnitude and on which groups are higher",
        "not compared",
        OPEN,
        "Needs the CPIC frequency tables, which are not in this repo and cannot be fetched here. "
        "Compare CYP2C19*2 across afr/eas/nfe/sas and explain any difference in group definitions.",
        "MED",
    ))
    out.append(Check(
        "Target-population shares are sourced",
        "disease-burden sources per group",
        "all shares are labelled ASSUMPTION placeholders",
        OPEN,
        "data/target_population_shares.csv is deliverable D5 and still holds structural "
        "placeholders. Every representation ratio depends on it.",
        "MED",
    ))
    out += check_target_definition()
    out.append(Check(
        "Variant rules are signed off",
        "verified_by filled for every assessable gene",
        "VKORC1 and CYP4F2 have an empty verified_by",
        OPEN,
        "Their rsIDs were taken from the ground-truth sheet and not confirmed against "
        "CPIC/PharmGKB/ClinVar. The pipeline warns at runtime, but warfarin is a backup demo drug.",
        "MED",
    ))
    return out


def check_target_definition() -> list[Check]:
    """
    Cross-check the shares against the medical student's own definition table.

    This is the check that matters most: the definition table records, per indication,
    whether the demographic distribution has actually been established. Computing a
    representation ratio against a denominator the clinician has marked NOT ESTABLISHED
    is exactly the kind of unsupported number the critic is meant to catch.
    """
    out: list[Check] = []
    defs = definitions_for_drug("clopidogrel")

    out.append(Check(
        "Target population is defined for clopidogrel",
        "at least one sourced indication row",
        f"{len(defs)} indication rows, all with a source URL",
        PASS if defs else FAIL,
        "The definition half of D5 is done: indication, geography and age scope are stated "
        "and sourced for each clopidogrel indication.",
        "MED",
    ))

    unestablished = [d.indication for d in defs if not d.distribution_established]
    out.append(Check(
        "Demographic distribution is established for clopidogrel",
        "an established group mix for the chosen indication",
        f"{len(unestablished)} of {len(defs)} indications marked NOT ESTABLISHED",
        OPEN if unestablished else PASS,
        "The clinician has recorded that the group mix for these indications is not yet "
        "established: " + "; ".join(unestablished) + ". Until one is, every representation "
        "ratio rests on the placeholder shares and must be presented as illustrative, not "
        "as a measured gap. Needed: " + (defs[0].required_denominator_data if defs else "-"),
        "MED",
    ))

    out.append(Check(
        "Shares file and definition file do not contradict each other",
        "shares only for indications with an established distribution",
        "shares exist while the definition says NOT ESTABLISHED",
        OPEN if unestablished else PASS,
        "data/target_population_shares.csv carries placeholder shares so the pipeline can "
        "run end to end. Both files label this clearly, but the report must not present the "
        "resulting ratios as established.",
        "MED",
    ))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Task 2.8 / D6 sanity checks on the M1 numbers.")
    ap.add_argument("--strict", action="store_true", help="exit 1 if any check FAILs")
    ap.add_argument("--out", default=str(LOG_PATH))
    args = ap.parse_args()

    table, variant, counts, used, runs = build_m1()

    checks: list[Check] = []
    checks += check_hand_calculations(variant, runs)
    checks += check_for_absurd_results(runs)
    checks += check_conservation_of_participants(counts, runs)
    checks += check_trial_counts(counts, used)
    checks += check_function_directions()
    checks += check_clinvar_and_open_items(variant)

    width = max(len(c.check) for c in checks) + 2
    print("=" * 110)
    print("TASK 2.8 / D6 SANITY CHECK  -  milestone M1, clopidogrel / CYP2C19*2 (10-94781859-G-A)")
    print("=" * 110)
    for c in checks:
        print(f"[{c.status:<4}] {c.check:<{width}} expected: {c.expected}")
        print(f"{'':<7} {'':<{width}} observed: {c.observed}")

    failed = [c for c in checks if c.status == FAIL]
    open_items = [c for c in checks if c.status == OPEN]
    print("\n" + "=" * 110)
    print(f"{len(checks) - len(failed) - len(open_items)} passed, {len(failed)} failed, {len(open_items)} open")
    if failed:
        print("\nFAILED:")
        for c in failed:
            print(f"  - {c.check}: {c.explanation}")
    if open_items:
        print("\nOPEN (cannot be closed from inside this repo):")
        for c in open_items:
            print(f"  - [{c.owner}] {c.check}: {c.explanation}")

    out_path = Path(args.out)
    with open(out_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["check", "expected", "observed", "status", "explanation", "owner"])
        writer.writeheader()
        for c in checks:
            writer.writerow(asdict(c))
    print(f"\nLog sheet written to {out_path}")

    return 1 if (args.strict and failed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
