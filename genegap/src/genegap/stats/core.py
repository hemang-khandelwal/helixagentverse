"""Task 2.6: Pure statistical functions for GeneGap.

No network, no file I/O, and no imports from clients or agents.
Missing or invalid inputs return None rather than raising exceptions.
"""
from __future__ import annotations

import math
from typing import Union

import numpy as np
from scipy.stats import binom


def allele_frequency(ac: int | None, an: int | None) -> float | None:
    """Return allele frequency p = ac / an, or None if data is missing or an == 0."""
    if ac is None or an is None or an <= 0 or ac < 0 or ac > an:
        return None
    return float(ac / an)


def carrier_frequency(
    p: float | None,
    inheritance: str = "dominant",
) -> Union[float, dict[str, float], None]:
    """Return carrier frequency under Hardy-Weinberg equilibrium for the given inheritance mode."""
    if p is None or not isinstance(p, (int, float)) or p < 0.0 or p > 1.0:
        return None
    p_val = float(p)
    if inheritance == "dominant":
        return 1.0 - (1.0 - p_val) ** 2
    if inheritance == "recessive":
        return p_val ** 2
    if inheritance == "x_linked":
        # TODO: The medical student must confirm which figure the report should use —
        # the autosomal carrier formula does not transfer cleanly to X-linked genes.
        return {"xy": p_val, "xx_carrier": 1.0 - (1.0 - p_val) ** 2}
    return None


def beta_posterior_samples(
    ac: int | None,
    an: int | None,
    draws: int = 10000,
    seed: int = 0,
) -> np.ndarray | None:
    """Draw deterministic Monte Carlo samples of allele frequency p ~ Beta(ac + 1, an - ac + 1)."""
    if ac is None or an is None or an <= 0 or ac < 0 or ac > an or draws <= 0:
        return None
    rng = np.random.default_rng(seed)
    return rng.beta(ac + 1, an - ac + 1, size=draws)


def carrier_frequency_interval(
    ac: int | None,
    an: int | None,
    inheritance: str = "dominant",
    level: float = 0.95,
    draws: int = 10000,
    seed: int = 0,
) -> tuple[float, float, float] | None:
    """Return (low, point, high) credible interval for carrier frequency, or None if data is missing."""
    p = allele_frequency(ac, an)
    if p is None or level <= 0.0 or level >= 1.0:
        return None
    point = carrier_frequency(p, inheritance=inheritance)
    if point is None or isinstance(point, dict):
        return None
    p_samples = beta_posterior_samples(ac, an, draws=draws, seed=seed)
    if p_samples is None:
        return None
    if inheritance == "dominant":
        cf_samples = 1.0 - (1.0 - p_samples) ** 2
    elif inheritance == "recessive":
        cf_samples = p_samples ** 2
    else:
        return None
    alpha = (1.0 - level) / 2.0
    low, high = np.quantile(cf_samples, [alpha, 1.0 - alpha])
    return (float(low), float(point), float(high))


def expected_carriers(
    n_group: int | float | None,
    carrier_freq: float | None,
) -> float | None:
    """Return expected number of carriers in a trial group of size n_group."""
    if (
        n_group is None
        or carrier_freq is None
        or isinstance(carrier_freq, dict)
        or n_group < 0
        or carrier_freq < 0.0
        or carrier_freq > 1.0
    ):
        return None
    return float(n_group * carrier_freq)


def prob_fewer_than_k(
    n_group: int | None,
    carrier_freq: float | None,
    k: int | None,
) -> float | None:
    """Return P(X < k) = Binomial CDF at k - 1 for X ~ Binomial(n_group, carrier_freq)."""
    if (
        n_group is None
        or carrier_freq is None
        or k is None
        or isinstance(carrier_freq, dict)
        or n_group <= 0
        or carrier_freq < 0.0
        or carrier_freq > 1.0
    ):
        return None
    if k <= 0:
        return 0.0
    return float(binom.cdf(k - 1, int(n_group), float(carrier_freq)))


def required_n(
    target_carriers: int | float | None,
    carrier_freq: float | None,
) -> int | None:
    """Return minimum group size ceiling(target_carriers / carrier_freq) to reach target_carriers."""
    if (
        target_carriers is None
        or carrier_freq is None
        or isinstance(carrier_freq, dict)
        or target_carriers < 0
        or carrier_freq <= 0.0
        or carrier_freq > 1.0
    ):
        return None
    if target_carriers == 0:
        return 0
    return int(math.ceil(target_carriers / carrier_freq))


def enrolment_ratio(
    trial_share: float | None,
    target_share: float | None,
) -> float | None:
    """Return trial group share divided by target population share."""
    if (
        trial_share is None
        or target_share is None
        or trial_share < 0.0
        or target_share <= 0.0
    ):
        return None
    return float(trial_share / target_share)


def carrier_ratio(
    trial_carrier_share: float | None,
    target_carrier_share: float | None,
) -> float | None:
    """Return trial group's share of expected carriers divided by target population's carrier share."""
    if (
        trial_carrier_share is None
        or target_carrier_share is None
        or trial_carrier_share < 0.0
        or target_carrier_share <= 0.0
    ):
        return None
    return float(trial_carrier_share / target_carrier_share)


def insufficient_data(an: int | None, min_an: int = 2000) -> bool:
    """Return True when allele number an is missing or below min_an."""
    if an is None or an < min_an:
        return True
    return False
