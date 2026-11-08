"""Tests for tasks 2.2 and 2.3: trial retrieval and the baseline demographics parser.

The awkward cases are the point here: percentages instead of counts, multi-arm tables with
no Total column, trials with no results posted, and trials with no race breakdown at all.
"""
import json
from pathlib import Path

import pytest

from genegap.clients.base import ApiError
from genegap.trials import (
    BaselineError,
    classify_measure,
    choose_group_ids,
    collect_trials,
    is_missing_label,
    parse_baseline,
    pooled_race_counts,
)
from genegap.schemas import BaselineDemographics, Trial

FIXTURE = Path(__file__).resolve().parents[1] / "smoke" / "responses" / "ctgov_study_raw_NCT01609842.json"


def real_study():
    return json.loads(FIXTURE.read_text())


def measure(title, categories, param="COUNT_OF_PARTICIPANTS", unit="Participants", groups=("BG000",)):
    """Build one baseline measure: categories is {label: {groupId: value}}."""
    return {
        "title": title,
        "paramType": param,
        "unitOfMeasure": unit,
        "classes": [{
            "categories": [
                {"title": label, "measurements": [{"groupId": g, "value": str(v)} for g, v in per_group.items()]}
                for label, per_group in categories.items()
            ]
        }],
    }


def study(measures, groups, denoms=None, nct="NCT00000001", has_results=True):
    return {
        "protocolSection": {"identificationModule": {"nctId": nct}},
        "hasResults": has_results,
        "resultsSection": {"baselineCharacteristicsModule": {
            "groups": groups,
            "denoms": denoms or [],
            "measures": measures,
        }},
    }


# ---- measure classification ----
@pytest.mark.parametrize("title,expected", [
    ("Race (NIH/OMB)", "race"),
    ("Race/Ethnicity, Customized", "race"),          # combined measure counts as race
    ("Ethnicity (NIH/OMB)", "ethnicity"),
    ("Sex: Female, Male", "sex"),
    ("Age, Continuous", "age"),
    ("Body Mass Index", None),
])
def test_classify_measure(title, expected):
    assert classify_measure(title) == expected


@pytest.mark.parametrize("label,expected", [
    ("Unknown or Not Reported", True),
    ("Not reported", True),
    ("White", False),
    ("Asian", False),
])
def test_is_missing_label(label, expected):
    assert is_missing_label(label) is expected


# ---- the real recorded trial ----
def test_real_trial_race_counts_match_the_registry_total_column():
    parsed = parse_baseline(real_study())
    assert parsed.nct_id == "NCT01609842"
    assert parsed.group_basis == "total_row"
    assert parsed.total_participants == 14573

    race = parsed.by_scheme("race")
    assert race is not None
    assert race.counts["White"] == 11935
    assert race.counts["Black or African American"] == 2329
    assert race.counts["Asian"] == 40
    # The Total column is used, so the categories add up to the stated enrolment, not 5x it.
    assert sum(race.counts.values()) == 14573

    sex = parsed.by_scheme("sex")
    assert sex.counts == {"Female": 287, "Male": 14286}
    # Age is a mean, not a headcount, so it is never turned into counts.
    assert parsed.by_scheme("age") is None


def test_real_trial_records_a_missing_data_rate():
    parsed = parse_baseline(real_study())
    assert parsed.missing_fraction == 0.0      # this trial reported 0 unknown


# ---- the awkward cases ----
def test_total_column_is_preferred_over_summing_arms():
    groups = [{"id": "BG000", "title": "Arm A"}, {"id": "BG001", "title": "Arm B"}, {"id": "BG002", "title": "Total"}]
    ids, basis, notes = choose_group_ids(groups, {})
    assert ids == ["BG002"] and basis == "total_row" and notes == []


def test_multi_arm_without_total_sums_arms_and_says_so():
    groups = [{"id": "BG000", "title": "Arm A"}, {"id": "BG001", "title": "Arm B"}]
    ids, basis, notes = choose_group_ids(groups, {})
    assert set(ids) == {"BG000", "BG001"} and basis == "summed_arms"
    assert any("only correct if the arms do not" in n for n in notes)

    s = study(
        [measure("Race (NIH/OMB)", {"White": {"BG000": 30, "BG001": 20}, "Asian": {"BG000": 5, "BG001": 5}})],
        groups,
    )
    parsed = parse_baseline(s)
    assert parsed.by_scheme("race").counts == {"White": 50, "Asian": 10}
    assert parsed.group_basis == "summed_arms"


def test_percentages_are_converted_using_the_denominator_and_flagged():
    groups = [{"id": "BG000", "title": "Total"}]
    denoms = [{"units": "Participants", "counts": [{"groupId": "BG000", "value": "200"}]}]
    s = study(
        [measure("Race (NIH/OMB)", {"White": {"BG000": 75}, "Asian": {"BG000": 25}},
                 param="NUMBER", unit="percentage of participants")],
        groups, denoms,
    )
    parsed = parse_baseline(s)
    assert parsed.by_scheme("race").counts == {"White": 150, "Asian": 50}
    assert any("percentages" in n and "approximate" in n for n in parsed.notes)


