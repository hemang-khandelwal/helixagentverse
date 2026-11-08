"""Deterministic verification layer (Critic) for GeneGap.

Hard rule: this module contains NO LLM calls and NO network calls.
It operates purely over in-memory domain objects and deterministic functions
imported from ``genegap.stats.core``.

Checks:
  Adjudicating (can FAIL and block the result -> REJECTED):
    C1  provenance and no-AI-arithmetic
    C2  arithmetic replay (via genegap.stats.core)
    C3  bounds and internal consistency
    C4  mapping is declared, not inferred
  Annotating (can only WARN -> moves TIER A down to TIER B):
    C5  evidence quality
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Iterable, Literal, Mapping, Optional, Sequence, Union

from pydantic import BaseModel, Field

from .mapping import AncestryMapping, MappedCounts, load_all_mappings, map_counts
from .schemas import Fact, GapResult, GroupGap, Trial, Variant
from .stats.core import (
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

CheckStatus = Literal["PASS", "WARN", "FAIL"]
Tier = Literal["TIER A", "TIER B", "REJECTED"]

# Explicit allow-list of the 10 top-level gnomAD v4 genetic ancestry groups
# (matching genegap.clients.gnomad.ANCESTRY_GROUPS without importing HTTP clients).
ANCESTRY_GROUPS: frozenset[str] = frozenset(
    {"afr", "ami", "amr", "asj", "eas", "fin", "mid", "nfe", "remaining", "sas"}
)


class CheckResult(BaseModel):
    """Outcome of a single critic check (C1-C5)."""

    check_id: Literal["C1", "C2", "C3", "C4", "C5"]
    name: str
    adjudicating: bool
    status: CheckStatus
    reason: str
    details: list[str] = Field(default_factory=list)


class CriticReport(BaseModel):
    """Aggregate report produced by ``criticise(result)``."""

    tier: Tier
    checks: dict[str, CheckResult]

    @property
    def passed(self) -> bool:
        """True when no adjudicating check failed (TIER A or TIER B)."""
        return self.tier != "REJECTED"

    @property
    def c1(self) -> CheckResult:
        return self.checks["C1"]

    @property
    def c2(self) -> CheckResult:
        return self.checks["C2"]

    @property
    def c3(self) -> CheckResult:
        return self.checks["C3"]

    @property
    def c4(self) -> CheckResult:
        return self.checks["C4"]

    @property
    def c5(self) -> CheckResult:
        return self.checks["C5"]

    def __getitem__(self, key: str) -> CheckResult:
        return self.checks[key]


def _resolve_facts(
    fact_ids: Sequence[str],
    facts_source: Optional[Union[Mapping[str, Fact], Iterable[Fact], Any]],
) -> tuple[dict[str, Fact], list[str]]:
    """Resolve cited fact_ids against a provided mapping, iterable, or Store-like object."""
    resolved: dict[str, Fact] = {}
    missing: list[str] = []
    if facts_source is None:
        return resolved, missing

    if isinstance(facts_source, Mapping):
        lookup = dict(facts_source)
        for fid in fact_ids:
            if fid in lookup:
                resolved[fid] = lookup[fid]
            else:
                missing.append(fid)
    elif hasattr(facts_source, "get_fact") and callable(facts_source.get_fact):
        for fid in fact_ids:
            fact = facts_source.get_fact(fid)
            if fact is not None:
                resolved[fid] = fact
            else:
                missing.append(fid)
    else:
        lookup = {f.id: f for f in facts_source if isinstance(f, Fact)}
        for fid in fact_ids:
            if fid in lookup:
                resolved[fid] = lookup[fid]
            else:
                missing.append(fid)

    return resolved, missing


def _has_numeric_values(group: GroupGap) -> bool:
    numeric_fields = (
        group.ac,
        group.an,
        group.allele_freq,
        group.trial_share,
        group.target_share,
        group.carrier_freq,
        group.ci_low,
        group.ci_high,
        group.expected_carriers,
        group.enrolment_ratio,
        group.carrier_ratio,
        group.prob_fewer_than_k,
        group.required_n,
    )
    return group.n_trial > 0 or any(v is not None for v in numeric_fields)


def _check_c1_provenance(
    result: GapResult,
    facts_source: Optional[Union[Mapping[str, Fact], Iterable[Fact], Any]],
    variant: Optional[Variant],
    trials: Optional[Sequence[Trial]],
) -> CheckResult:
    """C1: provenance and no-AI-arithmetic."""
    failures: list[str] = []

    numeric_groups = [g for g in result.groups if _has_numeric_values(g)]
    groups_with_counts = [g for g in result.groups if g.ac is not None or g.an is not None]

    if numeric_groups and not result.fact_ids:
        failures.append(
            "GapResult contains numeric group values but cites no supporting fact_ids."
        )

    for idx, fid in enumerate(result.fact_ids):
        if not fid or not str(fid).strip():
            failures.append(f"Empty or blank fact_id at index {idx} in result.fact_ids.")
        elif "llm_extracted" in str(fid).lower():
            failures.append(
                f"Fact ID '{fid}' indicates llm_extracted provenance on numeric result."
            )

    if groups_with_counts and len(result.fact_ids) < len(groups_with_counts):
        failures.append(
            f"GapResult has {len(groups_with_counts)} group(s) with allele counts "
            f"but only {len(result.fact_ids)} cited fact_id(s)."
        )

    if variant is not None:
        freq_by_group = {f.group: f for f in variant.frequencies}
        for g in groups_with_counts:
            vf = freq_by_group.get(g.group)
            if vf is None:
                failures.append(
                    f"Group '{g.group}' has allele counts on GapResult but is missing from Variant.frequencies."
                )
            elif not vf.fact_id:
                failures.append(
                    f"Variant frequency for group '{g.group}' has no fact_id."
                )
            elif vf.fact_id not in result.fact_ids:
                failures.append(
                    f"Variant frequency fact_id '{vf.fact_id}' for group '{g.group}' is not cited in result.fact_ids."
                )

    if trials is not None:
        for t in trials:
            for b in t.baseline:
                if b.counts and not t.fact_ids and not b.fact_ids:
                    failures.append(
                        f"Trial '{t.nct_id}' contributes baseline {b.scheme} counts without a supporting fact_id."
                    )

    resolved_facts, missing_ids = _resolve_facts(result.fact_ids, facts_source)
    if missing_ids:
        failures.append(
            f"Cited fact_id(s) could not be resolved in the fact store: {', '.join(missing_ids)}."
        )

    # Also inspect any extra facts passed directly in facts_source if it is an iterable/mapping
    facts_to_inspect: list[Fact] = list(resolved_facts.values())
    if facts_source is not None and not hasattr(facts_source, "get_fact"):
        if isinstance(facts_source, Mapping):
            for f in facts_source.values():
                if isinstance(f, Fact) and f.id not in resolved_facts:
                    facts_to_inspect.append(f)
        else:
            for f in facts_source:
                if isinstance(f, Fact) and f.id not in resolved_facts:
                    facts_to_inspect.append(f)

    for fact in facts_to_inspect:
        if fact.method == "llm_extracted":
            failures.append(
                f"Fact '{fact.id}' (source={fact.source}, field={fact.field}) has "
                f"method='llm_extracted'; no number used in a calculation may rest on LLM extraction."
            )

    # Verify that each group's (ac, an) traces to a cited gnomAD/frequency fact if values are present
    if resolved_facts and groups_with_counts:
        fact_ac_an: dict[str, tuple[Optional[int], Optional[int]]] = {}
        for fact in resolved_facts.values():
            if isinstance(fact.value, Mapping) and "ac" in fact.value and "an" in fact.value:
                grp = fact.value.get("id")
                if not grp and ":" in fact.field:
                    grp = fact.field.split(":", 1)[1]
                if isinstance(grp, str):
                    fact_ac_an[grp] = (fact.value.get("ac"), fact.value.get("an"))
        if fact_ac_an:
            for g in groups_with_counts:
                if g.group not in fact_ac_an:
                    failures.append(
                        f"Group '{g.group}' counts (ac={g.ac}, an={g.an}) have no matching cited Fact value."
                    )
                else:
                    f_ac, f_an = fact_ac_an[g.group]
                    if f_ac != g.ac or f_an != g.an:
                        failures.append(
                            f"Group '{g.group}' counts (ac={g.ac}, an={g.an}) disagree with cited Fact value (ac={f_ac}, an={f_an})."
                        )

    if failures:
        return CheckResult(
            check_id="C1",
            name="provenance and no-AI-arithmetic",
            adjudicating=True,
            status="FAIL",
            reason=failures[0],
            details=failures,
        )

    return CheckResult(
        check_id="C1",
        name="provenance and no-AI-arithmetic",
        adjudicating=True,
        status="PASS",
        reason=f"All {len(result.fact_ids)} cited fact(s) trace cleanly with no llm_extracted arithmetic inputs.",
    )


def _close(actual: Optional[float], expected: Optional[float], tol: float) -> bool:
    if actual is None and expected is None:
        return True
    if actual is None or expected is None:
        return False
    if math.isnan(actual) or math.isnan(expected):
        return False
    return abs(actual - expected) <= tol


def _check_c2_arithmetic_replay(
    result: GapResult,
    *,
    target_carriers: float,
    draws: int,
    seed: int,
    tolerance: float,
) -> CheckResult:
    """C2: recompute every derived number via genegap.stats.core and compare."""
    failures: list[str] = []

    total_mapped = sum(g.n_trial for g in result.groups)

    for g in result.groups:
        # 1. Allele frequency
        expected_af = (
            allele_frequency(g.ac, g.an)
            if (g.ac is not None and g.an is not None)
            else None
        )
        if not _close(g.allele_freq, expected_af, tolerance):
            failures.append(
                f"Group '{g.group}': allele_freq={g.allele_freq} != recomputed {expected_af}."
            )

        # 2. Trial share and enrolment ratio
        expected_trial_share = (g.n_trial / total_mapped) if total_mapped > 0 else None
        if not _close(g.trial_share, expected_trial_share, tolerance):
            failures.append(
                f"Group '{g.group}': trial_share={g.trial_share} != recomputed {expected_trial_share}."
            )

        expected_er = enrolment_ratio(expected_trial_share, g.target_share)
        if not _close(g.enrolment_ratio, expected_er, tolerance):
            failures.append(
                f"Group '{g.group}': enrolment_ratio={g.enrolment_ratio} != recomputed {expected_er}."
            )

        # 3. Carrier frequency, credible interval, expected carriers, prob_fewer_than_k, required_n
        if g.insufficient_data or result.inheritance == "x_linked":
            for field_name, val in (
                ("carrier_freq", g.carrier_freq),
                ("ci_low", g.ci_low),
                ("ci_high", g.ci_high),
                ("expected_carriers", g.expected_carriers),
                ("carrier_ratio", g.carrier_ratio),
                ("prob_fewer_than_k", g.prob_fewer_than_k),
                ("required_n", g.required_n),
            ):
                if val is not None:
                    failures.append(
                        f"Group '{g.group}': {field_name}={val} must be None when "
                        f"insufficient_data={g.insufficient_data} or inheritance='{result.inheritance}'."
                    )
            continue

        raw_cf = carrier_frequency(expected_af, result.inheritance)
        expected_cf = raw_cf if isinstance(raw_cf, float) else None
        if not _close(g.carrier_freq, expected_cf, tolerance):
            failures.append(
                f"Group '{g.group}': carrier_freq={g.carrier_freq} != recomputed {expected_cf}."
            )

        expected_ci = (
            carrier_frequency_interval(
                g.ac, g.an, inheritance=result.inheritance, draws=draws, seed=seed
            )
            if (g.ac is not None and g.an is not None)
            else None
        )
        expected_lo = expected_ci[0] if expected_ci is not None else None
        expected_hi = expected_ci[2] if expected_ci is not None else None
        if not _close(g.ci_low, expected_lo, tolerance):
            failures.append(
                f"Group '{g.group}': ci_low={g.ci_low} != recomputed {expected_lo}."
            )
        if not _close(g.ci_high, expected_hi, tolerance):
            failures.append(
                f"Group '{g.group}': ci_high={g.ci_high} != recomputed {expected_hi}."
            )

        expected_ec = expected_carriers(g.n_trial, expected_cf)
        if not _close(g.expected_carriers, expected_ec, tolerance):
            failures.append(
                f"Group '{g.group}': expected_carriers={g.expected_carriers} != recomputed {expected_ec}."
            )

        if g.k_threshold is not None or g.prob_fewer_than_k is not None:
            if g.k_threshold is None:
                failures.append(
                    f"Group '{g.group}': prob_fewer_than_k={g.prob_fewer_than_k} set without k_threshold."
                )
            else:
                expected_pfk = prob_fewer_than_k(
                    int(round(g.n_trial)), expected_cf, g.k_threshold
                )
                if not _close(g.prob_fewer_than_k, expected_pfk, tolerance):
                    failures.append(
                        f"Group '{g.group}': prob_fewer_than_k={g.prob_fewer_than_k} != recomputed {expected_pfk}."
                    )

        expected_rn = required_n(target_carriers, expected_cf)
        if g.required_n != expected_rn:
            failures.append(
                f"Group '{g.group}': required_n={g.required_n} != recomputed {expected_rn}."
            )

    # 4. Carrier ratio across groups
    weighted_target = {
        g.group: g.target_share * g.carrier_freq
        for g in result.groups
        if g.target_share is not None and g.carrier_freq is not None and not g.insufficient_data
    }
    total_target_weighted = sum(weighted_target.values())
    target_carrier_shares = (
        {grp: val / total_target_weighted for grp, val in weighted_target.items()}
        if total_target_weighted > 0
        else {}
    )
    total_expected = sum(
        g.expected_carriers
        for g in result.groups
        if g.expected_carriers is not None and not g.insufficient_data
    )

    for g in result.groups:
        if g.insufficient_data or result.inheritance == "x_linked":
            continue
        if g.expected_carriers is not None and total_expected > 0:
            expected_cr = carrier_ratio(
                g.expected_carriers / total_expected,
                target_carrier_shares.get(g.group),
            )
        else:
            expected_cr = None
        if not _close(g.carrier_ratio, expected_cr, tolerance):
            failures.append(
                f"Group '{g.group}': carrier_ratio={g.carrier_ratio} != recomputed {expected_cr}."
            )

    if failures:
        return CheckResult(
            check_id="C2",
            name="arithmetic replay",
            adjudicating=True,
            status="FAIL",
            reason=failures[0],
            details=failures,
        )

    return CheckResult(
        check_id="C2",
        name="arithmetic replay",
        adjudicating=True,
        status="PASS",
        reason=f"All derived figures across {len(result.groups)} group(s) match genegap.stats.core within {tolerance:g}.",
    )


def _in_unit_interval(val: Optional[float]) -> bool:
    if val is None:
        return True
    if math.isnan(val):
        return False
    return 0.0 <= val <= 1.0


def _check_c3_bounds_and_consistency(
    result: GapResult,
    *,
    variant: Optional[Variant],
    min_an: int,
) -> CheckResult:
    """C3: bounds and internal consistency."""
    failures: list[str] = []

    if variant is not None:
        for vf in variant.frequencies:
            if vf.group not in ANCESTRY_GROUPS:
                failures.append(
                    f"Variant frequency group '{vf.group}' is not in the gnomAD allow-list."
                )
            if vf.ac < 0 or vf.an < 0 or vf.ac > vf.an:
                failures.append(
                    f"Variant frequency for '{vf.group}' violates 0 <= ac <= an (ac={vf.ac}, an={vf.an})."
                )
            if not _in_unit_interval(vf.af):
                failures.append(
                    f"Variant frequency af={vf.af} for '{vf.group}' is outside [0, 1]."
                )

    for g in result.groups:
        if g.group not in ANCESTRY_GROUPS:
            failures.append(
                f"Group '{g.group}' is not in the gnomAD allow-list {sorted(ANCESTRY_GROUPS)}."
            )

        if g.n_trial < 0:
            failures.append(f"Group '{g.group}' has negative n_trial={g.n_trial}.")

        if g.ac is not None and g.an is not None:
            if g.ac < 0 or g.an < 0 or g.ac > g.an:
                failures.append(
                    f"Group '{g.group}' violates 0 <= ac <= an (ac={g.ac}, an={g.an})."
                )
            if g.an == 0 and g.allele_freq is not None:
                failures.append(
                    f"Group '{g.group}' has an=0 but non-None allele_freq={g.allele_freq}."
                )
        elif g.ac is not None or g.an is not None:
            failures.append(
                f"Group '{g.group}' has mismatched ac/an presence (ac={g.ac}, an={g.an})."
            )

        for field_name, val in (
            ("allele_freq", g.allele_freq),
            ("carrier_freq", g.carrier_freq),
            ("ci_low", g.ci_low),
            ("ci_high", g.ci_high),
            ("trial_share", g.trial_share),
            ("target_share", g.target_share),
            ("prob_fewer_than_k", g.prob_fewer_than_k),
        ):
            if not _in_unit_interval(val):
                failures.append(
                    f"Group '{g.group}': {field_name}={val} is outside [0, 1]."
                )

        # Credible interval bounds and point containment
        ci_fields = (g.ci_low, g.carrier_freq, g.ci_high)
        if any(v is not None for v in ci_fields):
            if not all(v is not None for v in ci_fields):
                failures.append(
                    f"Group '{g.group}' has incomplete carrier interval/point tuple "
                    f"(ci_low={g.ci_low}, carrier_freq={g.carrier_freq}, ci_high={g.ci_high})."
                )
            else:
                assert g.ci_low is not None and g.carrier_freq is not None and g.ci_high is not None
                if g.ci_low > g.ci_high:
                    failures.append(
                        f"Group '{g.group}' has inverted credible interval: ci_low={g.ci_low} > ci_high={g.ci_high}."
                    )
                if not (g.ci_low <= g.carrier_freq <= g.ci_high):
                    failures.append(
                        f"Group '{g.group}' credible interval [{g.ci_low}, {g.ci_high}] "
                        f"does not contain carrier_freq={g.carrier_freq}."
                    )

        # Insufficient-data threshold enforcement
        is_below_floor = insufficient_data(g.an, min_an=min_an)
        if is_below_floor and not g.insufficient_data:
            failures.append(
                f"Group '{g.group}' has an={g.an} below insufficient_data threshold ({min_an}) "
                f"but insufficient_data is False."
            )
        if is_below_floor and g.carrier_freq is not None:
            failures.append(
                f"Group '{g.group}' has an={g.an} below insufficient_data threshold ({min_an}) "
                f"but reports carrier_freq={g.carrier_freq}."
            )
        if not is_below_floor and g.insufficient_data:
            failures.append(
                f"Group '{g.group}' has sufficient an={g.an} (>= {min_an}) "
                f"but is marked insufficient_data=True."
            )

    if failures:
        return CheckResult(
            check_id="C3",
            name="bounds and internal consistency",
            adjudicating=True,
            status="FAIL",
            reason=failures[0],
            details=failures,
        )

    return CheckResult(
        check_id="C3",
        name="bounds and internal consistency",
        adjudicating=True,
        status="PASS",
        reason="All allele counts, frequencies, credible intervals, group labels, and AN thresholds are consistent.",
    )


def _check_c4_mapping_declared(
    result: GapResult,
    *,
    mapping: Optional[AncestryMapping],
    mapped_counts: Optional[MappedCounts],
    trial_counts: Optional[Mapping[str, float]],
    trials: Optional[Sequence[Trial]],
) -> CheckResult:
    """C4: mapping is declared, not inferred."""
    failures: list[str] = []

    resolved_mapping = mapping
    if resolved_mapping is None:
        all_mappings = load_all_mappings()
        resolved_mapping = all_mappings.get(result.mapping_version)

    if resolved_mapping is None:
        failures.append(
            f"Ancestry mapping version '{result.mapping_version}' is not declared."
        )
        return CheckResult(
            check_id="C4",
            name="mapping is declared, not inferred",
            adjudicating=True,
            status="FAIL",
            reason=failures[0],
            details=failures,
        )

    if not resolved_mapping.rows:
        failures.append(
            f"Ancestry mapping version '{resolved_mapping.version}' contains no mapping rows."
        )

    # Derive MappedCounts if trial_counts or trials were passed without mapped_counts
    resolved_mapped = mapped_counts
    if resolved_mapped is None and trial_counts is not None:
        resolved_mapped = map_counts(trial_counts, resolved_mapping)
    elif resolved_mapped is None and trials is not None:
        pooled: dict[str, float] = {}
        for t in trials:
            for b in t.baseline:
                if b.scheme == "race" and b.counts:
                    for cat, cnt in b.counts.items():
                        pooled[cat] = pooled.get(cat, 0.0) + float(cnt)
        if pooled:
            resolved_mapped = map_counts(pooled, resolved_mapping)

    if resolved_mapped is not None:
        if resolved_mapped.unmapped:
            unmapped_cats = ", ".join(
                f"{cat} ({cnt:g})" for cat, cnt in sorted(resolved_mapped.unmapped.items())
            )
            failures.append(
                f"Trial demographic group(s) absent from ancestry mapping '{resolved_mapping.version}': {unmapped_cats}."
            )

    # Inspect mapping rows that target groups with n_trial > 0 (or all rows if mapping has placeholders)
    rows_by_gnomad_group: dict[str, list[Any]] = {}
    for r in resolved_mapping.rows:
        if r.gnomad_group:
            rows_by_gnomad_group.setdefault(r.gnomad_group, []).append(r)

    for g in result.groups:
        if g.n_trial > 0:
            supporting_rows = [
                r for r in rows_by_gnomad_group.get(g.group, []) if r.weight > 0
            ]
            if not supporting_rows:
                failures.append(
                    f"Group '{g.group}' has n_trial={g.n_trial:g} but no explicit row in ancestry mapping '{resolved_mapping.version}'."
                )
            else:
                placeholder_rows = [r for r in supporting_rows if r.is_placeholder]
                if placeholder_rows:
                    cats = ", ".join(sorted({r.trial_category for r in placeholder_rows}))
                    failures.append(
                        f"Group '{g.group}' relies on placeholder mapping row(s) in version "
                        f"'{resolved_mapping.version}' (categories: {cats})."
                    )

    if resolved_mapping.placeholder_rows and not any(g.n_trial > 0 for g in result.groups):
        placeholder_cats = ", ".join(
            sorted({r.trial_category for r in resolved_mapping.placeholder_rows})
        )
        failures.append(
            f"Ancestry mapping version '{resolved_mapping.version}' contains placeholder row(s): {placeholder_cats}."
        )

    if failures:
        return CheckResult(
            check_id="C4",
            name="mapping is declared, not inferred",
            adjudicating=True,
            status="FAIL",
            reason=failures[0],
            details=failures,
        )

    return CheckResult(
        check_id="C4",
        name="mapping is declared, not inferred",
        adjudicating=True,
        status="PASS",
        reason=f"All contributing trial categories map via explicit, non-placeholder rows in mapping '{resolved_mapping.version}'.",
    )


def _check_c5_evidence_quality(
    result: GapResult,
    *,
    facts_source: Optional[Union[Mapping[str, Fact], Iterable[Fact], Any]],
    mapped_counts: Optional[MappedCounts],
    trials: Optional[Sequence[Trial]],
    variant: Optional[Variant],
    resolved_variant_ids: Optional[Sequence[str]],
    max_missing_fraction: float,
    run_started_at: Optional[datetime],
    run_finished_at: Optional[datetime],
    max_fact_span: timedelta = timedelta(hours=1),
) -> CheckResult:
    """C5: annotating evidence-quality check (can only PASS or WARN, never FAIL)."""
    warnings: list[str] = []

    # 1. Baseline demographics missing_fraction above threshold
    if trials is not None:
        for t in trials:
            for b in t.baseline:
                mf = b.missing_fraction
                if mf is not None and mf > max_missing_fraction:
                    warnings.append(
                        f"Trial '{t.nct_id}' baseline ({b.scheme}) missing_fraction={mf:.1%} "
                        f"exceeds threshold {max_missing_fraction:.1%}."
                    )
    if mapped_counts is not None:
        frac = mapped_counts.missing_fraction
        if frac is not None and frac > max_missing_fraction:
            warnings.append(
                f"Mapped trial counts have missing/unknown fraction {frac:.1%} "
                f"above threshold {max_missing_fraction:.1%}."
            )

    # 2. A gap resting on a single source
    resolved_facts, _ = _resolve_facts(result.fact_ids, facts_source)
    all_facts: list[Fact] = list(resolved_facts.values())
    if facts_source is not None and not hasattr(facts_source, "get_fact"):
        if isinstance(facts_source, Mapping):
            for f in facts_source.values():
                if isinstance(f, Fact) and f.id not in resolved_facts:
                    all_facts.append(f)
        else:
            for f in facts_source:
                if isinstance(f, Fact) and f.id not in resolved_facts:
                    all_facts.append(f)

    if all_facts:
        distinct_sources = {f.source for f in all_facts if f.source}
        if len(distinct_sources) <= 1:
            only_src = next(iter(distinct_sources), "unknown")
            warnings.append(
                f"GapResult rests on a single evidence source ('{only_src}')."
            )
    elif len(result.fact_ids) == 1:
        warnings.append(
            f"GapResult cites only a single fact_id ('{result.fact_ids[0]}')."
        )

    # 3. Variant whose rsID resolved to more than one variant_id
    if resolved_variant_ids is not None and len(set(resolved_variant_ids)) > 1:
        warnings.append(
            f"Variant rsID resolved to {len(set(resolved_variant_ids))} distinct variant_ids: "
            f"{', '.join(sorted(set(resolved_variant_ids)))}."
        )
    else:
        for f in all_facts:
            if f.field == "variant_search" and isinstance(f.value, Mapping):
                hits = f.value.get("hits")
                if isinstance(hits, list):
                    vids = {
                        h.get("variant_id")
                        for h in hits
                        if isinstance(h, Mapping) and h.get("variant_id")
                    }
                    if len(vids) > 1:
                        warnings.append(
                            f"Variant search for '{f.record_id}' resolved to {len(vids)} variant_ids: "
                            f"{', '.join(sorted(vids))}."
                        )

    # 4. A trial with no posted results
    if trials is not None:
        for t in trials:
            has_counts = any(bool(b.counts) for b in t.baseline)
            if not t.has_results or not t.baseline or not has_counts:
                warnings.append(
                    f"Trial '{t.nct_id}' has no posted baseline demographic results."
                )

    # 5. Facts retrieved outside the current run
    if all_facts:
        if run_started_at is not None or run_finished_at is not None:
            for f in all_facts:
                if run_started_at is not None and f.retrieved_at < run_started_at:
                    warnings.append(
                        f"Fact '{f.id}' was retrieved at {f.retrieved_at.isoformat()}, "
                        f"before run_started_at ({run_started_at.isoformat()})."
                    )
                elif run_finished_at is not None and f.retrieved_at > run_finished_at:
                    warnings.append(
                        f"Fact '{f.id}' was retrieved at {f.retrieved_at.isoformat()}, "
                        f"after run_finished_at ({run_finished_at.isoformat()})."
                    )
        else:
            timestamps = [f.retrieved_at for f in all_facts if f.retrieved_at is not None]
            if len(timestamps) >= 2 and (max(timestamps) - min(timestamps)) > max_fact_span:
                span = max(timestamps) - min(timestamps)
                warnings.append(
                    f"Facts span {span} across retrievals, indicating facts retrieved outside a single run."
                )

    if warnings:
        return CheckResult(
            check_id="C5",
            name="evidence quality",
            adjudicating=False,
            status="WARN",
            reason=warnings[0],
            details=warnings,
        )

    return CheckResult(
        check_id="C5",
        name="evidence quality",
        adjudicating=False,
        status="PASS",
        reason="No evidence-quality warnings triggered.",
    )


def criticise(
    result: GapResult,
    *,
    facts: Optional[Union[Mapping[str, Fact], Iterable[Fact], Any]] = None,
    mapping: Optional[AncestryMapping] = None,
    mapped_counts: Optional[MappedCounts] = None,
    trial_counts: Optional[Mapping[str, float]] = None,
    trials: Optional[Sequence[Trial]] = None,
    variant: Optional[Variant] = None,
    resolved_variant_ids: Optional[Sequence[str]] = None,
    min_an: int = 2000,
    target_carriers: float = 30.0,
    draws: int = 10000,
    seed: int = 0,
    tolerance: float = 1e-6,
    max_missing_fraction: float = 0.10,
    run_started_at: Optional[datetime] = None,
    run_finished_at: Optional[datetime] = None,
) -> CriticReport:
    """Run all adjudicating (C1-C4) and annotating (C5) checks on a ``GapResult``.

    Tier logic:
      - Any adjudicating check (C1-C4) FAIL -> ``"REJECTED"``
      - All adjudicating PASS, C5 WARN      -> ``"TIER B"``
      - All adjudicating PASS, no WARN      -> ``"TIER A"``
    """
    # Allow optional context attached as private/auxiliary attributes on result when called as criticise(result)
    facts = facts if facts is not None else getattr(result, "_facts", None)
    mapping = mapping if mapping is not None else getattr(result, "_mapping", None)
    mapped_counts = mapped_counts if mapped_counts is not None else getattr(result, "_mapped_counts", None)
    trial_counts = trial_counts if trial_counts is not None else getattr(result, "_trial_counts", None)
    trials = trials if trials is not None else getattr(result, "_trials", None)
    variant = variant if variant is not None else getattr(result, "_variant", None)
    resolved_variant_ids = (
        resolved_variant_ids
        if resolved_variant_ids is not None
        else getattr(result, "_resolved_variant_ids", None)
    )
    run_started_at = run_started_at if run_started_at is not None else getattr(result, "_run_started_at", None)
    run_finished_at = run_finished_at if run_finished_at is not None else getattr(result, "_run_finished_at", None)

    c1 = _check_c1_provenance(result, facts, variant, trials)
    c2 = _check_c2_arithmetic_replay(
        result,
        target_carriers=target_carriers,
        draws=draws,
        seed=seed,
        tolerance=tolerance,
    )
    c3 = _check_c3_bounds_and_consistency(result, variant=variant, min_an=min_an)
    c4 = _check_c4_mapping_declared(
        result,
        mapping=mapping,
        mapped_counts=mapped_counts,
        trial_counts=trial_counts,
        trials=trials,
    )
    c5 = _check_c5_evidence_quality(
        result,
        facts_source=facts,
        mapped_counts=mapped_counts,
        trials=trials,
        variant=variant,
        resolved_variant_ids=resolved_variant_ids,
        max_missing_fraction=max_missing_fraction,
        run_started_at=run_started_at,
        run_finished_at=run_finished_at,
    )

    checks = {"C1": c1, "C2": c2, "C3": c3, "C4": c4, "C5": c5}

    if any(chk.status == "FAIL" for chk in (c1, c2, c3, c4)):
        tier: Tier = "REJECTED"
    elif c5.status == "WARN":
        tier = "TIER B"
    else:
        tier = "TIER A"

    return CriticReport(tier=tier, checks=checks)
