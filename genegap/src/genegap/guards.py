"""Structural enforcement of the no-AI-arithmetic rule.

The critic (C1 in :mod:`genegap.critic`) catches a violation after a GapResult
exists. This module stops one earlier: at the point a value is about to become
an operand. Any code that takes a number out of a Fact and feeds it to a
statistical function passes it through :func:`computable` first, and a Fact that
an LLM produced raises instead of flowing into the maths.

No LLM calls, no network, no imports from clients or agents.
"""
from __future__ import annotations

from typing import Any, FrozenSet, get_args

from .schemas import Fact, Method

# The rule, in one paragraph, for the report (lane 3 pastes this verbatim).
RULE = (
    "No number in a GeneGap report is produced by a language model. Every figure is "
    "computed by deterministic Python from values retrieved from a database, curated by "
    "a named human, or derived from those by an earlier calculation. A language model may "
    "read a number out of prose, and GeneGap will display, quote and cite such a number, "
    "but it is never used as an input to a calculation. The code enforces this: a value "
    "whose provenance is LLM extraction raises an error if anything tries to compute with it."
)

# Methods whose values may be used as operands.
#   api       - retrieved directly from an external database (gnomAD, ClinicalTrials.gov, ...)
#   curated   - entered by a named human from guidelines or a source document
#   computed  - derived by our own deterministic functions from the above
COMPUTABLE_METHODS: FrozenSet[str] = frozenset({"api", "curated", "computed"})

# Methods whose values may be displayed, quoted and cited, but never computed with.
#   llm_extracted - a language model read this out of unstructured text
NON_COMPUTABLE_METHODS: FrozenSet[str] = frozenset({"llm_extracted"})


class AIArithmeticError(Exception):
    """Raised when a value a language model extracted is used as an operand."""


def computable(fact: Fact) -> Any:
    """Return ``fact.value`` if its provenance permits arithmetic; otherwise raise.

    Call this at the point a Fact's value enters a calculation::

        p = allele_frequency(computable(ac_fact), computable(an_fact))

    Raises:
        AIArithmeticError: if the fact's method is LLM extraction.
        ValueError: if the fact's method is unknown to this guard, so that adding a
            provenance pathway to ``Method`` forces an explicit decision here.
    """
    method = fact.method

    if method in NON_COMPUTABLE_METHODS:
        raise AIArithmeticError(
            f"Fact '{fact.id}' (field={fact.field!r}, source={fact.source!r}, "
            f"record_id={fact.record_id!r}) has method={method!r} and may not be used in a "
            f"calculation. A language model extracted this value; it may be displayed, quoted "
            f"and cited, but every operand must come from an API, a curated entry or a prior "
            f"deterministic computation."
        )

    if method not in COMPUTABLE_METHODS:
        raise ValueError(
            f"Fact '{fact.id}' (field={fact.field!r}) has method={method!r}, which "
            f"genegap.guards does not classify. Add it to COMPUTABLE_METHODS or "
            f"NON_COMPUTABLE_METHODS and say why in the report."
        )

    return fact.value


def is_computable(fact: Fact) -> bool:
    """True when ``fact``'s value may be used as an operand. Does not raise."""
    return fact.method in COMPUTABLE_METHODS


def declared_methods() -> FrozenSet[str]:
    """Every value of ``Method``, as declared in schemas.py."""
    return frozenset(get_args(Method))
