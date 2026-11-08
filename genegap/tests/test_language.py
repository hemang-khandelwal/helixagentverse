"""Task 4.2: the language and claim-strength checker.

Fully offline and deterministic. The GapResult under test is the real M1
clopidogrel / CYP2C19 result replayed from smoke/responses/, so the numbers the
prose quotes are genuine and the tier-sensitivity test is meaningful.
"""
from __future__ import annotations

import pytest

from genegap.fixtures import offline_clients
from genegap.gap import compute_gap
from genegap.language import (
    ADJUDICATING_RULES,
    ANNOTATING_RULES,
    check_language,
    permissible_decimals,
    split_sentences,
)
from genegap.mapping import load_all_mappings, map_counts
from genegap.population import load_target_population
from genegap.store import Store
from genegap.trials import collect_trials, pooled_race_counts
from genegap.variants import variants_for_gene


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    """The real Tier A gap table for CYP2C19 rs4244285 under mapping A."""
    tmp = tmp_path_factory.mktemp("language")
    gnomad, clinvar, ctgov, _ = offline_clients(Store(str(tmp / "lang.sqlite")))
    table = variants_for_gene("CYP2C19", gnomad, clinvar)
    trials, _ = collect_trials(ctgov, intervention="clopidogrel", max_trials=5)
    counts, _, _ = pooled_race_counts(trials)
    target = load_target_population("us_acs_antiplatelet")
    variant = next(v for v in table.variants if v.rsid == "rs4244285")
    return compute_gap(
        variant, map_counts(counts, load_all_mappings()["A"]), target,
        inheritance=table.inheritance,
    ).result


# The numbers below are the real ones: eas carrier_freq 0.51502..., an 36782,
# n_trial 24, expected carriers 12.36, required_n 59; ami is insufficient_data (an 910).
COMPLIANT = """\
Coverage gap for CYP2C19 rs4244285 in clopidogrel trials (gnomAD v4, ClinicalTrials.gov NCT01609842).

In the pooled trial population, 24 participants mapped to the East Asian ancestry group, \
against 11935 mapped to the non-Finnish European group (ClinicalTrials.gov NCT01609842).
The carrier frequency of this variant in the East Asian group is estimated at 51.50% \
(95% credible interval 50.85% to 52.17%), derived from gnomAD allele counts.
Expected carriers in the East Asian group total 12.4, and the carrier ratio for that group \
is 0.03 relative to its share of the target population (gnomAD, source: table 1).
Reaching 30 expected carriers in the East Asian group would require about 59 participants \
from that group (gnomAD).
The Amish group is reported as insufficient data, because gnomAD records only 910 alleles \
for it, below the threshold this analysis requires (gnomAD).
This finding is a hypothesis for expert review and should be read alongside the mapping \
assumptions recorded with the run.
"""


def _rule(text, result, tier, rule_id):
    return check_language(text, result, tier)[rule_id]


def _quoted_correctly(text, finding):
    """The finding must quote the real sentence and point at its real offset."""
    return text[finding.offset:finding.offset + len(finding.sentence)] == finding.sentence


# --------------------------------------------------------------- the compliant report


def test_a_compliant_tier_a_report_passes_every_rule(result):
    report = check_language(COMPLIANT, result, "TIER A")
    assert report.status == "PASS", [
        (r.rule_id, r.status, r.reason) for r in report.rules.values() if r.status != "PASS"
    ]
    assert report.passed is True
    assert report.failed_rules == []
    for rule_id in ADJUDICATING_RULES + ANNOTATING_RULES:
        assert report[rule_id].status == "PASS", f"{rule_id}: {report[rule_id].reason}"
    assert report.findings == []


def test_the_report_shape_mirrors_the_critic(result):
    report = check_language(COMPLIANT, result, "TIER A")
    assert set(report.rules) == set(ADJUDICATING_RULES) | set(ANNOTATING_RULES)
    assert report.l1.adjudicating is True and report.l6.adjudicating is True
    assert report.l7.adjudicating is False and report.l8.adjudicating is False
    assert report.tier == "TIER A"
    assert report["L1"] is report.l1


@pytest.mark.parametrize("tier,expected", [
    ("TIER A", "TIER A"), ("A", "TIER A"), ("tier_b", "TIER B"), ("REJECTED", "REJECTED"),
])
def test_tier_strings_are_normalised(result, tier, expected):
    assert check_language("No numbers here.", result, tier).tier == expected


