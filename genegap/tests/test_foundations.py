import json

import pytest
from pydantic import ValidationError

from genegap.clients.base import ApiClient, ApiError, CacheMiss
from genegap.clients.ctgov import CTGovClient
from genegap.clients.gnomad import GnomadClient, merge_populations
from genegap.schemas import AncestryFrequency, BaselineDemographics, Fact
from genegap.store import Store


class FakeResp:
    def __init__(self, data, status=200, url="http://x"):
        self._d, self.status_code, self.url, self.text = data, status, url, json.dumps(data)

    def json(self):
        return self._d


class FakeSession:
    def __init__(self, responses):
        self.headers, self.responses, self.calls = {}, list(responses), 0

    def request(self, method, url, **kw):
        self.calls += 1
        return self.responses.pop(0)


def client(cls, responses, **kw):
    return cls(store=Store(":memory:"), session=FakeSession(responses), sleep=lambda s: None, **kw)


# ---- schemas ----
def test_fact_id_is_deterministic_and_value_sensitive():
    a = Fact(field="f", value=1, source="s", record_id="r")
    b = Fact(field="f", value=1, source="s", record_id="r")
    c = Fact(field="f", value=2, source="s", record_id="r")
    assert a.id == b.id != c.id


def test_ac_cannot_exceed_an():
    with pytest.raises(ValidationError):
        AncestryFrequency(group="afr", ac=5, an=3, fact_id="x")


def test_af_and_missing_fraction():
    assert AncestryFrequency(group="afr", ac=1, an=4, fact_id="x").af == 0.25
    assert AncestryFrequency(group="afr", ac=0, an=0, fact_id="x").af is None
    b = BaselineDemographics(nct_id="N", scheme="race", counts={"White": 80, "Unknown or Not Reported": 20})
    assert b.missing_fraction == 0.2


# ---- store ----
def test_store_round_trip_and_steps():
    s = Store(":memory:")
    f = Fact(field="f", value={"ac": 1}, source="s", record_id="r")
    s.put_fact(f)
    assert s.get_fact(f.id).value == {"ac": 1}
    assert [x.id for x in s.facts_for_record("s", "r")] == [f.id]
    s.log_step("run1", "orchestrator", "plan", {"a": 1})
    assert s.steps("run1")[0]["payload"] == {"a": 1}


# ---- base client ----
def test_cache_hit_avoids_second_call():
    c = client(ApiClient, [FakeResp({"ok": 1})])
    assert c.request("GET", "http://x", params={"a": 1})[0] == {"ok": 1}
    assert c.request("GET", "http://x", params={"a": 1})[0] == {"ok": 1}
    assert c.session.calls == 1


def test_retries_then_succeeds():
    c = client(ApiClient, [FakeResp({}, 503), FakeResp({}, 429), FakeResp({"ok": 2})])
    assert c.request("GET", "http://x")[0] == {"ok": 2}
    assert c.session.calls == 3


def test_client_error_is_not_retried_or_cached():
    c = client(ApiClient, [FakeResp({}, 404)])
    with pytest.raises(ApiError):
        c.request("GET", "http://x")
    assert c.session.calls == 1


def test_offline_mode_raises_on_miss():
    c = client(ApiClient, [], offline=True)
    with pytest.raises(CacheMiss):
        c.request("GET", "http://x")


# ---- gnomAD parsing ----
GNOMAD = {"variant_id": "10-1-A-G", "rsids": ["rs1"],
          "exome": {"populations": [{"id": "afr", "ac": 10, "an": 100}, {"id": "afr_XX", "ac": 4, "an": 50}]},
          "genome": {"populations": [{"id": "afr", "ac": 5, "an": 200}, {"id": "nfe", "ac": 1, "an": 1000}]}}


def test_merge_populations_sums_cohorts_and_skips_sex_split():
    assert merge_populations(GNOMAD) == {"afr": [15, 300], "nfe": [1, 1000]}


def test_gnomad_variant_builds_schema_and_facts():
    c = client(GnomadClient, [FakeResp({"data": {"variant": GNOMAD}})])
    v = c.variant("10-1-A-G", gene="CYP2C19")
    assert v.rsid == "rs1" and {f.group for f in v.frequencies} == {"afr", "nfe"}
    fact = c.store.get_fact(v.frequencies[0].fact_id)
    assert fact.source == "gnomAD" and fact.value["dataset"] == "gnomad_r4"


# ---- ClinicalTrials.gov parsing ----
CT = {"studies": [{"hasResults": True, "protocolSection": {
    "identificationModule": {"nctId": "NCT1", "briefTitle": "T"}, "statusModule": {"overallStatus": "COMPLETED"},
    "designModule": {"phases": ["PHASE3"]}, "conditionsModule": {"conditions": ["MI"]},
    "armsInterventionsModule": {"interventions": [{"name": "Clopidogrel"}]}}}]}


def test_ctgov_search_parses_trials():
    t = client(CTGovClient, [FakeResp(CT)]).search(intervention="clopidogrel")[0]
    assert (t.nct_id, t.has_results, t.interventions) == ("NCT1", True, ["Clopidogrel"])
