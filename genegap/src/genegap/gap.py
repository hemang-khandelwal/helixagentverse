"""Glue for milestone M1: variant + mapped trial counts + target population -> GapResult.

Every number here is produced by a function in genegap.stats. This module only decides
which numbers to ask for and records what was missing; it never computes a statistic
inline, so the critic can re-derive any figure from the stats functions alone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .mapping import MappedCounts
from .population import TargetPopulation
from .schemas import GapResult, GroupGap, Variant
from .stats import (
    allele_frequency,
    carrier_frequency,
    carrier_frequency_interval,
    carrier_ratio,
    enrolment_ratio,
    expected_carriers,
    insufficient_data,
    prob_fewer_than_k,
    required_n,
)

# Defaults for the two "how big is the gap" questions, overridable per run.
DEFAULT_TARGET_CARRIERS = 30   # how many carriers we would like a group to contribute
DEFAULT_K = 10                 # "fewer than k carriers" threshold from the project summary


@dataclass
class GapRun:
    """One gap table plus the plain-English record of how it was built."""

    result: GapResult
    notes: list[str] = field(default_factory=list)
    target_label: str = ""
    mapping_version: str = "A"


def _target_carrier_shares(
    target: TargetPopulation,
    carrier_freqs: dict[str, Optional[float]],
) -> dict[str, float]:
    """
    Each group's share of the carriers in the target population.

    This is the denominator of the representation ratio: a group can be well enrolled by
    headcount yet still contribute few carriers if the variant is rare in that group.
    """
    weighted = {
        g: share * carrier_freqs[g]
        for g, share in target.shares.items()
        if carrier_freqs.get(g) is not None
    }
    total = sum(weighted.values())
    if total <= 0:
        return {}
    return {g: v / total for g, v in weighted.items()}


def compute_gap(
    variant: Variant,
    mapped: MappedCounts,
    target: TargetPopulation,
    inheritance: str = "dominant",
    min_an: int = 2000,
    target_carriers: float = DEFAULT_TARGET_CARRIERS,
    k: int = DEFAULT_K,
    draws: int = 10000,
    seed: int = 0,
) -> GapRun:
    """
    Build the gap table for one variant under one ancestry mapping.

    Groups are the union of those gnomAD reports for this variant and those the target
    population names, so a group that the trial enrolled nobody from still appears with
    n_trial = 0 rather than vanishing from the table.
    """
    notes: list[str] = []
    freq_by_group = {f.group: f for f in variant.frequencies}
    trial_counts = mapped.groups
    total_mapped = mapped.total_mapped

    if total_mapped <= 0:
        notes.append("No trial participants could be mapped to an ancestry group; ratios cannot be computed.")

    groups = sorted(set(freq_by_group) | set(target.shares) | set(trial_counts))

    # Pass 1: carrier frequency per group, which the carrier-share denominator needs.
    carrier_freqs: dict[str, Optional[float]] = {}
    intervals: dict[str, Optional[tuple[float, float, float]]] = {}
    for g in groups:
        f = freq_by_group.get(g)
        if f is None or insufficient_data(f.an, min_an=min_an):
            carrier_freqs[g] = None
            intervals[g] = None
            continue
        p = allele_frequency(f.ac, f.an)
        cf = carrier_frequency(p, inheritance=inheritance)
        carrier_freqs[g] = cf if isinstance(cf, float) else None
        intervals[g] = carrier_frequency_interval(
            f.ac, f.an, inheritance=inheritance, draws=draws, seed=seed
        )

    if inheritance == "x_linked":
        notes.append(
            "This gene is X-linked. The autosomal carrier formula does not apply, so carrier "
            "frequencies are left blank and no gap is reported."
        )

    target_carrier_shares = _target_carrier_shares(target, carrier_freqs)

    # Pass 2: the per-group row.
    rows: list[GroupGap] = []
    fact_ids = list(variant.fact_ids)
    for g in groups:
        f = freq_by_group.get(g)
        n_trial = float(trial_counts.get(g, 0.0))
        cf = carrier_freqs[g]
        interval = intervals[g]
        t_share = target.shares.get(g)
        trial_share = (n_trial / total_mapped) if total_mapped > 0 else None

        exp = expected_carriers(n_trial, cf)
        row = GroupGap(
            group=g,
            n_trial=n_trial,
            ac=f.ac if f else None,
            an=f.an if f else None,
            allele_freq=allele_frequency(f.ac, f.an) if f else None,
            trial_share=trial_share,
            target_share=t_share,
            carrier_freq=cf,
            ci_low=interval[0] if interval else None,
            ci_high=interval[2] if interval else None,
            expected_carriers=exp,
            enrolment_ratio=enrolment_ratio(trial_share, t_share),
            prob_fewer_than_k=prob_fewer_than_k(int(round(n_trial)), cf, k),
            k_threshold=k,
            required_n=required_n(target_carriers, cf),
            insufficient_data=(f is None or insufficient_data(f.an, min_an=min_an)),
        )
        rows.append(row)
        if f is not None:
            fact_ids.append(f.fact_id)

    # Pass 3: carrier ratios, which need every group's expected carriers first.
    total_expected = sum(r.expected_carriers for r in rows if r.expected_carriers is not None)
    for row in rows:
        if row.expected_carriers is None or total_expected <= 0:
            continue
        row.carrier_ratio = carrier_ratio(
            row.expected_carriers / total_expected,
            target_carrier_shares.get(row.group),
        )

    thin = [r.group for r in rows if r.insufficient_data]
    if thin:
        notes.append(
            f"gnomAD has fewer than {min_an} alleles for: {', '.join(thin)}. These groups are "
            "reported as insufficient data rather than given a frequency."
        )
    absent = [r.group for r in rows if r.n_trial == 0 and r.target_share]
    if absent:
        notes.append(
            f"The trial data maps nobody to: {', '.join(absent)}, although the target population "
            "includes these groups."
        )
    off_target = [r.group for r in rows if r.n_trial > 0 and r.target_share is None]
    if off_target:
        notes.append(
            f"The target population table has no share for: {', '.join(off_target)}, so no "
            "representation ratio can be formed for them even though the mapping places "
            "participants there. Either add them to the table or say why they are excluded."
        )
    if mapped.missing_fraction:
        notes.append(
            f"{mapped.missing_fraction:.1%} of reported participants could not be mapped to any "
            "ancestry group and are excluded from the ratios."
        )
    notes += mapped.notes
    notes += target.notes

    result = GapResult(
        variant_id=variant.variant_id,
        gene=variant.gene,
        inheritance=inheritance if inheritance in {"dominant", "recessive", "x_linked"} else "dominant",
        mapping_version=mapped.version,
        groups=rows,
        fact_ids=sorted(set(fact_ids)),
    )
    return GapRun(result=result, notes=notes, target_label=target.label, mapping_version=mapped.version)


def robust_across_mappings(runs: dict[str, GapRun], group: str, threshold: float = 1.0) -> Optional[bool]:
    """
    True when a group is under-represented (carrier ratio below threshold) under every mapping
    version, False when the versions disagree, and None when the ratio is missing somewhere.

    This is what lets the report say "robust" instead of "mapping-dependent".
    """
    verdicts = []
    for run in runs.values():
        row = next((r for r in run.result.groups if r.group == group), None)
        if row is None or row.carrier_ratio is None:
            return None
        verdicts.append(row.carrier_ratio < threshold)
    if not verdicts:
        return None
    # all(empty-of-True) would read as "robust", so an all-False run must come back False.
    return all(verdicts) and any(verdicts)


def format_gap_table(run: GapRun) -> str:
    """A readable gap table for the terminal (milestone M1's printed output)."""
    r = run.result
    head = (
        f"Variant {r.variant_id}"
        + (f" ({r.gene})" if r.gene else "")
        + f"   inheritance: {r.inheritance}   mapping: {r.mapping_version}"
    )
    lines = [head, f"Target population: {run.target_label}", ""]
    lines.append(
        f"{'group':<10}{'n_trial':>9}{'AC/AN':>18}{'carrier freq (95% CI)':>28}"
        f"{'exp. carriers':>15}{'enrol ratio':>13}{'carrier ratio':>15}{'P(<k)':>9}{'need n':>9}"
    )
    lines.append("-" * 126)
    for g in sorted(r.groups, key=lambda x: (x.carrier_ratio is None, x.carrier_ratio or 0.0)):
        if g.insufficient_data:
            freq = "insufficient data"
            ci = ""
        else:
            freq = f"{g.carrier_freq:.3%}" if g.carrier_freq is not None else "-"
            ci = f" ({g.ci_low:.3%}-{g.ci_high:.3%})" if g.ci_low is not None else ""
        acan = f"{g.ac}/{g.an}" if g.an is not None else "-"
        lines.append(
            f"{g.group:<10}{g.n_trial:>9.0f}{acan:>18}{(freq + ci):>28}"
            f"{(f'{g.expected_carriers:.1f}' if g.expected_carriers is not None else '-'):>15}"
            f"{(f'{g.enrolment_ratio:.2f}' if g.enrolment_ratio is not None else '-'):>13}"
            f"{(f'{g.carrier_ratio:.2f}' if g.carrier_ratio is not None else '-'):>15}"
            f"{(f'{g.prob_fewer_than_k:.2f}' if g.prob_fewer_than_k is not None else '-'):>9}"
            f"{(str(g.required_n) if g.required_n is not None else '-'):>9}"
        )
    if run.notes:
        lines.append("\nWhat the numbers assume:")
        lines += [f"  - {n}" for n in run.notes]
    return "\n".join(lines)
