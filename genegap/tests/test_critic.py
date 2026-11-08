"""Offline, deterministic tests for the GeneGap Critic (src/genegap/critic.py).

Uses the real recorded payloads in smoke/responses/*.json (never the network).
Covers:
  - Real-fixture GapResult reaching TIER A
  - Real-fixture GapResult downgraded to TIER B via each C5 warning condition
  - Deliberately broken cases for each adjudicating check (C1, C2, C3, C4) -> REJECTED
  - Verification that critic.py imports no HTTP/LLM client and performs no network I/O
"""
from __future__ import annotations

import ast
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import genegap.critic as critic_module
from genegap.critic import CriticReport, criticise
from genegap.fixtures import DEMO_DRUG, DEMO_NCT_ID, DEMO_RSID, DEMO_VARIANT_ID, offline_clients
from genegap.gap import compute_gap
from genegap.mapping import AncestryMapping, MappingRow, load_all_mappings, map_counts
from genegap.population import load_target_population
from genegap.schemas import BaselineDemographics, Fact, GapResult, Trial
from genegap.store import Store
from genegap.trials import collect_trials, pooled_race_counts


@pytest.fixture
def real_fixture_bundle(tmp_path):
    """Build a complete, real-fixture GapResult and supporting objects from smoke/responses/*.json."""
    store = Store(str(tmp_path / "critic_test.sqlite"))
    gnomad, clinvar, ctgov, _ = offline_clients(store)

    # Load real variant (10-94781859-G-A), real ClinVar records, and real trial (NCT01609842)
    variant = gnomad.variant(DEMO_VARIANT_ID)
    assert variant is not None
    clinvar_facts = clinvar.lookup_rsid(DEMO_RSID)
    assert clinvar_facts

    trials, _ = collect_trials(ctgov, intervention=DEMO_DRUG, max_trials=5)
    trial = next(t for t in trials if t.nct_id == DEMO_NCT_ID)
    race_counts, _, _ = pooled_race_counts([trial])
    assert race_counts

    mappings = load_all_mappings()
    mapping_a = mappings["A"]
    mapped_a = map_counts(race_counts, mapping_a)
    target = load_target_population("us_acs_antiplatelet")

    gap_run = compute_gap(
        variant,
        mapped_a,
        target,
        inheritance="dominant",
        target_carriers=30.0,
        min_an=2000,
    )
    result = gap_run.result.model_copy(deep=True)
    # Include the trial baseline fact_ids alongside the gnomAD frequency fact_ids
    trial_fids = list(trial.fact_ids)
    for b in trial.baseline:
        trial_fids.extend(b.fact_ids)
    for fid in trial_fids:
        if fid and fid not in result.fact_ids:
            result.fact_ids.append(fid)

    return {
        "store": store,
        "variant": variant,
        "trial": trial,
        "race_counts": race_counts,
        "mapping_a": mapping_a,
        "mappings": mappings,
        "mapped_a": mapped_a,
        "target": target,
        "result": result,
    }


# ---------------------------------------------------------------------------
# 1. TIER A — Clean GapResult built from real smoke/responses/*.json fixtures
# ---------------------------------------------------------------------------


def test_real_fixture_gap_result_reaches_tier_a(real_fixture_bundle):
    b = real_fixture_bundle
    # Single-argument call required by signature: criticise(result: GapResult) -> CriticReport
    report_simple = criticise(b["result"])
    assert isinstance(report_simple, CriticReport)
    assert report_simple.tier == "TIER A"
    assert report_simple.passed is True
    for cid in ("C1", "C2", "C3", "C4", "C5"):
        assert report_simple[cid].status == "PASS", f"{cid} did not PASS: {report_simple[cid].reason}"

    # Full-context call verifying provenance against Store, Trial, Variant, and Mapping A
    report_full = criticise(
        b["result"],
        facts=b["store"],
        mapping=b["mapping_a"],
        mapped_counts=b["mapped_a"],
        trials=[b["trial"]],
        variant=b["variant"],
        resolved_variant_ids=[DEMO_VARIANT_ID],
    )
    assert report_full.tier == "TIER A"
    assert report_full.passed is True
    assert report_full.c1.status == "PASS"
    assert report_full.c2.status == "PASS"
    assert report_full.c3.status == "PASS"
    assert report_full.c4.status == "PASS"
    assert report_full.c5.status == "PASS"


# ---------------------------------------------------------------------------
# 2. TIER B — Annotating C5 warnings downgrade TIER A to TIER B without blocking
# ---------------------------------------------------------------------------


def test_c5_multiallelic_rsid_from_real_fixture_warns_to_tier_b(real_fixture_bundle):
    """In smoke/responses/gnomad_variant_search.json, rs4244285 resolves to 3 variant_ids."""
    b = real_fixture_bundle
    gnomad, _, _, _ = offline_clients(b["store"])
    resolved_ids = gnomad.variant_ids_for_rsid(DEMO_RSID)
    assert len(resolved_ids) == 3  # 10-94781859-G-A, G-C, G-T

    report = criticise(
        b["result"],
        facts=b["store"],
        mapping=b["mapping_a"],
        mapped_counts=b["mapped_a"],
        trials=[b["trial"]],
        variant=b["variant"],
        resolved_variant_ids=resolved_ids,
    )
    assert report.tier == "TIER B"
    assert report.passed is True
    assert all(report[c].status == "PASS" for c in ("C1", "C2", "C3", "C4"))
    assert report.c5.status == "WARN"
    assert "3 distinct variant_ids" in report.c5.reason