def test_an_unknown_tier_is_refused(result):
    with pytest.raises(ValueError, match="Unknown tier"):
        check_language("No numbers here.", result, "probably fine")


# ------------------------------------------------- one deliberate violation per rule


def test_l1_rejects_an_enumerated_clinical_directive(result):
    text = ("The East Asian group is thinly enrolled (gnomAD).\n"
            "Patients in this group should be prescribed an alternative antiplatelet.")
    rule = _rule(text, result, "TIER A", "L1")
    assert rule.status == "FAIL"
    assert rule.findings[0].sentence == (
        "Patients in this group should be prescribed an alternative antiplatelet."
    )
    assert _quoted_correctly(text, rule.findings[0])
    assert "should be prescribed" in rule.findings[0].match.lower()
    assert check_language(text, result, "TIER A").passed is False


def test_l1_rejects_a_directive_it_was_never_given_as_a_phrase(result):
    """The grammatical pattern must catch a phrasing absent from the list."""
    text = "Clinicians ought to titrate the dose for this ancestry group."
    rule = _rule(text, result, "TIER A", "L1")
    assert rule.status == "FAIL"
    assert rule.findings[0].sentence == text.strip()
    assert "titrate" in rule.findings[0].match.lower()


def test_l2_rejects_a_claim_about_an_individual(result):
    text = ("Enrolment is thin in the East Asian group (gnomAD).\n"
            "A patient with this variant will respond poorly to the drug.")
    rule = _rule(text, result, "TIER A", "L2")
    assert rule.status == "FAIL"
    assert rule.findings[0].sentence == (
        "A patient with this variant will respond poorly to the drug."
    )
    assert _quoted_correctly(text, rule.findings[0])


def test_l2_rejects_a_bare_plural_outcome_claim(result):
    text = "Carriers experience reduced activation of the drug."
    rule = _rule(text, result, "TIER A", "L2")
    assert rule.status == "FAIL"
    assert "carriers experience" in rule.findings[0].match.lower()


def test_l3_rejects_causal_language(result):
    text = ("Frequencies differ between groups (gnomAD).\n"
            "The variant causes treatment failure in this population.")
    rule = _rule(text, result, "TIER A", "L3")
    assert rule.status == "FAIL"
    assert rule.findings[0].sentence == (
        "The variant causes treatment failure in this population."
    )
    assert _quoted_correctly(text, rule.findings[0])
    assert "causes" in rule.findings[0].match.lower()


def test_l4_rejects_an_unqualified_number_from_an_insufficient_data_group(result):
    """ami has only 910 alleles, so its numbers may not be stated plainly."""
    text = "The Amish group contributed 146 participants to the pooled analysis (gnomAD)."
    rule = _rule(text, result, "TIER A", "L4")
    assert rule.status == "FAIL"
    assert rule.findings[0].sentence == text.strip()
    assert "ami" in rule.findings[0].reason
    assert "insufficient_data" in rule.findings[0].reason


def test_l5_rejects_invented_precision(result):
    """eas has an=36782, which resolves four decimal places on a proportion."""
    text = ("The carrier frequency in the East Asian group is 51.5026% (gnomAD).")
    rule = _rule(text, result, "TIER A", "L5")
    assert rule.status == "FAIL"
    assert rule.findings[0].sentence == text.strip()
    assert _quoted_correctly(text, rule.findings[0])
    assert "36782" in rule.findings[0].reason


def test_l5_allows_the_precision_the_allele_number_does_support(result):
    text = "The carrier frequency in the East Asian group is 51.50% (gnomAD)."
    assert _rule(text, result, "TIER A", "L5").status == "PASS"


def test_permissible_decimals_is_derived_from_the_allele_number():
    assert permissible_decimals(36782) == 4       # 1/36782 resolves ~4 dp on a proportion
    assert permissible_decimals(910) == 2
    assert permissible_decimals(1111304) == 6
    assert permissible_decimals(0) is None
    assert permissible_decimals(None) is None


def test_l6_rejects_a_confidence_scalar(result):
    text = ("The East Asian group is under-enrolled (gnomAD).\n"
            "GeneGap is 87% confident in this finding.")
    rule = _rule(text, result, "TIER A", "L6")
    assert rule.status == "FAIL"
    assert rule.findings[0].sentence == "GeneGap is 87% confident in this finding."
    assert _quoted_correctly(text, rule.findings[0])


# ------------------------------------------- L4 is tier-sensitive, not a word filter


