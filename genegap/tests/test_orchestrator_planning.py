"""Planning stays within curated entities and never needs an API for tests."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from genegap.agents import planning


def test_catalog_deduplicates_variant_rows():
    catalog = planning.load_catalog()
    assert catalog.count({"drug": "clopidogrel", "gene": "CYP2C19"}) == 1
    assert {row["drug"] for row in catalog} == {"clopidogrel", "warfarin", "azathioprine", "simvastatin"}


@pytest.mark.parametrize("query,drug,genes,population", [
    ("Assess PLAVIX trials in the UK", "clopidogrel", ["CYP2C19"], "uk_acs_antiplatelet"),
    ("cyp2c19 research gaps", "clopidogrel", ["CYP2C19"], "us_acs_antiplatelet"),
    ("Assess VKORC1 representation", "warfarin", ["VKORC1"], None),
    ("warfarin UK trial representation", "warfarin", ["CYP2C9", "VKORC1"], None),
    ("G6PD research gaps", "", ["G6PD"], None),
    ("HLA-B representation", "", ["HLA-B"], None),
    ("my research project on clopidogrel dosing trials", "clopidogrel", ["CYP2C19"], "us_acs_antiplatelet"),
])
def test_explicit_offline_entities(query, drug, genes, population):
    result = planning.offline_plan(query)
    assert result.action == "assess"
    assert (result.drug, result.genes, result.population_id) == (drug, genes, population)


@pytest.mark.parametrize("query", [
    "clopidogrel and warfarin", "not clopidogrel", "exclude CYP2C19", "warfarin CYP2C19",
    "clopidogrel UK and US", "clopidogrellike", "CYP2C19A", "unknown treatment", "",
])
def test_unknown_ambiguous_excluded_and_mismatched_entities_clarify(query):
    assert planning.offline_plan(query) == planning.AnalysisPlan(action="clarify")


@pytest.mark.parametrize("query", [
    "What clopidogrel dose should I take with my CYP2C19 genotype?",
    "Interpret my CYP2C19 genotype", "Give a patient a warfarin dose based on VKORC1",
])
def test_individual_treatment_requests_refused(query):
    assert planning.offline_plan(query) == planning.AnalysisPlan(action="refuse")


def client_for(*plans):
    client = Mock()
    client.models.generate_content.side_effect = [SimpleNamespace(parsed=plan) for plan in plans]
    return client


def test_live_uses_configured_structured_schema_with_injected_client():
    valid = {"action": "assess", "drug": "clopidogrel", "genes": ["CYP2C19"],
             "population_id": "uk_acs_antiplatelet"}
    client = client_for(valid)
    assert planning.gemini_plan("Assess Plavix in the UK", client=client).drug == "clopidogrel"
    call = client.models.generate_content.call_args.kwargs
    assert call["model"] == planning.config.GEMINI_MODEL
    assert call["config"]["response_schema"] is planning.AnalysisPlan
    assert call["config"]["response_mime_type"] == "application/json"
    assert call["config"]["temperature"] == 0
    client.close.assert_not_called()


def test_invalid_pair_retries_once_and_can_recover():
    client = client_for(
        {"action": "assess", "drug": "warfarin", "genes": ["CYP2C19"]},
        {"action": "assess", "drug": "warfarin", "genes": ["VKORC1"]},
    )
    assert planning.gemini_plan("Assess warfarin VKORC1", client=client).genes == ["VKORC1"]
    assert client.models.generate_content.call_count == 2


@pytest.mark.parametrize("invalid", [
    {"action": "assess", "drug": "warfarin", "genes": ["VKORC1"], "population_id": "us_acs_antiplatelet"},
    {"action": "assess", "drug": "warfarin", "genes": ["MADEUP"]},
    {"action": "assess", "drug": "warfarin", "genes": ["VKORC1"], "enrollment": 999},
    {"action": "clarify", "drug": "warfarin"},
])
def test_invalid_outputs_stop_after_two_responses(invalid):
    client = client_for(invalid, invalid)
    with pytest.raises(planning.PlanningError, match="twice"):
        planning.gemini_plan("warfarin VKORC1", client=client)
    assert client.models.generate_content.call_count == 2


def test_live_cannot_assess_unrelated_entities_or_ignore_exclusions():
    invalid = {"action": "assess", "drug": "clopidogrel", "genes": ["CYP2C19"],
               "population_id": "us_acs_antiplatelet"}
    with pytest.raises(planning.PlanningError, match="twice"):
        planning.gemini_plan("not clopidogrel", client=client_for(invalid, invalid))


def test_missing_key_is_actionable_without_constructing_client(monkeypatch):
    monkeypatch.setattr(planning.config, "GEMINI_API_KEY", "")
    with pytest.raises(planning.PlanningError, match="GEMINI_API_KEY"):
        planning.gemini_plan("clopidogrel")


def test_owned_client_closed_even_after_live_failure(monkeypatch):
    from google import genai
    client = Mock()
    client.models.generate_content.side_effect = RuntimeError("service unavailable")
    factory = Mock(return_value=client)
    monkeypatch.setattr(planning.config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(genai, "Client", factory)
    with pytest.raises(planning.PlanningError, match="service access"):
        planning.gemini_plan("clopidogrel")
    assert client.models.generate_content.call_count == 1
    client.close.assert_called_once()


def test_json_text_response_supported():
    client = Mock()
    client.models.generate_content.return_value = SimpleNamespace(
        parsed=None, text='{"action":"assess","drug":"warfarin","genes":["VKORC1"]}')
    assert planning.gemini_plan("VKORC1", client=client).drug == "warfarin"
