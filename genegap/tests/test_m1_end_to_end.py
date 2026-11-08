"""Milestone M1 (task 2.7): the full deterministic chain, end to end, with no LLM.

This runs against the real recorded gnomAD / ClinVar / ClinicalTrials.gov payloads in
smoke/responses/ through the real client code, so it is a genuine integration test of
tasks 2.1 to 2.6 rather than a test of stubs.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from genegap.fixtures import offline_clients
from genegap.gap import compute_gap, robust_across_mappings
from genegap.mapping import load_all_mappings, map_counts
from genegap.population import load_target_population
from genegap.store import Store
from genegap.trials import collect_trials, pooled_race_counts
from genegap.variants import variants_for_gene

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "m1_clopidogrel.py"


@pytest.fixture
def offline(tmp_path):
    """Clients backed by a throwaway database, serving only the recorded responses."""
    return offline_clients(Store(str(tmp_path / "m1.sqlite")))


def test_offline_seeding_reports_what_it_replayed(offline):
    *_clients, seeded = offline
    assert any("gnomAD" in s for s in seeded)
    assert any("ClinVar" in s for s in seeded)
    assert any("ClinicalTrials.gov" in s for s in seeded)


def test_step_1_cyp2c19_resolves_to_real_gnomad_frequencies(offline):
    gnomad, clinvar, _ctgov, _ = offline
    table = variants_for_gene("CYP2C19", gnomad, clinvar)

    assert table.assessable and table.inheritance == "dominant"
    star2 = next(v for v in table.variants if v.rsid == "rs4244285")
    assert star2.variant_id == "10-94781859-G-A"
    assert star2.star_allele == "*2" and star2.function == "no function"
    assert star2.classification        # ClinVar label came through

    freqs = {f.group: f for f in star2.frequencies}
    assert set(freqs) == {"afr", "ami", "amr", "asj", "eas", "fin", "mid", "nfe", "remaining", "sas"}
    # The known signal: CYP2C19*2 is far more common in East and South Asian ancestry.
    assert freqs["eas"].af > freqs["nfe"].af
    assert freqs["sas"].af > freqs["nfe"].af


def test_an_unavailable_allele_does_not_lose_the_one_that_resolved(offline):
    """rs4244285 is multiallelic; only one allele is recorded offline."""
    gnomad, clinvar, _ctgov, _ = offline
    table = variants_for_gene("CYP2C19", gnomad, clinvar)
    assert any(v.variant_id == "10-94781859-G-A" for v in table.variants)
    assert any("could not be fetched" in n for n in table.notes)


def test_step_2_trials_yield_race_counts_matching_the_registry(offline):
    _g, _c, ctgov, _ = offline
    trials, notes = collect_trials(ctgov, intervention="clopidogrel", max_trials=5)
    counts, used, _ = pooled_race_counts(trials)

    assert "NCT01609842" in used
    assert counts["White"] == 11935 and counts["Black or African American"] == 2329
    assert sum(counts.values()) == 14573
    # Trials whose full record is not recorded are skipped with a note, not silently.
    assert any("could not fetch" in n for n in notes)


def test_full_chain_produces_a_gap_table_under_every_mapping(offline):
    gnomad, clinvar, ctgov, _ = offline
    table = variants_for_gene("CYP2C19", gnomad, clinvar)
    trials, _ = collect_trials(ctgov, intervention="clopidogrel", max_trials=5)
    counts, _, _ = pooled_race_counts(trials)
    target = load_target_population("us_acs_antiplatelet")
    variant = next(v for v in table.variants if v.rsid == "rs4244285")

    runs = {
        version: compute_gap(variant, map_counts(counts, mapping), target, inheritance=table.inheritance)
        for version, mapping in load_all_mappings().items()
    }
    assert set(runs) >= {"A", "B", "C"}

    for version, run in runs.items():
        rows = {r.group: r for r in run.result.groups}
        assert run.result.mapping_version == version
        # ami has only 910 alleles in gnomAD, below the 2000 floor.
        assert rows["ami"].insufficient_data is True
        # Every usable row must carry its own uncertainty and its source counts.
        for g, row in rows.items():
            if row.insufficient_data:
                continue
            assert row.ci_low <= row.carrier_freq <= row.ci_high
            assert row.ac is not None and row.an is not None
            assert row.required_n and row.required_n > 0


def test_the_known_clopidogrel_gap_is_found_and_is_robust(offline):
    """East and South Asian ancestry carry CYP2C19*2 most often and are thinly enrolled here.

    This is the headline finding the demo rests on, so it is pinned by a test.
    """
    gnomad, clinvar, ctgov, _ = offline
    table = variants_for_gene("CYP2C19", gnomad, clinvar)
    trials, _ = collect_trials(ctgov, intervention="clopidogrel", max_trials=5)
    counts, _, _ = pooled_race_counts(trials)
    target = load_target_population("us_acs_antiplatelet")
    variant = next(v for v in table.variants if v.rsid == "rs4244285")

    runs = {
        version: compute_gap(variant, map_counts(counts, mapping), target)
        for version, mapping in load_all_mappings().items()
    }
    assert robust_across_mappings(runs, "eas") is True
    assert robust_across_mappings(runs, "sas") is True
    # nfe is the group this trial over-enrolled, so it must not be flagged as a gap.
    assert robust_across_mappings(runs, "nfe") is False


def test_the_whole_script_runs_offline_and_exits_clean(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--offline", "--db", str(tmp_path / "script.sqlite")],
        capture_output=True, text=True, cwd=str(ROOT), timeout=300,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    out = proc.stdout
    assert "OFFLINE MODE" in out
    assert "ROBUSTNESS CHECK" in out
    assert "No language model produced any number in this run." in out
    assert "not clinical advice" in out


def test_an_unassessable_gene_refuses_rather_than_guessing(tmp_path):
    """G6PD is X-linked and HLA is not covered by gnomAD; saying so is the correct answer."""
    for gene in ("G6PD", "HLA-B"):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--offline", "--gene", gene,
             "--db", str(tmp_path / f"{gene}.sqlite")],
            capture_output=True, text=True, cwd=str(ROOT), timeout=300,
        )
        assert proc.returncode == 1
        assert "not assessable" in proc.stdout
        assert "correct answer" in proc.stdout


def test_the_sanity_check_script_runs_and_nothing_fails(tmp_path):
    """Task 2.8 / D6: the automated half of the sanity check must stay green."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sanity_check_m1.py"),
         "--strict", "--out", str(tmp_path / "log.csv")],
        capture_output=True, text=True, cwd=str(ROOT), timeout=300,
    )
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-3000:]
    assert "0 failed" in proc.stdout
    # The items that need a human or the network must stay visible, not quietly disappear.
    assert "CPIC allele-frequency tables" in proc.stdout
    assert "NOT ESTABLISHED" in proc.stdout
    assert (tmp_path / "log.csv").exists()
