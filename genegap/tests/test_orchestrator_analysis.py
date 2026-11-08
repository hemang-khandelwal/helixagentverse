"""Real recorded-data checks for the draft-producing analysis executor."""
import pytest
import requests

from genegap.agents import analysis
from genegap.fixtures import DEMO_VARIANT_ID, offline_clients
from genegap.gap import compute_gap
from genegap.mapping import load_all_mappings, map_counts
from genegap.population import load_target_population
from genegap.schemas import Fact
from genegap.store import Store
from genegap.trials import collect_trials, pooled_race_counts
from genegap.variants import load_rules, variants_for_gene


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Offline analysis attempted a network request")
    monkeypatch.setattr(requests.Session, "request", fail)


@pytest.fixture
def store():
    value = Store(":memory:")
    yield value
    value.db.close()


def execute(store, **changes):
    plan = {"action": "assess", "drug": "clopidogrel", "genes": ["CYP2C19"],
            "population_id": "us_acs_antiplatelet"}
    plan.update(changes)
    events = []
    draft = analysis.execute_plan(plan, store=store, run_id="recorded-run",
                                  on_event=lambda kind, payload: events.append((kind, payload)), offline=True)
    return draft, events


def computed_facts(store):
    return [Fact.model_validate_json(row["json"]) for row in store.db.execute(
        "SELECT json FROM facts WHERE source='GeneGap.stats' ORDER BY record_id"
    )]


def test_recorded_offline_run_returns_only_provenance_backed_draft_hypotheses(store):
    draft, events = execute(store)
    assert draft["run_id"] == "recorded-run"
    assert set(draft) == {"run_id", "title", "flags", "limitations"}
    assert len(draft["flags"]) == 4
    for flag in draft["flags"]:
        assert flag["variant_id"] == DEMO_VARIANT_ID
        assert flag["status"] == "draft"
        assert flag["evidence_tier"] is None
        assert flag["robust_across_mappings"] is True
        assert "suggests lower expected-carrier representation" in flag["claim"]
        assert flag["citations"] == []
        facts = [store.get_fact(fid) for fid in flag["fact_ids"]]
        assert all(facts)
        assert {"GeneGap.stats", "GeneGap.population", "GeneGap.mapping", "GeneGap.trials",
                "gnomAD", "ClinicalTrials.gov"} <= {fact.source for fact in facts}
    tables = computed_facts(store)
    assert len(tables) == 3
    assert {f.record_id.rsplit(":", 1)[-1] for f in tables} == {"A", "B", "C"}
    for fact in tables:
        assert fact.method == "computed" and fact.field == "gap_result"
        assert set(fact.value) == {"result", "notes", "target_label", "target_carriers"}
        assert fact.value["target_carriers"] == 30
        assert all(store.get_fact(fid) for fid in fact.value["result"]["fact_ids"])
    notes = " ".join(draft["limitations"])
    assert "PLACEHOLDER" in notes and "ASSUMPTION" in notes
    assert "independent critic review are pending" in notes
    assert "offline mode and no cached response" in notes
    assert events[-1][1]["draft_flags"] == 4


def test_persisted_math_matches_existing_core_and_gene_specific_minimum(store):
    execute(store)
    gnomad, clinvar, ctgov, _ = offline_clients(store)
    rule = load_rules()["CYP2C19"]
    table = variants_for_gene("CYP2C19", gnomad, clinvar)
    trials, _ = collect_trials(ctgov, intervention="clopidogrel", max_trials=5)
    counts, _, _ = pooled_race_counts(trials)
    target = load_target_population("us_acs_antiplatelet")
    mappings = load_all_mappings()
    for fact in computed_facts(store):
        result = fact.value["result"]
        expected = compute_gap(table.variants[0], map_counts(counts, mappings[result["mapping_version"]]),
                               target, inheritance=rule.inheritance, min_an=rule.min_an)
        assert result["groups"] == expected.result.model_dump(mode="json")["groups"]


@pytest.mark.parametrize("gene, expected", [
    ("G6PD", "X-linked"), ("HLA-B", "HLA"), ("UNKNOWN", "No curated variant rule"),
])
def test_unsupported_genes_leave_empty_flags_and_a_replan(store, gene, expected):
    draft, events = execute(store, genes=[gene], drug="", population_id=None)
    assert draft["flags"] == []
    assert expected in " ".join(draft["limitations"])
    assert any(kind == "replan" for kind, _ in events)
    assert not computed_facts(store)


@pytest.mark.parametrize("population", [None, "us_acs_antiplatelet"])
def test_warfarin_never_reuses_clopidogrel_denominator(store, population):
    draft, events = execute(store, drug="warfarin", genes=["VKORC1"], population_id=population)
    assert draft["flags"] == []
    assert "denominator" in " ".join(draft["limitations"])
    assert events[-1][0] == "replan"
    assert not computed_facts(store)


def test_missing_mapping_cannot_be_called_abc_robust(store, monkeypatch):
    mappings = load_all_mappings()
    monkeypatch.setattr(analysis, "load_all_mappings", lambda: {k: v for k, v in mappings.items() if k != "C"})
    draft, events = execute(store)
    assert draft["flags"] == []
    assert len(computed_facts(store)) == 2
    assert any(kind == "replan" and "Missing ancestry mappings C" in payload["reason"]
               for kind, payload in events)


def test_missing_baseline_replans_to_eligibility_only(store, monkeypatch):
    monkeypatch.setattr(analysis, "collect_trials", lambda *args, **kwargs: ([], ["No race breakdown available."]))
    draft, events = execute(store)
    assert draft["flags"] == []
    assert any(kind == "replan" and payload["mode"] == "eligibility-only" for kind, payload in events)
    assert not computed_facts(store)


@pytest.mark.parametrize("action", ["clarify", "refuse"])
def test_non_assessment_plans_do_not_compute(store, action):
    draft, events = execute(store, action=action)
    assert draft["flags"] == []
    assert events[0][0] == "replan"
    assert not computed_facts(store)
