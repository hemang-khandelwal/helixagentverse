import numpy as np
import pytest

from genegap.stats import (
    allele_frequency,
    beta_posterior_samples,
    carrier_frequency,
    carrier_frequency_interval,
    carrier_ratio,
    enrolment_ratio,
    expected_carriers,
    insufficient_data,
    prob_fewer_than_k,
    required_n,
)


def test_exact_hand_computable_cases():
    assert carrier_frequency(0.1, "dominant") == pytest.approx(0.19, abs=1e-12)
    assert carrier_frequency(0.5, "dominant") == pytest.approx(0.75, abs=1e-12)
    assert carrier_frequency(0.1, "recessive") == pytest.approx(0.01, abs=1e-12)

    x_res = carrier_frequency(0.1, "x_linked")
    assert x_res == {"xy": pytest.approx(0.1, abs=1e-12), "xx_carrier": pytest.approx(0.19, abs=1e-12)}

    # ac == an -> p=1.0, carrier=1.0
    p_full = allele_frequency(500, 500)
    assert p_full == 1.0
    assert carrier_frequency(p_full, "dominant") == 1.0
    assert carrier_frequency(p_full, "recessive") == 1.0


def test_an_zero_and_missing_inputs_return_none_without_exception():
    p = allele_frequency(0, 0)
    assert p is None
    assert carrier_frequency(p, "dominant") is None
    assert carrier_frequency(p, "recessive") is None
    assert carrier_frequency(p, "x_linked") is None
    assert beta_posterior_samples(0, 0) is None
    assert carrier_frequency_interval(0, 0) is None
    assert expected_carriers(100, None) is None
    assert prob_fewer_than_k(100, None, 10) is None
    assert prob_fewer_than_k(0, 0.2, 10) is None
    assert required_n(30, None) is None
    assert required_n(30, 0.0) is None
    assert enrolment_ratio(0.1, 0.0) is None
    assert enrolment_ratio(None, 0.2) is None
    assert carrier_ratio(0.1, 0.0) is None
    assert carrier_ratio(None, 0.2) is None
    assert insufficient_data(0) is True
    assert insufficient_data(None) is True


def test_real_gnomad_cyp2c19_star2_counts():
    # nfe: ac=162522, an=1111304 -> p ~ 0.146, carrier ~ 0.271
    p_nfe = allele_frequency(162522, 1111304)
    cf_nfe = carrier_frequency(p_nfe, "dominant")
    assert round(p_nfe, 3) == 0.146
    assert round(cf_nfe, 3) == 0.271
    assert insufficient_data(1111304, min_an=10000) is False

    # eas: ac=11167, an=36782 -> p ~ 0.304, carrier ~ 0.515
    p_eas = allele_frequency(11167, 36782)
    cf_eas = carrier_frequency(p_eas, "dominant")
    assert round(p_eas, 3) == 0.304
    assert round(cf_eas, 3) == 0.515
    assert insufficient_data(36782, min_an=10000) is False

    # mid: ac=611, an=5608 -> insufficient_data(5608, min_an=10000) is True
    p_mid = allele_frequency(611, 5608)
    cf_mid = carrier_frequency(p_mid, "dominant")
    assert round(p_mid, 3) == 0.109
    assert round(cf_mid, 3) == 0.206
    assert insufficient_data(5608, min_an=10000) is True
    assert insufficient_data(5608, min_an=2000) is False


def test_beta_posterior_samples_reproducibility():
    s1 = beta_posterior_samples(611, 5608, draws=10000, seed=42)
    s2 = beta_posterior_samples(611, 5608, draws=10000, seed=42)
    s3 = beta_posterior_samples(611, 5608, draws=10000, seed=99)
    assert isinstance(s1, np.ndarray)
    assert s1.shape == (10000,)
    assert np.array_equal(s1, s2)
    assert not np.array_equal(s1, s3)


def test_interval_sanity_and_width_scaling_with_an():
    mid_low, mid_point, mid_high = carrier_frequency_interval(611, 5608, inheritance="dominant", seed=0)
    nfe_low, nfe_point, nfe_high = carrier_frequency_interval(162522, 1111304, inheritance="dominant", seed=0)

    assert mid_low < mid_point < mid_high
    assert nfe_low < nfe_point < nfe_high

    mid_width = mid_high - mid_low
    nfe_width = nfe_high - nfe_low
    assert mid_width > nfe_width


def test_trial_gap_metrics_worked_example():
    # Worked example from project summary: p=0.30 -> carrier_freq=0.51;
    # Group A is 3% of 1000-person trial (n=30) vs 12% of target population
    cf = carrier_frequency(0.30, "dominant")
    assert cf == pytest.approx(0.51, abs=1e-12)
    assert expected_carriers(30, cf) == pytest.approx(15.3, abs=1e-12)
    assert enrolment_ratio(0.03, 0.12) == pytest.approx(0.25, abs=1e-12)
    assert carrier_ratio(0.03, 0.12) == pytest.approx(0.25, abs=1e-12)
    assert required_n(30, cf) == 59
    prob = prob_fewer_than_k(30, cf, 10)
    assert 0.0 < prob < 0.05
