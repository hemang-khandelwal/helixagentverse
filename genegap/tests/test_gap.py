"""Tests for the gap assembler: variant + mapped trial counts + target population -> GapResult.

These check the wiring and the worked example from the project summary. The statistics
themselves are tested in test_stats.py; what matters here is that the right numbers are
asked for, that missing inputs stay missing, and that the robustness verdict is honest.
"""
import math

import pytest

from genegap.gap import compute_gap, format_gap_table, robust_across_mappings
from genegap.mapping import MappedCounts
from genegap.population import PopulationRow, TargetPopulation
from genegap.schemas import AncestryFrequency, Variant


def variant(freqs, vid="1-1-A-G", gene="GENEX"):
    return Variant(
        variant_id=vid, gene=gene, dataset="gnomad_r4",
        frequencies=[AncestryFrequency(group=g, ac=ac, an=an, fact_id=f"F-{g}") for g, (ac, an) in freqs.items()],
    )


def target(shares, basis="burden"):
    return TargetPopulation(
        population_id="t", label="Test population",
        rows=[PopulationRow("t", "Test population", g, s, "src", "", "2024", "UK", basis, "")
              for g, s in shares.items()],
    )


def mapped(groups, version="A", total_reported=None):
    total = total_reported if total_reported is not None else int(sum(groups.values()))
    return MappedCounts(version=version, groups=dict(groups), total_reported=total)


# ---- the worked example from the project summary ----
def test_worked_example_from_the_project_summary():
    """p = 0.30 in group A -> carrier 0.51; 30 of 1000 participants; 12% of the target.

    Expected carriers ~= 15, and about 59 participants are needed for 30 expected carriers.
    """
    v = variant({"afr": (3000, 10000), "nfe": (1000, 10000)})
    run = compute_gap(
        v,
        mapped({"afr": 30, "nfe": 970}, total_reported=1000),
        target({"afr": 0.12, "nfe": 0.88}),
        target_carriers=30,
    )
    row = next(r for r in run.result.groups if r.group == "afr")

    assert row.allele_freq == pytest.approx(0.30)
    assert row.carrier_freq == pytest.approx(0.51)
    assert row.expected_carriers == pytest.approx(15.3, abs=0.05)
    assert row.required_n == math.ceil(30 / 0.51)        # 59
    assert row.trial_share == pytest.approx(0.03)
    assert row.enrolment_ratio == pytest.approx(0.25)    # 3% enrolled vs 12% of the target
    assert row.ci_low < row.carrier_freq < row.ci_high


def test_every_row_carries_its_ac_an_so_a_reader_can_recompute_it():
    v = variant({"eas": (300, 1000), "nfe": (100, 1000)})
    run = compute_gap(v, mapped({"eas": 50, "nfe": 50}), target({"eas": 0.5, "nfe": 0.5}), min_an=100)
    for row in run.result.groups:
        assert row.ac is not None and row.an is not None
        assert row.allele_freq == pytest.approx(row.ac / row.an)


def test_a_group_below_the_an_floor_is_insufficient_data_not_a_number():
    v = variant({"ami": (5, 100), "nfe": (1000, 10000)})
    run = compute_gap(v, mapped({"ami": 10, "nfe": 90}), target({"ami": 0.1, "nfe": 0.9}), min_an=2000)
    ami = next(r for r in run.result.groups if r.group == "ami")
    assert ami.insufficient_data is True
    assert ami.carrier_freq is None and ami.expected_carriers is None and ami.required_n is None
    assert any("fewer than 2000 alleles" in n for n in run.notes)


def test_a_group_the_trial_never_enrolled_still_appears_with_zero():
    v = variant({"eas": (300, 10000), "nfe": (100, 10000)})
    run = compute_gap(v, mapped({"nfe": 100}), target({"eas": 0.2, "nfe": 0.8}))
    eas = next(r for r in run.result.groups if r.group == "eas")
    assert eas.n_trial == 0 and eas.expected_carriers == 0.0
    assert eas.required_n is not None       # the fix is still computable
    assert any("maps nobody to" in n and "eas" in n for n in run.notes)


def test_a_group_with_participants_but_no_target_share_is_called_out():
    v = variant({"fin": (300, 10000), "nfe": (100, 10000)})
    run = compute_gap(v, mapped({"fin": 50, "nfe": 50}), target({"nfe": 1.0}))
    fin = next(r for r in run.result.groups if r.group == "fin")
    assert fin.enrolment_ratio is None and fin.carrier_ratio is None
    assert any("no share for" in n and "fin" in n for n in run.notes)


