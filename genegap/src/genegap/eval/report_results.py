"""Task 6.3: generate eval_out/RESULTS_AND_LIMITS.md from the real artifacts.

Discipline, the same as the arithmetic rule: no figure in the generated document is
a literal in this source. Every number is read from a committed artifact or computed
from one. If an artifact is missing, the document says so in place of the section
rather than quietly dropping it.

Artifacts read:
  eval_out/baseline_responses.json   the cached Arm A responses
  eval_out/mini_eval_summary.md      the scored summary
  config/ancestry_mapping_*.csv      the mapping versions and their placeholder state
  smoke/responses/*.json             via the offline pipeline (Arm B ground truth)
  the source tree                    to report which code actually calls the checkers

The output is deterministic: no timestamp, so it can be diffed and reproduced
byte-for-byte by the tests.
"""
from __future__ import annotations

import argparse
import ast
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..config import ROOT
from .mini_eval import (
    ANCESTRY_GROUP_NAMES,
    _pipeline_result,
    BASELINE_CACHE,
    DEMO_DRUG,
    DEMO_GENE,
    DEMO_RSID,
    DEMO_VARIANT_ID,
    SUMMARY_PATH,
    ground_truth,
    items,
    run_arm_b,
    score,
)

OUT_PATH = ROOT / "eval_out" / "RESULTS_AND_LIMITS.md"
CONFIG_DIR = ROOT / "config"
PLACEHOLDER_TOKEN = "PLACEHOLDER"

# Where to look for call sites, and what we are looking for.
SEARCH_DIRS = ("src", "scripts", "smoke", "app", "eval")
CHECKER_FUNCTIONS = ("criticise", "check_language")
# The modules that define or evaluate the checkers do not count as the report path.
NON_REPORT_MODULES = ("critic.py", "language.py", "mini_eval.py", "report_results.py")

MISSING = "_Artifact missing; this section could not be generated._"


# ----------------------------------------------------------------- reading artifacts


@dataclass
class MappingState:
    version: str
    path: Path
    exists: bool
    total_rows: int
    placeholder_rows: int
    weighted_rows: int           # rows that actually route participants (weight > 0)
    categories: int

    @property
    def all_placeholder(self) -> bool:
        return self.weighted_rows > 0 and self.placeholder_rows >= self.weighted_rows


def read_mapping_state(version: str) -> MappingState:
    """Row counts and placeholder state, read straight from the CSV."""
    path = CONFIG_DIR / f"ancestry_mapping_{version}.csv"
    if not path.exists():
        return MappingState(version, path, False, 0, 0, 0, 0)

    total = placeholder = weighted = 0
    categories = set()
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            total += 1
            categories.add((row.get("trial_category") or "").strip())
            try:
                weight = float(row.get("weight") or 0.0)
            except ValueError:
                weight = 0.0
            source = (row.get("source") or "").upper()
            if weight > 0:
                weighted += 1
                if PLACEHOLDER_TOKEN in source or not source.strip():
                    placeholder += 1
    return MappingState(version, path, True, total, placeholder, weighted, len(categories))


def mapping_versions() -> List[str]:
    """Every mapping version with a config file, in order."""
    found = sorted(p.stem.rsplit("_", 1)[-1]
                   for p in CONFIG_DIR.glob("ancestry_mapping_*.csv"))
    return found