def test_c5_high_missing_fraction_warns_to_tier_b(real_fixture_bundle):
    b = real_fixture_bundle
    high_missing_trial = b["trial"].model_copy(
        update={
            "baseline": [
                BaselineDemographics(
                    nct_id=b["trial"].nct_id,
                    scheme="race",
                    counts={"White": 800, "Unknown or Not Reported": 200},
                    total_participants=1000,
                    fact_ids=list(b["trial"].fact_ids),
                )
            ]
        }
    )
    report = criticise(
        b["result"],
        facts=b["store"],
        trials=[high_missing_trial],
        max_missing_fraction=0.10,
    )
    assert report.tier == "TIER B"
    assert report.c5.status == "WARN"
    assert "missing_fraction" in report.c5.reason


def test_c5_single_source_warns_to_tier_b(real_fixture_bundle):
    b = real_fixture_bundle
    # Keep only gnomAD fact IDs (drop the ClinicalTrials.gov fact_id)
    gnomad_only_result = b["result"].model_copy(
        update={"fact_ids": [f.fact_id for f in b["variant"].frequencies if f.fact_id]}
    )
    report = criticise(gnomad_only_result, facts=b["store"])
    assert report.tier == "TIER B"
    assert report.c5.status == "WARN"
    assert "single evidence source" in report.c5.reason


def test_c5_trial_without_posted_results_warns_to_tier_b(real_fixture_bundle):
    b = real_fixture_bundle
    no_results_trial = Trial(
        nct_id="NCT00000000",
        title="Completed trial with no posted baseline table",
        status="COMPLETED",
        has_results=False,
        baseline=[],
    )
    report = criticise(
        b["result"],
        facts=b["store"],
        trials=[b["trial"], no_results_trial],
    )
    assert report.tier == "TIER B"
    assert report.c5.status == "WARN"
    assert "no posted baseline demographic results" in report.c5.reason


def test_c5_facts_retrieved_outside_current_run_warns_to_tier_b(real_fixture_bundle):
    b = real_fixture_bundle
    future_start = datetime.now(timezone.utc) + timedelta(days=1)
    report = criticise(
        b["result"],
        facts=b["store"],
        run_started_at=future_start,
    )
    assert report.tier == "TIER B"
    assert report.c5.status == "WARN"
    assert "before run_started_at" in report.c5.reason


# ---------------------------------------------------------------------------
# 3. Adjudicating checks (C1, C2, C3, C4) -> FAIL and REJECTED
# ---------------------------------------------------------------------------


def test_c1_fails_when_fact_is_llm_extracted(real_fixture_bundle):
    """C1: Any numeric value whose supporting Fact has method == 'llm_extracted' is a FAIL."""
    b = real_fixture_bundle
    llm_fact = Fact(
        source="LiteraturePDF",
        record_id="PMID:123456",
        field="ac_an:eas",
        value={"ac": 11167, "an": 36782, "dataset": "gnomad_r4"},
        url="https://example.org/paper.pdf",
        method="llm_extracted",
    )
    b["store"].put_fact(llm_fact)

    broken = b["result"].model_copy(deep=True)
    broken.fact_ids.append(llm_fact.id)

    report = criticise(broken, facts=b["store"])
    assert report.tier == "REJECTED"
    assert report.passed is False
    assert report.c1.status == "FAIL"
    assert "llm_extracted" in report.c1.reason
    # Other adjudicating checks still pass on the untampered numbers
    assert report.c2.status == "PASS"
    assert report.c3.status == "PASS"
    assert report.c4.status == "PASS"


def test_c1_fails_when_fact_ids_missing(real_fixture_bundle):
    """C1: Numeric GapResult with no cited fact_ids must FAIL."""
    b = real_fixture_bundle
    broken = b["result"].model_copy(update={"fact_ids": []})

    report = criticise(broken)
    assert report.tier == "REJECTED"
    assert report.c1.status == "FAIL"
    assert "no supporting fact_ids" in report.c1.reason


def test_c2_fails_when_derived_arithmetic_disagrees_with_stats_core(real_fixture_bundle):
    """C2: Any disagreement between stored derived numbers and stats/core.py replay is a FAIL."""
    b = real_fixture_bundle
    broken = b["result"].model_copy(deep=True)
    eas_group = next(g for g in broken.groups if g.group == "eas")
    # Perturb carrier_freq by 0.01 (well outside tolerance)
    assert eas_group.carrier_freq is not None
    eas_group.carrier_freq += 0.01

    report = criticise(broken)
    assert report.tier == "REJECTED"
    assert report.passed is False
    assert report.c2.status == "FAIL"
    assert "carrier_freq" in report.c2.reason
    assert "eas" in report.c2.reason


