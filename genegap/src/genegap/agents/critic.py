"""Independent numerical/provenance audit of coverage hypotheses.

Rebuilds each result from its stored input Facts. Clinical evidence grading is
explicit human curation, never inferred from variant frequency or paper titles.
This is a deterministic audit, not a claim of external clinical verification.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..gap import compute_gap, robust_across_mappings
from ..mapping import AncestryMapping, MappingRow, map_counts
from ..population import PopulationRow, TargetPopulation
from ..schemas import AncestryFrequency, Flag, GapResult, Variant
from ..store import Store


@dataclass
class Critique:
    passed: bool = False
    issues: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)


def _equal(left, right) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isfinite(left) and math.isfinite(right) and math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)
    return left == right


def audit_flag(flag: Flag, store: Store) -> Critique:
    result = Critique()
    facts = [store.get_fact(key) for key in flag.fact_ids]
    if not facts or any(fact is None or fact.id != key for fact, key in zip(facts, flag.fact_ids)):
        result.issues.append("A supporting source Fact is missing or has a mismatched identifier.")
        return result
    result.checks["source_records_present"] = True
    evidence = [fact for fact in facts if fact.source == "GeneGap.human_evidence"]
    supported = any(
        isinstance(fact.value, dict)
        and fact.value.get("evidence_tier") == flag.evidence_tier
        and fact.value.get("citations") == flag.citations
        and fact.value.get("reviewer") and fact.value.get("note")
        for fact in evidence
    )
    result.checks["reviewer_supplied_evidence"] = bool(supported and flag.evidence_tier and flag.citations)
    if not result.checks["reviewer_supplied_evidence"]:
        result.issues.append("A reviewer must supply an evidence tier, citations and supporting context first.")
    if re.search(r"\b(proves|guarantees|prescribe|dosage)\b", flag.claim, re.I):
        result.issues.append("The claim contains an unsupported causal or treatment assertion.")
    group_match = re.search(r"\bin group ([a-z_]+)\b", flag.claim)
    if not group_match or "suggests lower expected-carrier representation" not in flag.claim:
        result.issues.append("This audit supports the generated, qualified coverage-hypothesis format only.")
    try:
        raw_frequencies = [fact for fact in facts if fact.source == "gnomAD" and fact.record_id == flag.variant_id and fact.field.startswith("ac_an:")]
        if not raw_frequencies:
            raise ValueError("No source allele counts support this variant.")
        rules = [fact.value for fact in facts if fact.field == "gene_rule"]
        if len(rules) != 1:
            raise ValueError("Exactly one gene-rule record is needed.")
        rule = rules[0]
        frequencies = [AncestryFrequency(group=fact.field.split(":", 1)[1], ac=fact.value["ac"], an=fact.value["an"], fact_id=fact.id) for fact in raw_frequencies]
        variant = Variant(variant_id=flag.variant_id, gene=rule["gene"], frequencies=frequencies, fact_ids=[fact.id for fact in raw_frequencies])
        rows = [PopulationRow(**fact.value) for fact in facts if fact.field == "target_population_row"]
        if not rows or len({row.population_id for row in rows}) != 1:
            raise ValueError("One consistent target-population table is needed.")
        target = TargetPopulation(rows[0].population_id, rows[0].label, rows)
        pooled = [fact.value for fact in facts if fact.field == "pooled_race_counts"]
        if len(pooled) != 1:
            raise ValueError("Exactly one pooled trial-count record is needed.")
        recomputed = {}
        for version in ("A", "B", "C"):
            mapping_rows = [MappingRow(**fact.value) for fact in facts if fact.field == "ancestry_mapping_row" and fact.record_id.startswith(version + ":")]
            computed = [fact for fact in facts if fact.source == "GeneGap.stats" and fact.field == "gap_result" and fact.value["result"]["mapping_version"] == version]
            if not mapping_rows or len(computed) != 1:
                raise ValueError(f"Mapping {version} needs its input rows and one calculated result.")
            mapping = AncestryMapping(version, Path(f"stored_mapping_{version}.csv"), mapping_rows)
            expected = compute_gap(variant, map_counts(pooled[0]["counts"], mapping), target,
                                   inheritance=rule["inheritance"], min_an=rule["min_an"],
                                   target_carriers=computed[0].value["target_carriers"])
            actual = GapResult.model_validate(computed[0].value["result"])
            fields = {"variant_id", "gene", "inheritance", "mapping_version", "groups"}
            matches = _equal(actual.model_dump(include=fields), expected.result.model_dump(include=fields))
            result.checks[f"mapping_{version}_numbers"] = matches
            if not matches:
                result.issues.append(f"Mapping {version}'s reported values differ from the stored source inputs.")
            recomputed[version] = expected
        robust = bool(group_match and robust_across_mappings(recomputed, group_match.group(1)) is True)
        result.checks["mapping_robustness"] = robust
        if not robust or flag.robust_across_mappings is not True:
            result.issues.append("The selected group's under-representation is not supported across A/B/C.")
        result.checks["qualified_research_claim"] = not any(word in flag.claim.lower() for word in ("proves", "guarantees", "prescribe", "dosage"))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        result.issues.append(f"Numerical audit could not complete: {exc}")
    result.passed = not result.issues and all(result.checks.values())
    return result