def test_x_linked_genes_get_no_carrier_number_and_say_why():
    v = variant({"afr": (1000, 10000)})
    run = compute_gap(v, mapped({"afr": 100}), target({"afr": 1.0}), inheritance="x_linked")
    assert all(r.carrier_freq is None for r in run.result.groups)
    assert any("X-linked" in n for n in run.notes)


def test_recessive_inheritance_uses_the_squared_formula():
    v = variant({"afr": (2000, 10000)})
    run = compute_gap(v, mapped({"afr": 100}), target({"afr": 1.0}), inheritance="recessive")
    row = run.result.groups[0]
    assert row.carrier_freq == pytest.approx(0.04)       # p^2 = 0.2^2
    assert run.result.inheritance == "recessive"


def test_probability_of_fewer_than_k_carriers_is_recorded():
    v = variant({"eas": (3000, 10000)})
    run = compute_gap(v, mapped({"eas": 5}), target({"eas": 1.0}), k=10)
    row = run.result.groups[0]
    # 5 participants cannot hold 10 carriers, so the probability is certain.
    assert row.prob_fewer_than_k == pytest.approx(1.0)
    assert row.k_threshold == 10


def test_facts_from_the_variant_are_carried_into_the_result():
    v = variant({"eas": (300, 10000), "nfe": (100, 10000)})
    run = compute_gap(v, mapped({"eas": 50, "nfe": 50}), target({"eas": 0.5, "nfe": 0.5}))
    assert "F-eas" in run.result.fact_ids and "F-nfe" in run.result.fact_ids


def test_no_mappable_participants_is_a_note_not_a_crash():
    v = variant({"nfe": (100, 10000)})
    run = compute_gap(v, mapped({}, total_reported=50), target({"nfe": 1.0}))
    assert any("No trial participants could be mapped" in n for n in run.notes)
    assert all(r.enrolment_ratio is None for r in run.result.groups)


def test_the_target_populations_own_caveats_reach_the_output():
    v = variant({"nfe": (100, 10000)})
    t = target({"nfe": 1.0}, basis="assumption")
    t.notes.append("100% of this target population is an ASSUMPTION.")
    run = compute_gap(v, mapped({"nfe": 100}), t)
    assert any("ASSUMPTION" in n for n in run.notes)


# ---- robustness across mappings ----
def build_runs(trial_counts_by_version):
    v = variant({"eas": (3000, 10000), "nfe": (1000, 10000)})
    t = target({"eas": 0.5, "nfe": 0.5})
    return {
        version: compute_gap(v, mapped(counts, version=version), t)
        for version, counts in trial_counts_by_version.items()
    }


def test_a_gap_present_under_every_mapping_is_robust():
    runs = build_runs({"A": {"eas": 2, "nfe": 998}, "B": {"eas": 5, "nfe": 995}})
    assert robust_across_mappings(runs, "eas") is True


def test_a_gap_present_under_only_one_mapping_is_not_robust():
    runs = build_runs({"A": {"eas": 2, "nfe": 998}, "B": {"eas": 900, "nfe": 100}})
    assert robust_across_mappings(runs, "eas") is False


def test_a_well_represented_group_is_not_reported_as_robust():
    runs = build_runs({"A": {"eas": 900, "nfe": 100}, "B": {"eas": 950, "nfe": 50}})
    assert robust_across_mappings(runs, "eas") is False


def test_a_missing_ratio_anywhere_gives_no_verdict():
    v = variant({"eas": (3000, 10000)})
    runs = {
        "A": compute_gap(v, mapped({"eas": 100}), target({"eas": 1.0})),
        "B": compute_gap(v, mapped({"eas": 100}), target({"nfe": 1.0})),   # no eas share
    }
    assert robust_across_mappings(runs, "eas") is None


# ---- presentation ----
def test_the_printed_table_shows_intervals_and_the_insufficient_data_flag():
    v = variant({"eas": (3000, 10000), "ami": (5, 100)})
    run = compute_gap(v, mapped({"eas": 50, "ami": 50}), target({"eas": 0.5, "ami": 0.5}))
    text = format_gap_table(run)
    assert "insufficient data" in text
    assert "95% CI" in text
    assert "What the numbers assume:" in text