SAME_SENTENCE = "The carrier frequency in the East Asian group is 51.50% (gnomAD)."


def test_l4_the_same_sentence_passes_at_tier_a_and_fails_at_tier_b(result):
    """The decisive test: identical prose, identical numbers, different tier.

    eas is not flagged insufficient_data, so only the tier can drive the difference.
    """
    at_a = check_language(SAME_SENTENCE, result, "TIER A")
    at_b = check_language(SAME_SENTENCE, result, "TIER B")

    assert at_a.l4.status == "PASS", at_a.l4.reason
    assert at_b.l4.status == "FAIL", at_b.l4.reason

    finding = at_b.l4.findings[0]
    assert finding.sentence == SAME_SENTENCE
    assert "carrier_freq" in finding.reason and "eas" in finding.reason
    assert at_a.passed is True and at_b.passed is False


def test_l4_a_tier_b_number_passes_once_the_limitation_is_named(result):
    qualified = (
        "The carrier frequency in the East Asian group is 51.50% (gnomAD), "
        "but this is a provisional figure pending expert review."
    )
    assert check_language(qualified, result, "TIER B").l4.status == "PASS"


def test_l4_ignores_numbers_that_do_not_come_from_the_result(result):
    """A number the result never produced is not a claim about the result."""
    text = "The analysis read 5 trials and was run on 8 November 2026."
    assert check_language(text, result, "TIER B").l4.status == "PASS"


# -------------------------------------------------- annotating rules warn, not block


def test_l7_warns_on_totalising_words_without_blocking(result):
    text = "This confirms the enrolment shortfall, which was never addressed."
    report = check_language(text, result, "TIER A")
    assert report.l7.status == "WARN"
    assert report.l7.adjudicating is False
    assert report.status == "WARN"
    assert report.passed is True
    assert report.l7.findings[0].sentence == text.strip()


def test_l8_warns_on_an_uncited_number_without_blocking(result):
    text = ("The carrier frequency in the East Asian group is 51.50%.\n"
            "That shortfall is the finding of this analysis.")
    report = check_language(text, result, "TIER A")
    assert report.l8.status == "WARN"
    assert report.passed is True
    assert report.l8.findings[0].sentence.startswith("The carrier frequency")


def test_l8_is_satisfied_by_a_citation_in_the_following_sentence(result):
    text = ("The carrier frequency in the East Asian group is 51.50%.\n"
            "That figure comes from gnomAD v4.")
    assert check_language(text, result, "TIER A").l8.status == "PASS"


# --------------------------------------------------------- false-positive guards


def test_a_credible_interval_does_not_trip_the_confidence_rule(result):
    text = ("The carrier frequency in the East Asian group is 51.50% "
            "(95% credible interval 50.85% to 52.17%), from gnomAD.")
    report = check_language(text, result, "TIER A")
    assert report.l6.status == "PASS", report.l6.reason
    assert report.passed is True


def test_should_in_a_non_clinical_sense_does_not_trip_the_directive_rule(result):
    text = "This should be read alongside the mapping assumptions recorded with the run."
    report = check_language(text, result, "TIER A")
    assert report.l1.status == "PASS", report.l1.reason
    assert report.passed is True


def test_group_framing_and_quantities_do_not_trip_the_individual_rule(result):
    text = ("Expected carriers in the East Asian group total 12.4 (gnomAD).\n"
            "The carrier frequency for that group is the figure above.\n"
            "Participants mapped to that group numbered 24.")
    assert check_language(text, result, "TIER A").l2.status == "PASS"


def test_a_variant_mentioned_without_a_causal_claim_does_not_trip_l3(result):
    text = ("This variant is more common in the East Asian group than the non-Finnish "
            "European group (gnomAD).\n"
            "The difference in enrolment is due to the trials that reported race.")
    assert check_language(text, result, "TIER A").l3.status == "PASS"


# ------------------------------------------------------------------ sentence handling


def test_sentences_keep_their_offsets_in_the_source_text():
    text = "First sentence here. Second one follows.\nThird on a new line."
    sentences = split_sentences(text)
    assert [s.text for s in sentences] == [
        "First sentence here.", "Second one follows.", "Third on a new line.",
    ]
    for s in sentences:
        assert text[s.offset:s.offset + len(s.text)] == s.text


def test_a_decimal_does_not_split_a_sentence():
    assert len(split_sentences("The frequency is 51.50% in that group.")) == 1