def test_c3_fails_on_bounds_and_internal_consistency_violations(real_fixture_bundle):
    """C3: ac > an, frequency outside [0, 1], CI not containing point, bad group label, or an < min_an."""
    b = real_fixture_bundle

    # Case A: ac > an
    broken_ac = b["result"].model_copy(deep=True)
    nfe = next(g for g in broken_ac.groups if g.group == "nfe")
    nfe.ac = (nfe.an or 0) + 10
    rep_ac = criticise(broken_ac)
    assert rep_ac.tier == "REJECTED"
    assert rep_ac.c3.status == "FAIL"
    assert "ac <= an" in rep_ac.c3.reason

    # Case B: Credible interval does not contain carrier_freq
    broken_ci = b["result"].model_copy(deep=True)
    eas = next(g for g in broken_ci.groups if g.group == "eas")
    eas.ci_low = 0.60
    eas.ci_high = 0.70  # eas.carrier_freq is ~0.515
    rep_ci = criticise(broken_ci)
    assert rep_ci.tier == "REJECTED"
    assert rep_ci.c3.status == "FAIL"
    assert "credible interval" in rep_ci.c3.reason

    # Case C: Disallowed ancestry group label (e.g. karyotype cohort 'XX')
    broken_grp = b["result"].model_copy(deep=True)
    broken_grp.groups[0].group = "XX"
    rep_grp = criticise(broken_grp)
    assert rep_grp.tier == "REJECTED"
    assert rep_grp.c3.status == "FAIL"
    assert "allow-list" in rep_grp.c3.reason

    # Case D: an below insufficient_data threshold (ami has an=910 < 2000) without insufficient_data=True
    broken_an = b["result"].model_copy(deep=True)
    ami = next(g for g in broken_an.groups if g.group == "ami")
    ami.insufficient_data = False
    rep_an = criticise(broken_an, min_an=2000)
    assert rep_an.tier == "REJECTED"
    assert rep_an.c3.status == "FAIL"
    assert "insufficient_data threshold" in rep_an.c3.reason


def test_c4_fails_on_placeholder_or_absent_mapping(real_fixture_bundle):
    """C4: A group that is absent from mapping, or whose mapping row is a placeholder, is a FAIL."""
    b = real_fixture_bundle

    # Case A: Mapping version B contains PLACEHOLDER rows in config/ancestry_mapping_B.csv
    mapping_b = b["mappings"]["B"]
    mapped_b = map_counts(b["race_counts"], mapping_b)
    run_b = compute_gap(b["variant"], mapped_b, b["target"], inheritance="dominant")
    rep_b = criticise(run_b.result)
    assert rep_b.tier == "REJECTED"
    assert rep_b.passed is False
    assert rep_b.c4.status == "FAIL"
    assert "placeholder" in rep_b.c4.reason.lower()

    # Case B: Trial demographic group absent from mapping (unmapped category)
    incomplete_mapping = AncestryMapping(
        version="A",
        path=Path("incomplete_mapping_A.csv"),
        rows=[
            MappingRow(
                trial_category="White",
                gnomad_group="nfe",
                weight=1.0,
                confidence="high",
                source="gnomAD v4 ancestry definitions",
                rationale="1-to-1",
            )
        ],
    )
    mapped_unmapped = map_counts(
        {"White": 100, "Black or African American": 50}, incomplete_mapping
    )
    assert "Black or African American" in mapped_unmapped.unmapped
    rep_unmapped = criticise(
        b["result"],
        mapping=incomplete_mapping,
        mapped_counts=mapped_unmapped,
    )
    assert rep_unmapped.tier == "REJECTED"
    assert rep_unmapped.c4.status == "FAIL"
    assert "absent from ancestry mapping" in rep_unmapped.c4.reason


# ---------------------------------------------------------------------------
# 4. Hard rule: critic.py performs NO network I/O and imports no HTTP/LLM client
# ---------------------------------------------------------------------------


def test_critic_performs_no_network_io(real_fixture_bundle, monkeypatch):
    """Verify critic.py imports no network/LLM modules and makes zero socket/HTTP calls."""
    critic_path = Path(critic_module.__file__).resolve()
    tree = ast.parse(critic_path.read_text(), filename=str(critic_path))

    forbidden_prefixes = (
        "requests",
        "urllib",
        "http",
        "httpx",
        "socket",
        "aiohttp",
        "google",
        "genai",
        "genegap.clients",
        "genegap.agents",
    )

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level > 0:
                mod = f"genegap.{mod}" if mod else "genegap"
            imported_names.append(mod)

    for mod_name in imported_names:
        assert not mod_name.startswith(forbidden_prefixes), (
            f"critic.py must not import network or LLM module '{mod_name}'"
        )

    # Also block socket creation at runtime to prove criticise() touches no network
    def _forbid_socket(*_args, **_kwargs):
        raise AssertionError("critic.py attempted to open a network socket!")

    monkeypatch.setattr(socket, "socket", _forbid_socket)
    monkeypatch.setattr(socket, "create_connection", _forbid_socket)

    report = criticise(real_fixture_bundle["result"])
    assert report.tier == "TIER A"