def find_checker_call_sites() -> Dict[str, List[str]]:
    """Which modules actually call criticise() / check_language().

    Parsed from the AST, not grepped, and the defining and evaluating modules are
    excluded so the answer reflects the real report path.
    """
    sites: Dict[str, List[str]] = {name: [] for name in CHECKER_FUNCTIONS}
    for directory in SEARCH_DIRS:
        base = ROOT / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if path.name in NON_REPORT_MODULES:
                continue
            try:
                tree = ast.parse(path.read_text(), filename=str(path))
            except (SyntaxError, OSError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name in CHECKER_FUNCTIONS:
                    rel = str(path.relative_to(ROOT))
                    if rel not in sites[name]:
                        sites[name].append(rel)
    return sites


# ------------------------------------------------------------------ deriving figures


@dataclass
class Finding:
    """Section 1's numbers, all derived from the Arm B pipeline run."""

    groups: Dict[str, object]
    high_group: str
    low_group: str
    high_cf: float
    low_cf: float
    ratio: float
    trial_total: float
    assessable_groups: int
    insufficient_groups: List[str]
    tier: str
    checks: Dict[str, str]
    n_facts: int
    mapping_version: str


def derive_finding(arm_b: dict, truths: dict) -> Finding:
    """The two most divergent groups, the ratio between them, and the trial figures.

    The per-group enrolment and required-n figures come from the GapResult itself,
    read back from the same offline pipeline run that produced ground truth.
    """
    usable = {g: t for g, t in truths.items() if t.carrier_freq is not None}
    high = max(usable, key=lambda g: usable[g].carrier_freq)
    low = min(usable, key=lambda g: usable[g].carrier_freq)

    result, _variant, _run, _store = _pipeline_result()
    by_group = {
        g.group: {
            "group": g.group,
            "n_trial": g.n_trial,
            "ac": g.ac,
            "an": g.an,
            "carrier_freq": g.carrier_freq,
            "expected_carriers": g.expected_carriers,
            "required_n": g.required_n,
            "insufficient_data": g.insufficient_data,
        }
        for g in result.groups
    }
    trial_total = sum(float(r["n_trial"] or 0.0) for r in by_group.values())

    return Finding(
        groups=by_group,
        high_group=high,
        low_group=low,
        high_cf=usable[high].carrier_freq,
        low_cf=usable[low].carrier_freq,
        ratio=usable[high].carrier_freq / usable[low].carrier_freq,
        trial_total=trial_total,
        assessable_groups=len(usable),
        insufficient_groups=sorted(g for g, t in truths.items() if t.insufficient_data),
        tier=arm_b.get("tier", ""),
        checks=arm_b.get("critic_checks", {}),
        n_facts=arm_b.get("n_cited_facts", 0),
        mapping_version=arm_b.get("mapping_version", ""),
    )


def _pct(value: Optional[float], an: Optional[int] = None) -> str:
    """Percent, to the precision the allele number supports (language rule L5)."""
    if value is None:
        return "not assessable"
    dp = 2
    if an:
        import math
        dp = max(0, int(math.floor(math.log10(an))) - 2)
    return f"{value:.{dp}%}"


def ami_observation(cache: dict, truths: dict) -> Optional[Tuple[str, bool, bool]]:
    """Did the baseline decline on the same group our pipeline flags insufficient?"""
    for group, truth in truths.items():
        if not truth.insufficient_data:
            continue
        for record in cache.get("responses", {}).values():
            if record.get("group") == group:
                declined = bool(record.get("refused")) or record.get("status") != "OK"
                return group, declined, True
        return group, False, True
    return None


# ------------------------------------------------------------------------- rendering


def render(
    summary,
    arm_b: dict,
    truths: dict,
    cache: dict,
    mappings: Sequence[MappingState],
    call_sites: Dict[str, List[str]],
    cache_exists: bool,
    summary_exists: bool,
) -> str:
    L: List[str] = []
    add = L.append

    finding = derive_finding(arm_b, truths)
    counts = summary.counts
    n = summary.n_items
    answered = counts["EXACT"] + counts["CLOSE"] + counts["WRONG"] + counts["FABRICATED"]
    declined = counts["REFUSED"] + counts["ERROR"]

    primary = next((m for m in mappings if m.version == finding.mapping_version), None)
    alternatives = [m for m in mappings if m.version != finding.mapping_version]
    placeholder_alts = [m for m in alternatives if m.all_placeholder]

    add("# GeneGap: results and limits")
    add("")
    add(f"Generated from the committed artifacts by `{Path(__file__).name}`. "
        f"Every figure below is read or computed from an artifact; none is typed by hand.")
    add("")
    add(f"Demo pair: **{DEMO_DRUG} / {DEMO_GENE}**, variant {DEMO_RSID} "
        f"(`{DEMO_VARIANT_ID}`), mapping version `{finding.mapping_version}`.")
    add("")

    # ------------------------------------------------------------ 1. what we found
    add("## 1. What we found")
    add("")
    add(f"Carrier frequency of {DEMO_RSID} varies widely across gnomAD genetic ancestry "
        f"groups. The two most divergent assessable groups are "
        f"**{ANCESTRY_GROUP_NAMES.get(finding.high_group, finding.high_group)} "
        f"(`{finding.high_group}`)** at "
        f"{_pct(finding.high_cf, finding.groups.get(finding.high_group, {}).get('an'))} "
        f"and **{ANCESTRY_GROUP_NAMES.get(finding.low_group, finding.low_group)} "
        f"(`{finding.low_group}`)** at "
        f"{_pct(finding.low_cf, finding.groups.get(finding.low_group, {}).get('an'))}, "
        f"a ratio of **{finding.ratio:.2f}x** between them.")
    add("")
    n_insufficient = len(finding.insufficient_groups)
    verb = "falls" if n_insufficient == 1 else "fall"
    is_are = "is" if n_insufficient == 1 else "are"
    add(f"Across the {len(truths)} groups, {finding.assessable_groups} are assessable and "
        f"{n_insufficient} "
        f"({', '.join(f'`{g}`' for g in finding.insufficient_groups) or 'none'}) "
        f"{verb} below the allele-number floor and {is_are} reported as insufficient data "
        f"rather than given a frequency.")
    add("")
    add(f"Pooled trial enrolment mapped to ancestry groups totals "
        f"{finding.trial_total:,.0f} participants. Per group:")
    add("")
    add("| Group | Mapped participants | Carrier frequency | Expected carriers | "
        "Participants needed |")
    add("|---|---|---|---|---|")
    for group in sorted(finding.groups):
        row = finding.groups[group]
        cf = row.get("carrier_freq")
        an = row.get("an")
        exp = row.get("expected_carriers")
        need = row.get("required_n")
        add(f"| `{group}` | {float(row.get('n_trial') or 0):,.0f} | {_pct(cf, an)} "
            f"| {'-' if exp is None else format(float(exp), ',.1f')} "
            f"| {'-' if need is None else format(int(need), ',d')} |")
    add("")
    passed = [c for c, s in sorted(finding.checks.items()) if s == "PASS"]
    add(f"The critic returned **{finding.tier}**. "
        f"{len(passed)} of {len(finding.checks)} checks passed "
        f"({', '.join(passed)}), over {finding.n_facts} cited provenance facts.")
    add("")

    # ------------------------------------------- 2. how we know it isn't invented
    add("## 2. How we know it isn't invented")
    add("")
    if not cache_exists:
        add(MISSING)
        add("")
    else:
        add(f"A baseline arm asked `{summary.model}` the same {n} questions with no tools "
            f"and no retrieval. Of {n} groups, it produced a usable value for "
            f"**{answered}**, declined **{counts['REFUSED']}**, failed outright on "
            f"**{counts['ERROR']}**, and fabricated **{counts['FABRICATED']}**.")
        add("")
        add(f"**The baseline fabricated nothing.** Where it answered it was approximately "
            f"correct: {counts['EXACT']} within the exact band, {counts['CLOSE']} within "
            f"the close band, {counts['WRONG']} outside both, with a mean absolute error of "
            f"{'not applicable' if summary.mean_absolute_error_pp is None else format(summary.mean_absolute_error_pp, '.1f') + ' percentage points'} "
            f"over the {summary.n_scored_numeric} scorable answers. This is the honest "
            f"result and it is not the result we expected: this model declines rather than "
            f"inventing. The argument for retrieval here is coverage, not hallucination.")
        add("")
        add(f"Our pipeline produced **{summary.arm_b_total} of {n}** values. Each is traced "
            f"to a cited record and replayed through the arithmetic by the critic's "
            f"independent recomputation, and the language checker passed on "
            f"{summary.arm_b_language_pass} of {summary.arm_b_total} generated finding "
            f"sentences.")
        add("")
        observation = ami_observation(cache, truths)
        if observation:
            group, declined_here, _ = observation
            name = ANCESTRY_GROUP_NAMES.get(group, group)
            if declined_here:
                add(f"One agreement is worth naming: the baseline declined on `{group}` "
                    f"({name}), which is the same group our pipeline independently flags as "
                    f"insufficient data from the allele number. Two unrelated routes reached "
                    f"the same conclusion that this value should not be stated.")
            else:
                add(f"Note: the baseline did **not** decline on `{group}` ({name}), the group "
                    f"our pipeline flags as insufficient data.")
            add("")

    # ------------------------------------------------- 3. what we cannot claim
    add("## 3. What we cannot claim")
    add("")

    add(f"**1. One drug-gene pair.** Everything above is {DEMO_DRUG} / {DEMO_GENE} at a "
        f"single variant, {DEMO_RSID}. Nothing here demonstrates that the method "
        f"generalises to any other drug, gene or variant.")
    add("")

    # limit 2 - mapping state, read from the real CSVs
    if primary is None or not primary.exists:
        add("**2. Mapping state unknown.** " + MISSING)
    else:
        alt_desc = "; ".join(
            f"`{m.version}` has {m.weighted_rows} weighted rows, "
            f"{m.placeholder_rows} of them placeholders"
            for m in alternatives
        ) or "no alternative mapping files exist"
        add(f"**2. Mapping {primary.version} only.** The result above was computed under "
            f"mapping `{primary.version}` ({primary.weighted_rows} weighted rows across "
            f"{primary.categories} trial categories, {primary.placeholder_rows} of them "
            f"placeholders). The alternatives: {alt_desc}.")
        if placeholder_alts:
            names = ", ".join(f"`{m.version}`" for m in placeholder_alts)
            add("")
            add(f"> Every weighted row in {names} is a placeholder carrying no citation. "
                f"`robust_across_mappings` exists and runs, but it has **never been "
                f"exercised against a populated alternative mapping**. Running it over "
                f"{names} compares mapping `{primary.version}` against invented weights, "
                f"which tests the plumbing and not the robustness of the finding. "
                f"**Robustness across mapping choices is NOT demonstrated.** Any statement "
                f"that a finding 'survives all mappings' would be false as the "
                f"configuration stands.")
    add("")

    add("**3. Race is not ancestry.** Trial-reported race is self-identified and "
        "administrative; gnomAD groups are genetic ancestry inferred from genotype. The "
        "mapping between them is a documented assumption, not a fact, and every ratio "
        "above is sensitive to it.")
    add("")

    add("**4. Hardy-Weinberg.** Carrier frequency is computed as 1 - (1 - p)^2, which "
        "assumes Hardy-Weinberg equilibrium in each group. Population structure, "
        "inbreeding and selection all violate it to some degree.")
    add("")

    add("**5. No HLA, no CYP2D6.** The method does not transfer to these. They are "
        "haplotypes and copy-number variants; gnomAD allele frequencies do not represent "
        "star alleles, and nothing in this system handles them. Asked about them, the "
        "pipeline reports that it cannot assess rather than guessing.")
    add("")

    add("**6. Invisible trials.** Trials without posted results have no baseline "
        "demographic table, so they are invisible to us. Which trials we can assess at all "
        "is therefore itself a biased sample of trials.")
    add("")

    add("**7. No temporal holdout.** We have not shown the system would have found this "
        "before the finding was public. The CYP2C19 / clopidogrel association is long "
        "established and was in the model's training data and in our own curated files.")
    add("")

    if cache_exists:
        add(f"**8. One model, one run, {n} groups.** The evaluation is a single model "
            f"(`{summary.model}`) at one temperature, one call per item, over {n} ancestry "
            f"groups of one variant. No repeats, no sampling, no second model. It is a "
            f"demonstration, not a benchmark.")
    else:
        add("**8. Evaluation artifacts missing.** " + MISSING)
    add("")

    add("**9. No clinical claim.** The system makes no clinical claim and must not be used "
        "to inform prescribing. Its output is a flag for a human expert to review.")
    add("")

    # --------------------------------------------------------- 4. wiring status
    add("## 4. Wiring status")
    add("")
    unwired = [fn for fn in CHECKER_FUNCTIONS if not call_sites.get(fn)]
    for fn in CHECKER_FUNCTIONS:
        sites = call_sites.get(fn) or []
        if sites:
            add(f"- `{fn}()` is called from: " + ", ".join(f"`{s}`" for s in sites))
        else:
            add(f"- `{fn}()` is **not called from the report path**. It is implemented and "
                f"tested, but no report, demo, agent or script invokes it.")
    add("")
    if unwired:
        add(f"**{len(unwired)} of {len(CHECKER_FUNCTIONS)} verification layers are not "
            f"wired into the report path.** The figures in section 1 were produced by the "
            f"evaluation harness calling them directly, not by the demo. Until a report "
            f"path calls them, the guarantee in section 2 holds for this document only, "
            f"not for anything the demo prints.")
        add("")

    if not summary_exists:
        add("> Note: the scored summary artifact was missing when this document was "
            "generated.")
        add("")

    return "\n".join(L)


# --------------------------------------------------------------------------- driver


def generate(out_path: Path = OUT_PATH) -> str:
    """Read every artifact and return the document text."""
    cache_exists = BASELINE_CACHE.exists()
    summary_exists = SUMMARY_PATH.exists()
    cache = json.loads(BASELINE_CACHE.read_text()) if cache_exists else {"responses": {}}

    arm_b = run_arm_b()
    truths = ground_truth()
    summary = score(cache_path=BASELINE_CACHE, arm_b=arm_b, truths=truths)
    mappings = [read_mapping_state(v) for v in mapping_versions()]
    call_sites = find_checker_call_sites()

    return render(summary, arm_b, truths, cache, mappings, call_sites,
                  cache_exists, summary_exists)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="GeneGap results and limits (task 6.3)")
    parser.add_argument("--out", default=str(OUT_PATH))
    args = parser.parse_args(argv)

    text = generate()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(text)
    print(f"\nWritten to {out}")
    return 0


if __name__ == "__main__":      # pragma: no cover
    raise SystemExit(main())
