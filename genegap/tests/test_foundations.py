"""Unit tests for Phase 1 foundations: schemas, persistence, HTTP caching, and API parsers."""
import json

import pytest
from pydantic import ValidationError

from genegap.clients.base import ApiClient, ApiError, CacheMiss
from genegap.clients.ctgov import CTGovClient
from genegap.clients.gnomad import GnomadClient, merge_populations
from genegap.schemas import AncestryFrequency, BaselineDemographics, Fact
from genegap.store import Store


# ---- Mock fixtures for hermetic, network-free testing ----

class FakeResp:
    """Mock HTTP response object that mimics requests.Response."""
    def __init__(self, data, status=200, url="http://x"):
        self._d, self.status_code, self.url, self.text = data, status, url, json.dumps(data)

    def json(self):
        return self._d


class FakeSession:
    """Mock requests.Session that returns a sequence of predetermined responses."""
    def __init__(self, responses):
        self.headers, self.responses, self.calls = {}, list(responses), 0

    def request(self, method, url, **kw):
        self.calls += 1
        return self.responses.pop(0)


def client(cls, responses, **kw):
    """Helper to instantiate an ApiClient subclass with an in-memory SQLite store and instant sleep."""
    return cls(store=Store(":memory:"), session=FakeSession(responses), sleep=lambda s: None, **kw)


# ---- Schemas & biological invariants ----

def test_fact_id_is_deterministic_and_value_sensitive():
    # Identical facts must yield the exact same hash ID (idempotency)
    a = Fact(field="f", value=1, source="s", record_id="r")
    b = Fact(field="f", value=1, source="s", record_id="r")
    # A different value must produce a different hash ID
    c = Fact(field="f", value=2, source="s", record_id="r")
    assert a.id == b.id != c.id


def test_ac_cannot_exceed_an():
    # Biological invariant: observed mutant alleles (AC) cannot exceed total chromosomes examined (AN)
    with pytest.raises(ValidationError):
        AncestryFrequency(group="afr", ac=5, an=3, fact_id="x")


def test_af_and_missing_fraction():
    # Allele frequency should be AC / AN (1 / 4 = 0.25)
    assert AncestryFrequency(group="afr", ac=1, an=4, fact_id="x").af == 0.25
    # Allele frequency should be None when AN is zero (avoids division by zero)
    assert AncestryFrequency(group="afr", ac=0, an=0, fact_id="x").af is None
    # Missing demographic fraction: 20 unknown out of 100 total participants = 0.20 (20%)
    b = BaselineDemographics(nct_id="N", scheme="race", counts={"White": 80, "Unknown or Not Reported": 20})
    assert b.missing_fraction == 0.2


# ---- Store (persistence & logging) ----

def test_store_round_trip_and_steps():
    store = Store(":memory:")
    fact = Fact(field="f", value={"ac": 1}, source="s", record_id="r")
    store.put_fact(fact)

    # Verify fact can be retrieved by its ID
    assert store.get_fact(fact.id).value == {"ac": 1}
    # Verify fact can be queried by source and record ID
    assert [x.id for x in store.facts_for_record("s", "r")] == [fact.id]

    # Verify agent execution steps are logged in order
    store.log_step("run1", "orchestrator", "plan", {"a": 1})
    assert store.steps("run1")[0]["payload"] == {"a": 1}


# ---- Base client (caching, retry, offline mode) ----

def test_cache_hit_avoids_second_call():
    # The first call fetches and caches; the second call should return from cache without hitting the network
    c = client(ApiClient, [FakeResp({"ok": 1})])
    assert c.request("GET", "http://x", params={"a": 1})[0] == {"ok": 1}
    assert c.request("GET", "http://x", params={"a": 1})[0] == {"ok": 1}
    assert c.session.calls == 1  # Exactly one network request made


def test_retries_then_succeeds():
    # Transient 503 (service unavailable) and 429 (rate limited) should trigger retries until 200 OK
    c = client(ApiClient, [FakeResp({}, 503), FakeResp({}, 429), FakeResp({"ok": 2})])
    assert c.request("GET", "http://x")[0] == {"ok": 2}
    assert c.session.calls == 3


def test_client_error_is_not_retried_or_cached():
    # Client errors (404) should fail immediately without retrying or saving to cache
    c = client(ApiClient, [FakeResp({}, 404)])
    with pytest.raises(ApiError):
        c.request("GET", "http://x")
    assert c.session.calls == 1


def test_offline_mode_raises_on_miss():
    # In offline mode, querying an uncached endpoint must raise CacheMiss
    c = client(ApiClient, [], offline=True)
    with pytest.raises(CacheMiss):
        c.request("GET", "http://x")


# ---- gnomAD population parsing ----

GNOMAD = {
    "variant_id": "10-1-A-G",
    "rsids": ["rs1"],
    "exome": {"populations": [{"id": "afr", "ac": 10, "an": 100}, {"id": "afr_XX", "ac": 4, "an": 50}]},
    "genome": {"populations": [{"id": "afr", "ac": 5, "an": 200}, {"id": "nfe", "ac": 1, "an": 1000}]},
}


def test_merge_populations_sums_cohorts_and_skips_sex_split():
    # 'afr' should be summed across exome (10/100) and genome (5/200) -> 15/300
    # 'afr_XX' contains an underscore, indicating a sex-split subset, so it should be skipped
    assert merge_populations(GNOMAD) == {"afr": [15, 300], "nfe": [1, 1000]}


def test_gnomad_variant_builds_schema_and_facts():
    # Parsing gnomAD response should populate the Variant model and store provenance Facts
    c = client(GnomadClient, [FakeResp({"data": {"variant": GNOMAD}})])
    v = c.variant("10-1-A-G", gene="CYP2C19")
    assert v.rsid == "rs1" and {f.group for f in v.frequencies} == {"afr", "nfe"}
    fact = c.store.get_fact(v.frequencies[0].fact_id)
    assert fact.source == "gnomAD" and fact.value["dataset"] == "gnomad_r4"


# ---- ClinicalTrials.gov parsing ----

CT = {
    "studies": [
        {
            "hasResults": True,
            "protocolSection": {
                "identificationModule": {"nctId": "NCT1", "briefTitle": "T"},
                "statusModule": {"overallStatus": "COMPLETED"},
                "designModule": {"phases": ["PHASE3"]},
                "conditionsModule": {"conditions": ["MI"]},
                "armsInterventionsModule": {"interventions": [{"name": "Clopidogrel"}]},
            },
        }
    ]
}


def test_ctgov_search_parses_trials():
    # Parsing ClinicalTrials.gov response should extract trial identifier, results flag, and intervention
    t = client(CTGovClient, [FakeResp(CT)]).search(intervention="clopidogrel")[0]
    assert (t.nct_id, t.has_results, t.interventions) == ("NCT1", True, ["Clopidogrel"])