def test_percentages_without_a_denominator_produce_no_counts():
    groups = [{"id": "BG000", "title": "Total"}]
    s = study(
        [measure("Race (NIH/OMB)", {"White": {"BG000": 75}}, param="NUMBER", unit="percentage of participants")],
        groups,
    )
    parsed = parse_baseline(s)
    assert parsed.by_scheme("race") is None
    assert any("no participant denominator" in n for n in parsed.notes)


def test_trial_with_no_results_section_is_a_note_not_a_crash():
    parsed = parse_baseline({
        "protocolSection": {"identificationModule": {"nctId": "NCT00000002"}},
        "hasResults": False,
    })
    assert parsed.demographics == []
    assert parsed.has_results is False
    assert any("No baseline characteristics table" in n for n in parsed.notes)


def test_trial_with_results_but_no_race_measure_is_flagged():
    groups = [{"id": "BG000", "title": "Total"}]
    s = study([measure("Sex: Female, Male", {"Female": {"BG000": 10}, "Male": {"BG000": 15}})], groups)
    parsed = parse_baseline(s)
    assert not parsed.has_race_data
    assert any("No race breakdown" in n for n in parsed.notes)


def test_unknown_category_feeds_the_missing_data_rate():
    groups = [{"id": "BG000", "title": "Total"}]
    s = study([measure("Race (NIH/OMB)", {
        "White": {"BG000": 70}, "Asian": {"BG000": 10}, "Unknown or Not Reported": {"BG000": 20},
    })], groups)
    parsed = parse_baseline(s)
    assert parsed.missing_fraction == pytest.approx(0.2)
    assert any("20.0% of participants" in n for n in parsed.notes)


def test_unparseable_values_are_skipped_not_guessed():
    groups = [{"id": "BG000", "title": "Total"}]
    s = study([measure("Race (NIH/OMB)", {"White": {"BG000": "NA"}, "Asian": {"BG000": 12}})], groups)
    parsed = parse_baseline(s)
    assert parsed.by_scheme("race").counts == {"Asian": 12}


def test_study_without_an_nct_id_is_rejected():
    with pytest.raises(BaselineError, match="NCT ID"):
        parse_baseline({"protocolSection": {}})


def test_second_race_measure_is_ignored_with_a_note():
    groups = [{"id": "BG000", "title": "Total"}]
    s = study([
        measure("Race (NIH/OMB)", {"White": {"BG000": 10}}),
        measure("Race, Customized", {"Other": {"BG000": 5}}),
    ], groups)
    parsed = parse_baseline(s)
    assert parsed.by_scheme("race").counts == {"White": 10}
    assert any("More than one 'race' measure" in n for n in parsed.notes)


# ---- pooling across trials ----
def test_pooled_race_counts_sums_trials_and_states_the_assumption():
    def trial(nct, counts):
        return Trial(nct_id=nct, baseline=[BaselineDemographics(nct_id=nct, scheme="race", counts=counts)])

    counts, used, notes = pooled_race_counts([
        trial("NCT1", {"White": 100, "Asian": 10}),
        trial("NCT2", {"White": 50, "Black or African American": 20}),
        Trial(nct_id="NCT3"),      # no baseline at all
    ])
    assert counts == {"White": 150, "Asian": 10, "Black or African American": 20}
    assert used == ["NCT1", "NCT2"]
    assert any("no participant took part in more than one" in n for n in notes)


# ---- retrieval (2.2) with a stub client ----
class StubCtgov:
    def __init__(self, trials, raw=None, fail=()):
        self._trials, self._raw, self._fail = trials, raw or {}, set(fail)

    def search(self, intervention=None, condition=None, has_results=True):
        return list(self._trials)

    def study_raw(self, nct_id):
        if nct_id in self._fail:
            raise ApiError(f"boom for {nct_id}")
        return self._raw[nct_id]

    def fact(self, **kw):
        class F:
            id = "F-stub"
        return F()


def test_collect_trials_skips_unfetchable_trials_and_keeps_going():
    groups = [{"id": "BG000", "title": "Total"}]
    raw = {"NCT1": study([measure("Race (NIH/OMB)", {"White": {"BG000": 9}})], groups, nct="NCT1")}
    ctgov = StubCtgov([Trial(nct_id="NCT1"), Trial(nct_id="NCT2")], raw=raw, fail=["NCT2"])

    trials, notes = collect_trials(ctgov, intervention="clopidogrel")
    assert [t.nct_id for t in trials] == ["NCT1"]
    assert any("NCT2: could not fetch" in n for n in notes)
    assert any("1 of 1 trials report a race breakdown" in n for n in notes)


def test_collect_trials_reports_an_empty_search_rather_than_failing():
    trials, notes = collect_trials(StubCtgov([]), intervention="nosuchdrug")
    assert trials == []
    assert any("No completed trials" in n for n in notes)


def test_collect_trials_survives_a_search_failure():
    class Boom(StubCtgov):
        def search(self, **kw):
            raise ApiError("ctgov down")

    trials, notes = collect_trials(Boom([]), intervention="clopidogrel")
    assert trials == [] and any("Trial search failed" in n for n in notes)
